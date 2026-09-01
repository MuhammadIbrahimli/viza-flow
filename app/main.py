#!/usr/bin/env python3
"""VizaFlow FastAPI service: uploads → Gemini → 2 PDF + 1 TXT."""

from __future__ import annotations

import base64
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

from app.config import ensure_runtime_dirs, get_settings, load_env_file
from app.services.documents import generate_pack, pack_to_zip, wipe_pack
from app.services.gemini import extract_applicant, rasterize_uploads
from app.services.pipeline import (
    TripDetails,
    clear_applicant,
    default_trip,
    log,
    specimen_applicant,
)
from app.services.rate_limit import GenerationRateLimitMiddleware
from app.services.schengen_template import ensure_schengen_template
from app.services.telegram import notify_generation

load_env_file()
SETTINGS = get_settings()
STARTED_AT = time.monotonic()
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf"}
CORS_ALLOW_HEADERS = (
    "Accept, Accept-Language, Authorization, Content-Type, Origin, "
    "X-Requested-With, X-CSRF-Token"
)
CORS_ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD"


class AllowTunnelCORS(BaseHTTPMiddleware):
    """Echo Origin so localhost:3000 → trycloudflare.com never fails preflight."""

    async def dispatch(self, request: Request, call_next):
        origin = (request.headers.get("origin") or "").strip() or "*"
        if request.method == "OPTIONS":
            return StarletteResponse(status_code=204, headers=_cors_headers(origin))
        response = await call_next(request)
        for key, value in _cors_headers(origin).items():
            response.headers[key] = value
        return response


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": CORS_ALLOW_METHODS,
        "Access-Control-Allow-Headers": CORS_ALLOW_HEADERS,
        "Access-Control-Expose-Headers": "Content-Disposition, Content-Type, Content-Length",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_runtime_dirs()
    ensure_schengen_template()
    log("VizaFlow API: zero-retention mode (uploads stay in RAM / process temp)")
    yield


app = FastAPI(title="VizaFlow", docs_url="/docs", redoc_url=None, lifespan=lifespan)
app.add_middleware(GenerationRateLimitMiddleware)
app.add_middleware(AllowTunnelCORS)


def _whatsapp_e164() -> str:
    raw = SETTINGS.whatsapp_e164
    return "".join(ch for ch in raw if ch.isdigit())


def _trip_from_form(arrival: str, departure: str, hotel: str) -> TripDetails:
    trip = TripDetails(
        arrival_date=(arrival or "").strip(),
        departure_date=(departure or "").strip(),
        hotel=(hotel or "").strip(),
        destination="ITALY",
    )
    if not trip.is_complete():
        fallback = default_trip()
        ready = trip.normalized()
        return TripDetails(
            arrival_date=ready.arrival_date or fallback.arrival_date,
            departure_date=ready.departure_date or fallback.departure_date,
            hotel=ready.hotel or fallback.hotel,
            destination="ITALY",
        )
    return trip.normalized()


def _normalize_ref(raw: str) -> str:
    cleaned = "".join(ch for ch in (raw or "").strip() if ch.isprintable())
    if not cleaned or cleaned.lower() in {"direct", "whatsapp", "wa"}:
        return "whatsapp"
    return cleaned[:128]


def _queue_notify(background_tasks: BackgroundTasks, ref: str) -> None:
    background_tasks.add_task(notify_generation, ref=_normalize_ref(ref))


def _wipe_uploads(uploads: list[tuple[str, bytearray]]) -> None:
    for _name, payload in uploads:
        for index in range(len(payload)):
            payload[index] = 0
        payload.clear()
    uploads.clear()


def _encode_pack(pack: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    files: dict[str, dict[str, str]] = {}
    for key, item in pack.items():
        content = item.get("content") or b""
        if not isinstance(content, (bytes, bytearray)):
            content = b""
        files[key] = {
            "filename": str(item.get("filename") or f"{key}.bin"),
            "media_type": str(item.get("media_type") or "application/octet-stream"),
            "base64": base64.b64encode(bytes(content)).decode("ascii"),
        }
        if isinstance(item.get("content"), (bytearray, bytes)):
            item["content"] = b""
    pack.clear()
    return files


def _document_kinds(applicant) -> list[str]:
    kinds: list[str] = []
    if applicant.has_passport:
        kinds.append("passport")
    if applicant.has_id_card:
        kinds.append("id_card")
    if applicant.has_employment_certificate:
        kinds.append("employment")
    return kinds


def _zip_response(pack: dict[str, dict[str, Any]], last_name: str) -> Response:
    filename, payload = pack_to_zip(pack, last_name)
    wipe_pack(pack)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _json_pack_payload(applicant, pack: dict[str, dict[str, Any]], *, specimen: bool) -> dict[str, Any]:
    fields = applicant.filled_names()
    kinds = _document_kinds(applicant)
    files = _encode_pack(pack)
    return {
        "ok": True,
        "specimen": specimen,
        "fields_filled": fields,
        "document_types": kinds,
        "files": files,
    }


def _build_specimen_pack(arrival: str, departure: str, hotel: str, as_zip: bool, ref: str = ""):
    applicant = specimen_applicant()
    trip = _trip_from_form(arrival, departure, hotel)
    try:
        log("API demo: specimen pack (no scans)")
        pack = generate_pack(applicant, trip, specimen=True)
        if as_zip:
            return _zip_response(pack, applicant.last_name), ref
        return _json_pack_payload(applicant, pack, specimen=True), ref
    finally:
        clear_applicant(applicant)


def _build_upload_pack(
    uploads: list[tuple[str, bytes]],
    arrival: str,
    departure: str,
    hotel: str,
    as_zip: bool,
    extra_text: str = "",
    ref: str = "",
):
    frames: list[bytes] = []
    applicant = None
    try:
        frames = rasterize_uploads(uploads)
        if not frames:
            raise HTTPException(status_code=400, detail="Şəkil oxunmadı. Daha aydın foto yükləyin.")
        applicant = extract_applicant(frames, extra_text)
        extra_text = ""
        frames.clear()
        if not applicant.filled_names():
            raise HTTPException(
                status_code=422,
                detail="Məlumat oxunmadı. Pasport və ya şəxsiyyət vəsiqəsinin şəklini yükləyin.",
            )
        trip = _trip_from_form(arrival, departure, hotel)
        pack = generate_pack(applicant, trip, specimen=False)
        log(f"API process: filled {len(applicant.filled_names())} field(s)")
        if as_zip:
            return _zip_response(pack, applicant.last_name), ref
        return _json_pack_payload(applicant, pack, specimen=False), ref
    finally:
        frames.clear()
        if applicant is not None:
            clear_applicant(applicant)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "VizaFlow", "ping": "/ping"}


