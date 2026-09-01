#!/usr/bin/env python3
"""Extract applicant data from scans and fill visa documents (Italy Schengen / USA DS-160)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

from pdf2image import convert_from_path
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText
from pypdf.generic import BooleanObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from app.config import (  # noqa: E402
    DS160_TEMPLATE,
    INPUT_DIR,
    OUTPUT_DIR,
    ROOT,
    SCHENGEN_TEMPLATE,
    TEMP_DIR,
    TEMPLATES_DIR,
    load_env_file,
)
from app.services.overlay import ensure_interactive_form, list_pdf_fields  # noqa: E402
from app.services.schengen_template import ensure_schengen_template, write_text_pdf  # noqa: E402

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
PDF_SUFFIX = ".pdf"
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | {PDF_SUFFIX}

VALID_COUNTRIES = {"italy", "schengen", "usa", "us"}
ITALY_ALIASES = {"italy", "schengen"}
USA_ALIASES = {"usa", "us"}

EXTRACT_PROMPT = """You are extracting data for a visa application form used by agencies in Baku.
Read ALL provided images. Classify each document, then extract fields.
Document types (set true only if that document is actually in the images):
- passport: international travel passport biodata page (title PASSPORT/PASPORT, MRZ line starts with P).
- id_card: Azerbaijan national identity card - Şəxsiyyət vəsiqəsi / IDENTITY CARD
  (plastic card, chip, bilingual AZ/EN labels SOYADI/SURNAME, ADI/GIVEN NAME,
  VƏSİQƏNİN NÖMRƏSİ/CARD NO, FƏRDİ İDENTİFİKASİYA NÖMRƏSİ/PERSONAL NO; MRZ often starts with I or IA).
