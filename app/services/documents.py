"""Generate Schengen PDF, cover letter PDF, and checklist TXT (optionally as a ZIP)."""

from __future__ import annotations

import io
import zipfile
from typing import Any

from app.services.pipeline import (
    ApplicantFields,
    TripDetails,
    lastname_token,
    log,
    visa_pack_bytes,
)


def generate_pack(
    applicant: ApplicantFields,
    trip: TripDetails | None = None,
    *,
    specimen: bool = False,
    country: str = "italy",
) -> dict[str, dict[str, Any]]:
    """Build 2 PDF + 1 TXT in a process-scoped temp dir and return file bytes."""
    log("Documents: generating visa pack")
    return visa_pack_bytes(applicant, country, trip, specimen=specimen)


def pack_to_zip(pack: dict[str, dict[str, Any]], last_name: str = "") -> tuple[str, bytes]:
    """Archive form PDF, cover letter PDF, and checklist TXT into one ZIP."""
    token = lastname_token(last_name)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in pack.values():
            name = str(item.get("filename") or "document.bin")
            content = item.get("content") or b""
            if not isinstance(content, (bytes, bytearray)):
                content = b""
            archive.writestr(name, bytes(content))
    return f"visa_pack_{token}.zip", buffer.getvalue()


def wipe_pack(pack: dict[str, dict[str, Any]]) -> None:
    for item in pack.values():
        item["content"] = b""
    pack.clear()