@app.get("/ping")
def ping() -> dict[str, object]:
    return {
        "status": "ok",
        "uptime_seconds": round(time.monotonic() - STARTED_AT, 3),
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/defaults")
def defaults() -> dict[str, object]:
    trip = default_trip()
    return {
        "arrival_date": trip.arrival_date,
        "departure_date": trip.departure_date,
        "hotel": trip.hotel,
        "destination": trip.destination,
        "whatsapp": _whatsapp_e164(),
        "specimen_given": "LEYLA",
        "specimen_surname": "MAMMADOVA",
    }


@app.post("/api/demo")
async def demo_pack(
    background_tasks: BackgroundTasks,
    arrival_date: Annotated[str, Form()] = "",
    departure_date: Annotated[str, Form()] = "",
    hotel: Annotated[str, Form()] = "",
    response_format: Annotated[str, Form()] = "json",
    ref: Annotated[str, Form()] = "",
):
    as_zip = (response_format or "json").strip().lower() == "zip"
    try:
        result, notify_ref = await run_in_threadpool(
            _build_specimen_pack,
            arrival_date,
            departure_date,
            hotel,
            as_zip,
            ref,
        )
        _queue_notify(background_tasks, notify_ref)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        log(f"API demo ERROR: {type(exc).__name__}")
        raise HTTPException(status_code=500, detail="Nümunə sənədlər hazırlanmadı. Yenidən cəhd edin.") from exc


@app.post("/api/process")
async def process_pack(
    background_tasks: BackgroundTasks,
    files: Annotated[list[UploadFile], File()],
    arrival_date: Annotated[str, Form()] = "",
    departure_date: Annotated[str, Form()] = "",
    hotel: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    response_format: Annotated[str, Form()] = "json",
    ref: Annotated[str, Form()] = "",
):
    if not files:
        raise HTTPException(status_code=400, detail="Heç bir fayl yüklənməyib.")
    if len(files) > SETTINGS.max_files:
        raise HTTPException(status_code=400, detail=f"Maksimum {SETTINGS.max_files} fayl.")

    uploads: list[tuple[str, bytearray]] = []
    as_zip = (response_format or "json").strip().lower() == "zip"
    try:
        for upload in files:
            name = upload.filename or "scan.jpg"
            suffix = Path(name).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise HTTPException(
                    status_code=400,
                    detail="Yalnız JPG, PNG və PDF qəbul olunur.",
                )
            payload = bytearray(await upload.read())
            if len(payload) > SETTINGS.max_file_bytes:
                payload.clear()
                raise HTTPException(status_code=400, detail="Fayl 12 MB-dan böyükdür.")
            if not payload:
                continue
            uploads.append((name, payload))

        if not uploads:
            raise HTTPException(status_code=400, detail="Fayl boşdur. Başqa şəkil seçin.")

        log(f"API process: {len(uploads)} file(s), RAM only")
        frozen = [(name, bytes(blob)) for name, blob in uploads]
        _wipe_uploads(uploads)
        result, notify_ref = await run_in_threadpool(
            _build_upload_pack,
            frozen,
            arrival_date,
            departure_date,
            hotel,
            as_zip,
            notes,
            ref,
        )
        notes = ""
        for _name, blob in frozen:
            del blob
        frozen.clear()
        _queue_notify(background_tasks, notify_ref)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        log(f"API process ERROR: {type(exc).__name__}")
        raise HTTPException(status_code=500, detail="Sənədlər hazırlanmadı. Yenidən cəhd edin.") from exc
    finally:
        _wipe_uploads(uploads)


@app.post("/api/pack")
async def process_pack_zip(
    background_tasks: BackgroundTasks,
    files: Annotated[list[UploadFile], File()],
    arrival_date: Annotated[str, Form()] = "",
    departure_date: Annotated[str, Form()] = "",
    hotel: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    ref: Annotated[str, Form()] = "",
):
    """Accept files and/or extra text notes; return ZIP with 2 PDF + 1 TXT."""
    return await process_pack(
        background_tasks=background_tasks,
        files=files,
        arrival_date=arrival_date,
        departure_date=departure_date,
        hotel=hotel,
        notes=notes,
        response_format="zip",
        ref=ref,
    )