- employment_certificate: employment letter, salary certificate, справка с работы / iş yeri arayışı.
NEVER treat an ID card as a passport. NEVER copy an ID card number into passport_no.
Return a single JSON object with exactly these keys:
{
  "has_passport": false,
  "has_id_card": false,
  "has_employment_certificate": false,
  "first_name": "",
  "last_name": "",
  "date_of_birth": "",
  "passport_no": "",
  "expiry_date": "",
  "fin_code": "",
  "id_series": "",
  "id_number": "",
  "id_expiry_date": "",
  "address": "",
  "phone": "",
  "employer_name": "",
  "job_title": "",
  "monthly_income": ""
}
Rules:
- Names: ICAO Latin (Doc 9303), no diacritics. Prefer MRZ spelling. Given name -> first_name, surname -> last_name.
- Dates: DD/MM/YYYY only.
- passport_no and expiry_date: ONLY from a passport. Empty strings if no passport is present.
- id_card CARD NO such as AA3038280 -> id_series "AA", id_number "3038280" (series = leading letters, number = the rest).
- id_expiry_date: ID card validity / ETİBARLILIQ MÜDDƏTİ if shown.
- fin_code: Azerbaijan FİN / PERSONAL NO. Exactly 7 alphanumeric characters. Also present at the end of ID MRZ line 1.
- address: registered address / qeydiyyat / propiska / living address if printed (often ID reverse). Empty if not printed.
- employer_name: organisation name in English (or ICAO Latin). Empty if no employment certificate.
- job_title: English job title. Empty if no employment certificate.
- monthly_income: digits only, no currency symbol. Empty if no employment certificate.
- phone: include country code if visible.
- Use empty string / false when not found. Do not invent values.
- JSON only, no markdown.
"""

PDF_FIELD_MAP = {
    "First_Name": "first_name",
    "Last_Name": "last_name",
    "DOB": "date_of_birth",
    "Place_Of_Birth": "",
    "Country_Of_Birth": "",
    "Nationality": "",
    "Nationality_At_Birth": "",
    "Other_Nationalities": "",
    "FIN_Code": "fin_code",
    "Passport_No": "passport_no",
    "Issue_Date": "",
    "Expiry_Date": "expiry_date",
    "Issuing_Country": "",
    "Address": "address",
    "Phone": "phone",
    "Job_Title": "job_title",
    "Employer_Name": "employer_name",
    "Monthly_Income": "monthly_income",
    "Main_Destination": "",
    "First_Entry": "",
    "Place_And_Date": "",
    "Birth_Surname": "",
    "Travel_Purpose_Other": "",
    "Arrival_Date": "",
    "Departure_Date": "",
}

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "first_name": (
        "first_name",
        "firstname",
        "first name",
        "given_name",
        "givenname",
        "given name",
        "name",
        "ad",
        "ad soyad",
    ),
    "last_name": (
        "last_name",
        "lastname",
        "last name",
        "surname",
        "family_name",
        "familyname",
        "family name",
        "soyad",
        "soyadi",
    ),
    "date_of_birth": (
        "date_of_birth",
        "dateofbirth",
        "date of birth",
        "dob",
        "birth_date",
        "birthdate",
        "birth date",
        "dogum",
        "dogum tarihi",
    ),
    "passport_no": (
        "passport_no",
        "passportno",
        "passport no",
        "passport_number",
        "passportnumber",
        "passport number",
        "passport",
        "pasport",
    ),
    "expiry_date": (
        "expiry_date",
        "expirydate",
        "expiry date",
        "expire_date",
        "expiration",
        "expiration_date",
        "passport_expiry",
        "valid_until",
        "validuntil",
        "bitme",
    ),
    "fin_code": (
        "fin_code",
        "fincode",
        "fin code",
        "fin",
        "personal_no",
        "personalno",
        "personal number",
        "id_code",
        "idcode",
        "ferdi identifikasiya",
    ),
    "id_series": (
        "id_series",
        "idseries",
        "card_series",
        "vesiqe_seriya",
    ),
    "id_number": (
        "id_number",
        "idnumber",
        "id no",
        "card_no",
        "card number",
        "vesiqenin nomresi",
        "document_no",
        "documentno",
    ),
    "address": (
        "address",
        "residence",
        "registered_address",
        "propiska",
        "qeydiyyat",
        "unvan",
        "home_address",
    ),
    "phone": (
        "phone",
        "telephone",
        "tel",
        "mobile",
        "cell",
        "phone_number",
        "phonenumber",
        "telefon",
    ),
    "employer_name": (
        "employer_name",
        "employername",
        "employer name",
        "employer",
        "company",
        "organisation",
        "organization",
        "ish yeri",
        "is yeri",
        "workplace",
    ),
    "job_title": (
        "job_title",
        "jobtitle",
        "job title",
        "occupation",
        "position",
        "profession",
        "employment",
        "vezife",
        "is",
    ),
    "monthly_income": (
        "monthly_income",
        "monthlyincome",
        "monthly income",
        "income",
        "salary",
        "wage",
        "maas",
        "ayliq",
    ),
}

CYRILLIC_ICAO = {
    "А": "A",
    "Б": "B",
    "В": "V",
    "Г": "G",
    "Д": "D",
    "Е": "E",
    "Ё": "E",
    "Ж": "ZH",
    "З": "Z",
    "И": "I",
    "Й": "I",
    "К": "K",
    "Л": "L",
    "М": "M",
    "Н": "N",
    "О": "O",
    "П": "P",
    "Р": "R",
    "С": "S",
    "Т": "T",
    "У": "U",
    "Ф": "F",
    "Х": "KH",
    "Ц": "TS",
    "Ч": "CH",
    "Ш": "SH",
    "Щ": "SHCH",
    "Ъ": "",
    "Ы": "Y",
    "Ь": "",
    "Э": "E",
    "Ю": "IU",
    "Я": "IA",
}

LATIN_DIACRITIC_ICAO = str.maketrans(
    {
        "ə": "e",
        "Ə": "e",
        "ç": "c",
        "Ç": "c",
        "ğ": "g",
        "Ğ": "g",
        "ı": "i",
        "İ": "i",
        "ö": "o",
        "Ö": "o",
        "ş": "s",
        "Ş": "s",
        "ü": "u",
        "Ü": "u",
        "á": "a",
        "à": "a",
        "ä": "a",
        "â": "a",
        "é": "e",
        "è": "e",
        "ê": "e",
        "ë": "e",
        "í": "i",
        "ì": "i",
        "î": "i",
        "ï": "i",
        "ó": "o",
        "ò": "o",
        "ô": "o",
        "ú": "u",
        "ù": "u",
        "û": "u",
        "ñ": "n",
        "ý": "y",
    }
)


PENDING_PASSPORT = "[PENDING_PASSPORT]"
PENDING_EMPLOYMENT = "[Employment Certificate: Pending/Not Provided]"
SPECIMEN_NOTICE = "Nümunə rejimi: Test üçün hazırlanmışdır"

ASCII_PUNCT = (
    ("\u2014", "-"),
    ("\u2013", "-"),
    ("\u2012", "-"),
    ("\u2010", "-"),
    ("\u2011", "-"),
    ("\u2212", "-"),
    ("\u2018", "'"),
    ("\u2019", "'"),
    ("\u201c", '"'),
    ("\u201d", '"'),
    ("\u00ab", '"'),
    ("\u00bb", '"'),
    ("\u00a0", " "),
    ("\u2026", "..."),
)


@dataclass
class ApplicantFields:
    first_name: str = ""
    last_name: str = ""
    date_of_birth: str = ""
    passport_no: str = ""
    expiry_date: str = ""
    fin_code: str = ""
    id_series: str = ""
    id_number: str = ""
    id_expiry_date: str = ""
    address: str = ""
    phone: str = ""
    employer_name: str = ""
    job_title: str = ""
    monthly_income: str = ""
    has_passport: bool = False
    has_id_card: bool = False
    has_employment_certificate: bool = False
    passport_pending: bool = False

    def filled_names(self) -> list[str]:
        names: list[str] = []
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool):
                continue
            if str(value or "").strip():
                names.append(item.name)
        return names

    def has_employment_data(self) -> bool:
        return bool(
            self.has_employment_certificate
            or self.employer_name
            or self.job_title
            or self.monthly_income
        )

    def id_document_no(self) -> str:
        return f"{self.id_series}{self.id_number}".strip()


@dataclass
class TripDetails:
    arrival_date: str = ""
    departure_date: str = ""
    hotel: str = ""
    destination: str = "ITALY"

    def normalized(self) -> TripDetails:
        dest = transliterate_icao(self.destination).upper() or "ITALY"
        hotel = transliterate_icao(self.hotel).upper()
        return TripDetails(
            arrival_date=normalize_date(self.arrival_date),
            departure_date=normalize_date(self.departure_date),
            hotel=hotel,
            destination=dest,
        )

    def is_complete(self) -> bool:
        trip = self.normalized()
        return bool(trip.arrival_date and trip.departure_date and trip.hotel)


def default_trip() -> TripDetails:
    start = date.today() + timedelta(days=14)
    end = start + timedelta(days=7)
    return TripDetails(
        arrival_date=start.strftime("%d/%m/%Y"),
        departure_date=end.strftime("%d/%m/%Y"),
        hotel="Hotel Artemide, Rome",
        destination="ITALY",
    )


def specimen_applicant() -> ApplicantFields:
    """Fictional Baku applicant for the public demo. Not a real person."""
    return ApplicantFields(
        first_name="LEYLA",
        last_name="MAMMADOVA",
        date_of_birth="14/03/1994",
        passport_no="C00411007",
        expiry_date="12/08/2031",
        fin_code="1A2B3C4",
        id_series="AA",
        id_number="1000001",
        id_expiry_date="14/03/2034",
        address="NASIMI DISTRICT, BAKU",
        phone="+994501110000",
        employer_name="BAKU TRAVEL DESK LLC",
        job_title="Operations Manager",
        monthly_income="1800",
        has_passport=True,
        has_id_card=True,
        has_employment_certificate=True,
        passport_pending=False,
    )


def _configure_stdio() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")


def _commit_output(tmp_path: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp_path.replace(dest)
        return dest
    except PermissionError:
        alt = dest.with_name(f"{dest.stem}_new{dest.suffix}")
        tmp_path.replace(alt)
        log(f"WARNING: {dest.name} занят другой программой, записано: {alt.name}")
        return alt


def log(message: str) -> None:
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write((message + "\n").encode(encoding, "replace"))
        sys.stdout.flush()


def lastname_token(last_name: str) -> str:
    token = re.sub(r"[^A-Z0-9]+", "", (last_name or "").upper())
    return token or "UNKNOWN"


def resolve_country(raw: str) -> str:
    key = (raw or "italy").strip().lower()
    if key in ITALY_ALIASES:
        return "italy"
    if key in USA_ALIASES:
        return "usa"
    allowed = ", ".join(sorted(VALID_COUNTRIES))
    raise RuntimeError(f"Неизвестная страна {raw!r}. Допустимо: {allowed}.")


def collect_input_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return [
        path
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    ]


def _pil_to_jpeg_bytes(image: Image.Image) -> bytes:
    rgb = image.convert("RGB")
    out = BytesIO()
    rgb.save(out, format="JPEG", quality=90)
    return out.getvalue()


def documents_to_jpeg_bytes(paths: list[Path]) -> list[bytes]:
    """Rasterize scans/PDFs in a temp dir (deleted on return)."""
    frames: list[bytes] = []
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="visa-fill-", dir=str(TEMP_DIR)) as tmp:
        tmp_dir = Path(tmp)
        for path in paths:
            suffix = path.suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                with Image.open(path) as image:
                    frames.append(_pil_to_jpeg_bytes(image))
            elif suffix == PDF_SUFFIX:
                pages = convert_from_path(str(path), output_folder=str(tmp_dir), fmt="jpeg")
                for page in pages:
                    frames.append(_pil_to_jpeg_bytes(page))
                    page.close()
    return frames


def transliterate_icao(value: str) -> str:
    if not value:
        return ""
    chars: list[str] = []
    for char in value:
        upper = char.upper()
        if upper in CYRILLIC_ICAO:
            chars.append(CYRILLIC_ICAO[upper])
        else:
            chars.append(char)
    mapped = "".join(chars).translate(LATIN_DIACRITIC_ICAO)
    mapped = mapped.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", mapped).strip()


def normalize_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    match = re.search(r"(\d{1,4})[./\- ](\d{1,2})[./\- ](\d{1,4})", raw)
    if not match:
        return raw
    a, b, c = match.group(1), match.group(2), match.group(3)
    if len(a) == 4:
        year, month, day = a, b, c
    else:
        day, month, year = a, b, c
    try:
        d, m, y = int(day), int(month), int(year)
        if y < 100:
            y += 2000 if y < 50 else 1900
        return f"{d:02d}/{m:02d}/{y:04d}"
    except ValueError:
        return raw


def ascii_punct(text: str) -> str:
    mapped = text or ""
    for src, dst in ASCII_PUNCT:
        mapped = mapped.replace(src, dst)
    return mapped


def specimen_notice_pdf() -> str:
    """Helvetica-safe watermark for demo PDFs (Azerbaijani diacritics mapped)."""
    return ascii_punct(transliterate_icao(SPECIMEN_NOTICE))


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "present"}


def _as_text(value: object) -> str:
    if isinstance(value, bool):
        return ""
    return str(value or "").strip()


def normalize_fin(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", transliterate_icao(value)).upper()
    if not cleaned:
        return ""
    if len(cleaned) == 7:
        return cleaned
    match = re.search(r"[A-Z0-9]{7}", cleaned)
    return match.group(0) if match else cleaned


def looks_like_az_id_number(value: str) -> bool:
    """Azerbaijan ID card number: two Latin letters + seven digits (CARD NO)."""
    return bool(re.fullmatch(r"[A-Z]{2}\d{7}", value or ""))


def split_id_card_number(series: str, number: str, fallback: str = "") -> tuple[str, str]:
    series_clean = re.sub(r"[^A-Z]", "", transliterate_icao(series)).upper()
    number_clean = re.sub(r"[^A-Z0-9]", "", transliterate_icao(number)).upper()
    combined = re.sub(r"[^A-Z0-9]", "", transliterate_icao(fallback)).upper()
    if not series_clean and not number_clean and combined:
        match = re.match(r"^([A-Z]{1,3})(\d{5,})$", combined)
        if match:
            return match.group(1), match.group(2)
        match = re.match(r"^([A-Z]+)(.+)$", combined)
        if match and match.group(2):
            return match.group(1), match.group(2)
        return "", combined
    if not series_clean and number_clean:
        match = re.match(r"^([A-Z]{1,3})(\d{5,})$", number_clean)
        if match:
            return match.group(1), match.group(2)
    return series_clean, number_clean


def normalize_fields(raw: dict) -> ApplicantFields:
    has_passport = _as_bool(raw.get("has_passport"))
    has_id_card = _as_bool(raw.get("has_id_card"))
    has_employment = _as_bool(raw.get("has_employment_certificate"))
    passport_no = re.sub(r"\s+", "", transliterate_icao(_as_text(raw.get("passport_no")))).upper()
    id_series, id_number = split_id_card_number(
        _as_text(raw.get("id_series")),
        _as_text(raw.get("id_number")),
        passport_no if not has_passport else "",
    )
    combined_id = f"{id_series}{id_number}".strip()
    if looks_like_az_id_number(passport_no):
        has_id_card = True
        if not combined_id:
            id_series, id_number = split_id_card_number("", "", passport_no)
            combined_id = f"{id_series}{id_number}".strip()
        has_passport = False
        passport_no = ""
    elif has_id_card and not has_passport:
        passport_no = ""
    passport_pending = not has_passport or not passport_no
    if passport_pending:
        passport_no = combined_id or PENDING_PASSPORT

    employer = transliterate_icao(_as_text(raw.get("employer_name"))).upper()
    job_title = transliterate_icao(_as_text(raw.get("job_title"))).title()
    income = re.sub(r"[^\d.]", "", _as_text(raw.get("monthly_income")))
    if not has_employment and not (employer or job_title or income):
        employer, job_title, income = "", "", ""

    return ApplicantFields(
        first_name=transliterate_icao(_as_text(raw.get("first_name"))).upper(),
        last_name=transliterate_icao(_as_text(raw.get("last_name"))).upper(),
        date_of_birth=normalize_date(_as_text(raw.get("date_of_birth"))),
        passport_no=passport_no,
        expiry_date=normalize_date(_as_text(raw.get("expiry_date"))),
        fin_code=normalize_fin(_as_text(raw.get("fin_code"))),
        id_series=id_series,
        id_number=id_number,
        id_expiry_date=normalize_date(_as_text(raw.get("id_expiry_date"))),
        address=transliterate_icao(_as_text(raw.get("address"))).upper(),
        phone=re.sub(r"[^\d+]", "", _as_text(raw.get("phone"))),
        employer_name=employer,
        job_title=job_title,
        monthly_income=income,
        has_passport=has_passport,
        has_id_card=has_id_card or bool(id_series or id_number),
        has_employment_certificate=has_employment or bool(employer or job_title or income),
        passport_pending=passport_pending,
    )


# Free-tier Flash IDs for vision + JSON (Google AI Studio, as of Aug 2026).
# gemini-2.0-flash was shut down 2026-06-01 — do not use it.
# Override with GEMINI_MODEL=... in .env if needed.
FREE_VISION_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
)

GEMINI_REST_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_SECRET_IN_TEXT = re.compile(
    r"(key=)[^&\s]+|AIza[0-9A-Za-z_\-]{10,}|AQ\.[0-9A-Za-z_\-]{10,}",
    re.IGNORECASE,
)


def redact_secrets(text: str, api_key: str = "") -> str:
    cleaned = _SECRET_IN_TEXT.sub(lambda match: (match.group(1) or "") + "REDACTED", str(text))
    if api_key:
        cleaned = cleaned.replace(api_key, "REDACTED")
    return cleaned


def model_candidates() -> list[str]:
    preferred = (os.environ.get("GEMINI_MODEL") or "").strip()
    ordered: list[str] = []
    if preferred:
        ordered.append(preferred)
    for name in FREE_VISION_MODELS:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _is_missing_model(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("404", "not found", "not_found", "is not found"))


def _is_fatal_auth(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in ("401", "unauthenticated", "invalid api key", "api key not valid", "api_key_invalid")
    )


def _parse_extract_payload(text: str) -> dict:
    payload = json.loads(text or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("Модель вернула неожиданный формат. Поля не извлечены.")
    docs = payload.get("documents_present")
    if not isinstance(docs, dict):
        docs = {}
    parsed: dict = {}
    for item in fields(ApplicantFields):
        if item.name in {"has_passport", "has_id_card", "has_employment_certificate", "passport_pending"}:
            continue
        parsed[item.name] = _as_text(payload.get(item.name, ""))
    parsed["has_passport"] = _as_bool(payload.get("has_passport", docs.get("passport")))
    parsed["has_id_card"] = _as_bool(payload.get("has_id_card", docs.get("id_card")))
    parsed["has_employment_certificate"] = _as_bool(
        payload.get("has_employment_certificate", docs.get("employment_certificate"))
    )
    return parsed


def extract_fields_genai(images: list[bytes], api_key: str, model: str, extra_text: str = "") -> dict[str, str]:
    from google import genai
    from google.genai import types

    parts: list[object] = [
        types.Part.from_bytes(data=frame, mime_type="image/jpeg") for frame in images
    ]
    parts.append(EXTRACT_PROMPT)
    note = (extra_text or "").strip()
    if note:
        parts.append("Additional operator notes (do not invent fields from this if scans contradict it):\n" + note)
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0,
        ),
    )
    return _parse_extract_payload(response.text or "{}")


def extract_fields_requests(images: list[bytes], api_key: str, model: str, extra_text: str = "") -> dict[str, str]:
    import base64

    import requests

    parts = [
        {
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(frame).decode("ascii"),
            }
        }
        for frame in images
    ]
    parts.append({"text": EXTRACT_PROMPT})
    note = (extra_text or "").strip()
    if note:
        parts.append(
            {
                "text": "Additional operator notes (do not invent fields from this if scans contradict it):\n"
                + note
            }
        )
    url = f"{GEMINI_REST_BASE}/{model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }
    response = requests.post(
        url,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=body,
        timeout=120,
    )
    if not response.ok:
        detail = redact_secrets(response.text[:400], api_key)
        raise RuntimeError(f"Gemini REST {response.status_code} for {model}: {detail}")
    data = response.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("REST-ответ Gemini не содержит текста.") from exc
    return _parse_extract_payload(text)


def discover_flash_models(api_key: str) -> list[str]:
    import requests

    response = requests.get(
        GEMINI_REST_BASE,
        headers={"x-goog-api-key": api_key},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"Не удалось получить список моделей ({response.status_code}). "
            + redact_secrets(response.text[:240], api_key)
        )
    found: list[str] = []
    for item in response.json().get("models") or []:
        name = str(item.get("name") or "").replace("models/", "")
        methods = item.get("supportedGenerationMethods") or []
        lowered = name.lower()
        if "generateContent" not in methods:
            continue
        if "flash" not in lowered:
            continue
        if any(skip in lowered for skip in ("image", "tts", "live", "audio")):
            continue
        found.append(name)
    return found


def extract_with_models(
    images: list[bytes],
    api_key: str,
    models: list[str],
    extra_text: str = "",
) -> dict[str, str]:
    last_error = "модели не найдены"
    for model in models:
        log(f"Модель: {model}")
        try:
            return extract_fields_genai(images, api_key, model, extra_text)
        except Exception as genai_exc:
            detail = redact_secrets(f"{type(genai_exc).__name__}: {genai_exc}", api_key)
            if _is_fatal_auth(genai_exc):
                raise RuntimeError(detail) from genai_exc
            if _is_missing_model(genai_exc):
                last_error = detail
                log(f"Модель {model} недоступна, следующая…")
                continue
            log("SDK ошибка, пробуем REST: " + detail)
            try:
                return extract_fields_requests(images, api_key, model, extra_text)
            except Exception as rest_exc:
                last_error = redact_secrets(str(rest_exc), api_key)
                if _is_fatal_auth(rest_exc):
                    raise RuntimeError(last_error) from rest_exc
                if _is_missing_model(rest_exc):
                    log(f"Модель {model} недоступна, следующая…")
                    continue
                raise RuntimeError(last_error) from rest_exc
    raise RuntimeError(last_error)


def extract_fields(images: list[bytes], extra_text: str = "") -> ApplicantFields:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Не задан GEMINI_API_KEY (или GOOGLE_API_KEY). "
            "Создайте ключ на https://aistudio.google.com/apikey и укажите его в .env."
        )
    models = model_candidates()
    try:
        raw = extract_with_models(images, api_key, models, extra_text)
    except Exception as first_exc:
        message = redact_secrets(str(first_exc), api_key)
        if _is_fatal_auth(first_exc):
            raise RuntimeError(message) from first_exc
        log("Заданные модели не ответили. Запрашиваю список доступных Flash у API…")
        try:
            discovered = [name for name in discover_flash_models(api_key) if name not in models]
        except Exception as list_exc:
            raise RuntimeError(
                message + " | список моделей: " + redact_secrets(str(list_exc), api_key)
            ) from list_exc
        if not discovered:
            raise RuntimeError(
                message
                + " Список Flash-моделей с generateContent пуст. "
                "Проверьте ключ на https://aistudio.google.com/apikey"
            )
        log("Доступны: " + ", ".join(discovered[:12]))
        raw = extract_with_models(images, api_key, discovered[:8], extra_text)
    return normalize_fields(raw)


def _normalize_field_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def official_form_values(
    applicant: ApplicantFields,
    trip: TripDetails | None = None,
) -> dict[str, str]:
    """Values keyed to overlay widgets on the official Italy embassy blank."""
    values: dict[str, str] = {}
    az_docs = bool(
        applicant.has_id_card
        or applicant.has_passport
        or applicant.fin_code
        or applicant.id_document_no()
    )
    if az_docs:
        values["Nationality"] = "AZERBAIJAN"
        values["Issuing_Country"] = "AZERBAIJAN"
    national_id = applicant.fin_code or applicant.id_document_no()
    if national_id:
        values["FIN_Code"] = national_id
    values["Main_Destination"] = "ITALY"
    values["First_Entry"] = "ITALY"
    values["Place_And_Date"] = f"BAKU, {date.today().strftime('%d/%m/%Y')}"
    if applicant.has_passport and not applicant.passport_pending:
        values["DocType_Ordinary_Passport"] = "Yes"
    if trip:
        ready = trip.normalized()
        if ready.destination:
            values["Main_Destination"] = ready.destination
            values["First_Entry"] = ready.destination
        if ready.arrival_date:
            values["Arrival_Date"] = ready.arrival_date
        if ready.departure_date:
            values["Departure_Date"] = ready.departure_date
        if ready.hotel:
            values["Travel_Purpose_Other"] = f"TOURISM. HOTEL: {ready.hotel}"
    return {key: value for key, value in values.items() if value}


def map_fields_to_pdf(
    pdf_field_names: list[str],
    applicant: ApplicantFields,
    trip: TripDetails | None = None,
) -> dict[str, str]:
    data = asdict(applicant)
    mapped: dict[str, str] = {}
    used_pdf: set[str] = set()

    for pdf_name, canonical in PDF_FIELD_MAP.items():
        if not canonical:
            continue
        value = data.get(canonical)
        if isinstance(value, bool) or not value:
            continue
        if pdf_name in pdf_field_names:
            mapped[pdf_name] = str(value)
            used_pdf.add(pdf_name)

    for canonical, aliases in FIELD_ALIASES.items():
        value = data.get(canonical)
        if isinstance(value, bool) or not value:
            continue
        alias_set = {_normalize_field_name(alias) for alias in aliases}
        for pdf_name in pdf_field_names:
            if pdf_name in used_pdf:
                continue
            if _normalize_field_name(pdf_name) in alias_set:
                mapped[pdf_name] = str(value)
                used_pdf.add(pdf_name)
                break

    for pdf_name, value in official_form_values(applicant, trip).items():
        if pdf_name in pdf_field_names and value:
            mapped[pdf_name] = value
    return mapped


def _unlock_form_fields(writer: PdfWriter) -> None:
    """Keep AcroForm widgets editable (clear ReadOnly, ask viewers to redraw)."""
    root = writer._root_object
    if "/AcroForm" not in root:
        return
    acro = root["/AcroForm"]
    acro.update({NameObject("/NeedAppearances"): BooleanObject(True)})
    raw_fields = acro.get("/Fields")
    if raw_fields is None:
        return
    stack = list(raw_fields)
    seen: set[int] = set()
    while stack:
        item = stack.pop()
        obj = item.get_object() if hasattr(item, "get_object") else item
        ident = id(obj)
        if ident in seen:
            continue
        seen.add(ident)
        kids = obj.get("/Kids") if isinstance(obj, dict) else None
        if kids:
            stack.extend(list(kids))
        flags = obj.get("/Ff", 0) if isinstance(obj, dict) else 0
        try:
            flag_int = int(flags)
        except (TypeError, ValueError):
            flag_int = 0
        if isinstance(obj, dict) and ("/T" in obj or "/FT" in obj):
            obj[NameObject("/Ff")] = NumberObject(flag_int & ~1)


def _add_specimen_mark(writer: PdfWriter) -> None:
    """Demo notice as a free-text annotation — does not flatten form fields."""
    writer.add_annotation(
        0,
        FreeText(
            text=specimen_notice_pdf(),
            rect=(36, 818, 560, 838),
            font="Helvetica",
            font_size="9pt",
            font_color="2563EB",
            border_color=None,
            background_color=None,
        ),
    )


def fill_schengen_pdf(
    template_path: Path,
    applicant: ApplicantFields,
    output_path: Path,
    trip: TripDetails | None = None,
    *,
    specimen: bool = False,
) -> Path:
    names = list_pdf_fields(template_path)
    log(f"get_fields(): {names if names else '(none)'}")
    if not names:
        log("Бланк статичный (нет AcroForm). Накладываю интерактивные поля по графам…")
        template_path, names, applied = ensure_interactive_form(template_path)
        log(f"overlay_applied: {applied}")
        log(f"get_fields(): {names}")
    if not names:
        raise RuntimeError(
            f"В бланке {template_path.name} нет интерактивных полей (AcroForm). "
            "Не удалось наложить поля на официальный PDF."
        )

    mapped = map_fields_to_pdf(names, applicant, trip)
    if not mapped:
        known = ", ".join(names)
        raise RuntimeError(
            "Не удалось сопоставить извлечённые данные с полями бланка. "
            f"Поля шаблона: {known}"
        )

    reader = PdfReader(str(template_path))
    writer = PdfWriter()
    writer.append(reader)

    writer.update_page_form_field_values(
        None,
        mapped,
        auto_regenerate=True,
        flatten=False,
    )
    _unlock_form_fields(writer)
    if specimen:
        _add_specimen_mark(writer)

    root = writer._root_object
    if "/AcroForm" in root:
        acro = root["/AcroForm"]
        acro.update({NameObject("/NeedAppearances"): BooleanObject(True)})
        if "/DR" not in acro:
            font = DictionaryObject()
            font.update(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                    NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
                }
            )
            fonts = DictionaryObject()
            fonts[NameObject("/Helv")] = writer._add_object(font)
            resources = DictionaryObject()
            resources[NameObject("/Font")] = fonts
            acro[NameObject("/DR")] = resources
        acro[NameObject("/DA")] = TextStringObject("/Helv 8 Tf 0 g")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    with tmp_path.open("wb") as handle:
        writer.write(handle)
    committed = _commit_output(tmp_path, output_path)

    log(f"Заполнены поля бланка: {', '.join(sorted(mapped))}")
    return committed


def _fill_ds160_node(node: object, values: dict[str, str]) -> None:
    if isinstance(node, dict):
        source = node.get("source")
        if isinstance(source, str) and "value" in node:
            raw_value = values.get(source, "")
            node["value"] = "" if isinstance(raw_value, bool) else str(raw_value or "")
        for child in node.values():
            _fill_ds160_node(child, values)
    elif isinstance(node, list):
        for child in node:
            _fill_ds160_node(child, values)


def write_ds160_answers(applicant: ApplicantFields, output_path: Path) -> None:
    if not DS160_TEMPLATE.is_file():
        raise RuntimeError(
            "Нет шаблона templates/us_ds160_template.json. "
            "Восстановите файл и запустите скрипт снова."
        )
    template = json.loads(DS160_TEMPLATE.read_text(encoding="utf-8"))
    payload = deepcopy(template)
    _fill_ds160_node(payload, asdict(applicant))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"DS-160 ответы записаны: {output_path.name}")


def _full_name(applicant: ApplicantFields) -> str:
    return ascii_punct(
        " ".join(part for part in (applicant.first_name, applicant.last_name) if part).strip() or "APPLICANT"
    )


def _employment_cover_lines(applicant: ApplicantFields) -> list[str]:
    if not applicant.has_employment_data():
        return [PENDING_EMPLOYMENT]
    lines: list[str] = []
    if applicant.job_title and applicant.employer_name:
        lines.append(f"I am employed as {applicant.job_title} at {applicant.employer_name}.")
    elif applicant.employer_name:
        lines.append(f"I am employed at {applicant.employer_name}.")
    elif applicant.job_title:
        lines.append(f"I am employed as {applicant.job_title}.")
    else:
        lines.append(PENDING_EMPLOYMENT)
    if applicant.monthly_income:
        lines.append(
            f"The enclosed employment certificate indicates a monthly income of {applicant.monthly_income} AZN."
        )
    return lines


def cover_letter_lines(
    applicant: ApplicantFields,
    country: str,
    trip: TripDetails | None = None,
    *,
    specimen: bool = False,
) -> list[str]:
    today = date.today().strftime("%d/%m/%Y")
    full_name = _full_name(applicant)

    if country == "usa":
        addressee = [
            "Embassy of the United States of America",
            "Visa Section",
            "Baku, Azerbaijan",
        ]
        subject = f"Subject: Nonimmigrant visa application (DS-160) - {full_name}"
        purpose = "an application for a nonimmigrant visa to the United States"
        form_item = "DS-160 confirmation"
    else:
        addressee = [
            "Consulate General of Italy",
            "Visa Section",
            "Baku, Azerbaijan",
        ]
        subject = f"Subject: Schengen visa application (Italy) - {full_name}"
        purpose = "an application for a short-stay Schengen visa (Italy as the main destination)"
        form_item = "the completed Schengen application form"

    identity_items: list[str] = [f"- {form_item};"]
    if applicant.has_passport and not applicant.passport_pending:
        identity_items.append("- passport biodata page;")
    else:
        identity_items.append(f"- Travel document (passport): {PENDING_PASSPORT};")
    if applicant.has_id_card:
        identity_items.append("- national identity document (ID card / shaxsiyyat vesiqesi);")
    else:
        identity_items.append("- national identity document (shaxsiyyat vesiqesi), if applicable;")

    extra_lines: list[str] = []
    if specimen:
        extra_lines.append(specimen_notice_pdf())
    if applicant.address:
        extra_lines.append(f"My registered address is {applicant.address}.")
    extra_lines.extend(_employment_cover_lines(applicant))
    if trip:
        ready = trip.normalized()
        stay_bits: list[str] = []
        if ready.arrival_date and ready.departure_date:
            stay_bits.append(f"from {ready.arrival_date} to {ready.departure_date}")
        if ready.hotel:
            stay_bits.append(f"at {ready.hotel}")
        if stay_bits:
            extra_lines.append("Intended stay " + " ".join(stay_bits) + ".")

    lines = [
        today,
        "",
        *addressee,
        "",
        subject,
        "",
        "Dear Visa Officer,",
        "",
        f"I hereby submit {purpose}.",
        "The application is lodged in Baku, Azerbaijan.",
        "",
        *extra_lines,
        "",
        "Please find enclosed:",
        *identity_items,
        "- travel medical insurance, itinerary and accommodation evidence, where prepared;",
        "- additional civil or financial documents as listed in the accompanying checklist.",
        "",
        "All personal data on the form follow ICAO Doc 9303 Latin transliteration.",
        "Dates are given as DD/MM/YYYY. No information has been invented where a source document was missing.",
        "",
        f"Sincerely, {full_name}",
    ]
    return [ascii_punct(line) for line in lines]


def checklist_text(
    applicant: ApplicantFields,
    country: str,
    trip: TripDetails | None = None,
    *,
    specimen: bool = False,
) -> str:
    full_name = _full_name(applicant)
    token = lastname_token(applicant.last_name)
    today = date.today().strftime("%d/%m/%Y")
    employment_item = (
        f"[x] Employment / enrolment certificate"
        if applicant.has_employment_data()
        else f"[ ] {PENDING_EMPLOYMENT}"
    )
    passport_item = (
        "[x] Passport biodata page (scan provided)"
        if applicant.has_passport and not applicant.passport_pending
        else f"[ ] Passport: {PENDING_PASSPORT}"
    )
    id_item = (
        "[x] National ID (shaxsiyyat vesiqesi) copy"
        if applicant.has_id_card
        else "[ ] National ID (shaxsiyyat vesiqesi) copy - FIN page"
    )

    if country == "usa":
        title = "US nonimmigrant visa (DS-160) - document checklist"
        post = "US Embassy, Baku"
        items = [
            "[ ] DS-160 confirmation page (barcode) after online submission",
            passport_item,
            "[ ] One US visa photograph (5 x 5 cm, white background) if required by the post",
            "[ ] Appointment confirmation (visa fee / MRV receipt)",
            employment_item,
            "[ ] Bank statements (last 3-6 months)",
            "[ ] Proof of ties to Azerbaijan (property, family, ongoing employment)",
            "[ ] Travel itinerary / invitation, if applicable",
            "[ ] Previous US visas or I-94 records, if any",
            id_item,
        ]
        extras = [
            f"Filled answers file: output/filled_{token}_ds160.json (agency working copy)",
            "Do not email the JSON to the Embassy - it is an internal fill aid for DS-160.",
        ]
    else:
        title = "Schengen visa (Italy) - document checklist"
        post = "Consulate General of Italy / VFS, Baku"
        items = [
            "[ ] Harmonised Schengen application form, signed",
            passport_item,
            "[ ] Copy of passport bio page and previous Schengen visas",
            "[ ] One recent colour photo 35 x 45 mm (ICAO)",
            "[ ] Travel medical insurance min. EUR 30,000, valid for the whole Schengen area",
            "[ ] Flight reservation (or other proof of travel)",
            "[ ] Hotel booking or invitation (with host ID / residence permit if private stay)",
            employment_item,
            "[ ] Salary slips and/or bank statements (last 3 months)",
            id_item,
            f"[ ] Cover letter (this file set includes cover_letter_{token}.pdf)",
        ]
        extras = [
            f"Filled form: output/filled_{token}_schengen.pdf",
        ]

    if trip:
        ready = trip.normalized()
        hotel_line = "[ ] Hotel booking or invitation (with host ID / residence permit if private stay)"
        if ready.hotel:
            hotel_line = (
                f"[x] Hotel / accommodation: {ready.hotel}"
                + (f" ({ready.arrival_date} - {ready.departure_date})" if ready.arrival_date else "")
            )
        items = [hotel_line if "Hotel booking" in item else item for item in items]
        if ready.arrival_date:
            extras.append(f"Intended travel: {ready.arrival_date} - {ready.departure_date}.")

    if specimen:
        extras.insert(0, SPECIMEN_NOTICE)

    if applicant.address:
        extras.append("Registered address extracted from ID / supporting documents.")
    if applicant.passport_pending:
        extras.append(f"Travel document number on the form: ID card number or {PENDING_PASSPORT}.")

    lines = [
        title,
        f"Place of filing: {post}",
        f"Applicant: {full_name}",
        f"Prepared: {today}",
        "",
        "Mark each item before the appointment. Do not staple original passports.",
        "",
        *items,
        "",
        "Agency notes",
        *extras,
        "Keep originals and one photocopy set. Return passport to the applicant after scanning.",
        "This checklist is for the Baku filing pack only. It is not a consular decision.",
        "",
    ]
    return ascii_punct("\n".join(lines) + "\n")


def write_cover_letter(
    applicant: ApplicantFields,
    country: str,
    output_path: Path,
    trip: TripDetails | None = None,
    *,
    specimen: bool = False,
) -> Path:
    title = "COVER LETTER - UNITED STATES VISA" if country == "usa" else "COVER LETTER - SCHENGEN VISA"
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    write_text_pdf(
        tmp_path,
        cover_letter_lines(applicant, country, trip, specimen=specimen),
        page_title=ascii_punct(title),
    )
    committed = _commit_output(tmp_path, output_path)
    log(f"Сопроводительное письмо: {committed.name}")
    return committed


def write_checklist(
    applicant: ApplicantFields,
    country: str,
    output_path: Path,
    trip: TripDetails | None = None,
    *,
    specimen: bool = False,
) -> Path:
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    tmp_path.write_text(
        checklist_text(applicant, country, trip, specimen=specimen),
        encoding="utf-8",
    )
    committed = _commit_output(tmp_path, output_path)
    log(f"Чек-лист: {committed.name}")
    return committed


def frames_from_uploads(uploads: list[tuple[str, bytes]]) -> list[bytes]:
    """Rasterize uploaded scans in a process-scoped temp dir (deleted on return)."""
    frames: list[bytes] = []
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="visa-upload-", dir=str(TEMP_DIR)) as tmp:
        tmp_dir = Path(tmp)
        for original_name, payload in uploads:
            suffix = Path(original_name).suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES:
                continue
            if suffix in IMAGE_SUFFIXES:
                with Image.open(BytesIO(payload)) as image:
                    frames.append(_pil_to_jpeg_bytes(image))
            elif suffix == PDF_SUFFIX:
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original_name).name) or "scan.pdf"
                pdf_path = tmp_dir / safe_name
                pdf_path.write_bytes(payload)
                pages = convert_from_path(str(pdf_path), output_folder=str(tmp_dir), fmt="jpeg")
                for page in pages:
                    frames.append(_pil_to_jpeg_bytes(page))
                    page.close()
                pdf_path.unlink(missing_ok=True)
    return frames


def write_visa_pack(
    applicant: ApplicantFields,
    country: str,
    dest_dir: Path,
    trip: TripDetails | None = None,
    *,
    specimen: bool = False,
) -> dict[str, Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    token = lastname_token(applicant.last_name)
    written: dict[str, Path] = {}
    if country == "usa":
        ds160_path = dest_dir / f"filled_{token}_ds160.json"
        write_ds160_answers(applicant, ds160_path)
        written["form"] = ds160_path
    else:
        template = ensure_schengen_template(SCHENGEN_TEMPLATE)
        log(f"Бланк: {template.name}")
        form_path = dest_dir / f"filled_{token}_schengen.pdf"
        written["form"] = fill_schengen_pdf(template, applicant, form_path, trip, specimen=specimen)
    written["cover_letter"] = write_cover_letter(
        applicant,
        country,
        dest_dir / f"cover_letter_{token}.pdf",
        trip,
        specimen=specimen,
    )
    written["checklist"] = write_checklist(
        applicant,
        country,
        dest_dir / f"document_checklist_{token}.txt",
        trip,
        specimen=specimen,
    )
    return written


def media_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".json":
        return "application/json"
    return "text/plain; charset=utf-8"


def visa_pack_bytes(
    applicant: ApplicantFields,
    country: str,
    trip: TripDetails | None = None,
    *,
    specimen: bool = False,
) -> dict[str, dict[str, object]]:
    """Build the filing pack in a temp dir and return file bytes. Temp files are deleted."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="visa-pack-", dir=str(TEMP_DIR)) as tmp:
        paths = write_visa_pack(applicant, country, Path(tmp), trip, specimen=specimen)
        pack: dict[str, dict[str, object]] = {}
        for key, path in paths.items():
            pack[key] = {
                "filename": path.name,
                "media_type": media_type_for(path),
                "content": path.read_bytes(),
            }
        return pack


def verify_outputs(paths: list[Path]) -> int:
    log("")
    log("=== Проверка output/ ===")
    failed = 0
    for path in paths:
        if path.is_file() and path.stat().st_size > 0:
            log(f"[OK] {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")
        else:
            log(f"[MISSING] {path.relative_to(ROOT)}")
            failed += 1
    listed = sorted(
        p.name for p in OUTPUT_DIR.iterdir() if p.is_file() and p.name != ".gitkeep"
    )
    log(f"Файлы в output/: {', '.join(listed) if listed else '(пусто)'}")
    return failed


def clear_applicant(applicant: ApplicantFields) -> None:
    for item in fields(applicant):
        setattr(applicant, item.name, item.default)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill visa documents from scans in input/ (Baku agencies)."
    )
    parser.add_argument(
        "--target-country",
        "-c",
        default="italy",
        help="Destination: italy (default, Schengen) or usa (DS-160 JSON).",
    )
    parser.add_argument("--arrival", default="", help="Intended arrival date DD/MM/YYYY.")
    parser.add_argument("--departure", default="", help="Intended departure date DD/MM/YYYY.")
    parser.add_argument("--hotel", default="", help="Hotel or accommodation (Latin).")
    return parser.parse_args(argv)


def trip_from_args(args: argparse.Namespace) -> TripDetails | None:
    arrival = (args.arrival or "").strip()
    departure = (args.departure or "").strip()
    hotel = (args.hotel or "").strip()
    if not (arrival or departure or hotel):
        return None
    return TripDetails(arrival_date=arrival, departure_date=departure, hotel=hotel)


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    load_env_file()
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        args = parse_args(argv)
        country = resolve_country(args.target_country)
    except RuntimeError as exc:
        log(f"ERROR: {exc}")
        return 1

    trip = trip_from_args(args)

    sources = collect_input_files(INPUT_DIR)
    if not sources:
        log(
            "В папке input/ нет файлов для обработки. "
            "Добавьте фото или сканы паспорта, şəxsiyyət vəsiqəsi или справок "
            "(JPG, PNG или PDF) и запустите скрипт снова."
        )
        return 0

    log(f"Страна: {country}")
    log(f"Входных файлов: {len(sources)} ({', '.join(path.name for path in sources)})")

    images: list[bytes] = []
    applicant: ApplicantFields | None = None
    written: list[Path] = []
    try:
        images = documents_to_jpeg_bytes(sources)
        if not images:
            log("Не удалось получить изображения из файлов в input/.")
            return 1

        log("Извлечение полей из документов…")
        applicant = extract_fields(images)
        if not applicant.filled_names():
            log("Не удалось извлечь поля из документов. Проверьте качество сканов.")
            return 1
        log(f"Извлечены поля: {', '.join(applicant.filled_names())}")
        kinds: list[str] = []
        if applicant.has_passport:
            kinds.append("Passport")
        if applicant.has_id_card:
            kinds.append("ID Card (shaxsiyyat vesiqesi)")
        if applicant.has_employment_certificate:
            kinds.append("Employment certificate")
        log("Типы документов: " + (", ".join(kinds) if kinds else "не определены"))
        if applicant.passport_pending:
            log("Паспорт не найден: поле Passport Number = номер ID-карты или [PENDING_PASSPORT]")
        if not applicant.has_employment_data():
            log("Справка с работы не найдена: [Employment Certificate: Pending/Not Provided]")

        if trip:
            ready = trip.normalized()
            log(
                "Поездка: "
                + ", ".join(
                    part
                    for part in (ready.arrival_date, ready.departure_date, ready.hotel)
                    if part
                )
            )

        pack_paths = write_visa_pack(applicant, country, OUTPUT_DIR, trip)
        written.extend(pack_paths.values())

        missing = verify_outputs(written)
        if missing:
            log("ERROR: не все выходные файлы созданы.")
            return 1
        log("Готово.")
        return 0
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 1
    finally:
        images.clear()
        if applicant is not None:
            clear_applicant(applicant)


if __name__ == "__main__":
    sys.exit(main())
