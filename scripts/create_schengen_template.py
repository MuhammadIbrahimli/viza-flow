#!/usr/bin/env python3
"""Build an interactive AcroForm Schengen (Italy) blank into templates/."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
TEMPLATE_PATH = TEMPLATES_DIR / "schengen_italy.pdf"

PAGE_W = 595.28
PAGE_H = 841.89

FORM_FIELDS: tuple[tuple[str, str], ...] = (
    ("Last_Name", "1. Surname"),
    ("First_Name", "2. First name(s)"),
    ("DOB", "3. Date of birth (DD/MM/YYYY)"),
    ("Passport_No", "4. Travel document number"),
    ("Expiry_Date", "5. Document expiry date"),
    ("FIN_Code", "6. National ID / FIN"),
    ("Phone", "7. Telephone"),
    ("Employer_Name", "8. Employer / organisation"),
    ("Job_Title", "9. Occupation (English)"),
    ("Monthly_Income", "10. Monthly income"),
)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _stream(content: bytes, extra: str = "") -> bytes:
    header = f"<< /Length {len(content)}{extra} >>\nstream\n".encode("ascii")
    return header + content + b"\nendstream"


class PdfBuilder:
    def __init__(self) -> None:
        self._bodies: dict[int, bytes] = {}

    def add(self, obj_id: int, body: bytes) -> int:
        if not body.endswith(b"\n"):
            body += b"\n"
        self._bodies[obj_id] = body
        return obj_id

    def write(self, path: Path) -> None:
        chunks: list[bytes] = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
        offsets: dict[int, int] = {}
        for obj_id in sorted(self._bodies):
            offsets[obj_id] = sum(len(part) for part in chunks)
            chunks.append(f"{obj_id} 0 obj\n".encode("ascii") + self._bodies[obj_id] + b"endobj\n")
        xref_pos = sum(len(part) for part in chunks)
        max_id = max(self._bodies)
        xref = [f"xref\n0 {max_id + 1}\n", "0000000000 65535 f \n"]
        for index in range(1, max_id + 1):
            if index in offsets:
                xref.append(f"{offsets[index]:010d} 00000 n \n")
            else:
                xref.append("0000000000 65535 f \n")
        chunks.append("".join(xref).encode("ascii"))
        trailer = (
            f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        )
        chunks.append(trailer.encode("ascii"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(chunks))


def _page_content() -> bytes:
    commands = [
        "0.12 0.22 0.45 rg",
        "40 800 515 28 re f",
        "1 1 1 rg",
        "BT /Helv 13 Tf 52 808 Td (SCHENGEN VISA APPLICATION  -  ITALY) Tj ET",
        "0.15 0.15 0.18 rg",
        "BT /Helv 9 Tf 40 786 Td (Republic of Italy  |  Consular district: Baku, Azerbaijan) Tj ET",
        "BT /Helv 8 Tf 40 772 Td (Interactive AcroForm blank. Dates: DD/MM/YYYY. Names: ICAO Latin.) Tj ET",
        "0.12 0.22 0.45 RG",
        "1.2 w",
        "40 766 m 555 766 l S",
        "0.15 0.15 0.18 rg",
    ]
    field_top = 738.0
    row_h = 42.0
    for index, (_name, label) in enumerate(FORM_FIELDS):
        y_label = field_top - index * row_h
        commands.append(
            f"BT /Helv 9 Tf 44 {y_label + 16:.1f} Td ({_escape(label)}) Tj ET"
        )
        y_box = y_label - 2
        commands.extend(
            [
                "0.75 0.78 0.84 RG",
                "0.98 0.98 0.99 rg",
                "0.8 w",
                f"250 {y_box:.1f} 305 20 re B",
            ]
        )
    commands.extend(
        [
            "0.12 0.22 0.45 RG",
            "0.6 w",
            "40 300 m 555 300 l S",
            "0.25 0.25 0.28 rg",
            "BT /Helv 8 Tf 40 282 Td (Place of application: Baku. Purpose: tourism / business / family as declared by the applicant.) Tj ET",
            "BT /Helv 8 Tf 40 268 Td (Do not flatten this form. Fields remain editable for consular review.) Tj ET",
            "BT /Helv 8 Tf 40 248 Td (11. Declaration: I certify that the information provided is true and complete.) Tj ET",
            "0.75 0.78 0.84 RG",
            "0.98 0.98 0.99 rg",
            "40 78 515 150 re B",
            "0.15 0.15 0.18 rg",
            "BT /Helv 8 Tf 48 212 Td (For consular use / office remarks) Tj ET",
        ]
    )
    return "\n".join(commands).encode("latin-1", "replace")


_PDF_PUNCT = (
    ("\u2014", "-"),  # em dash
    ("\u2013", "-"),  # en dash
    ("\u2012", "-"),  # figure dash
    ("\u2010", "-"),  # hyphen
    ("\u2011", "-"),  # non-breaking hyphen
    ("\u2212", "-"),  # minus
    ("\u2018", "'"),
    ("\u2019", "'"),
    ("\u201a", "'"),
    ("\u201b", "'"),
    ("\u201c", '"'),
    ("\u201d", '"'),
    ("\u00ab", '"'),
    ("\u00bb", '"'),
    ("\u00a0", " "),
    ("\u2026", "..."),
    ("\u2022", "-"),
)


def pdf_safe_text(text: str) -> str:
    """Map unicode dashes/quotes to ASCII so Helvetica/latin-1 does not emit '?'."""
    mapped = (text or "").replace("\t", " ")
    for src, dst in _PDF_PUNCT:
        mapped = mapped.replace(src, dst)
    return mapped.encode("latin-1", "ignore").decode("latin-1")


def write_text_pdf(path: Path, lines: list[str], *, page_title: str = "") -> None:
    """Write a simple multi-page Helvetica PDF (cover letters). ASCII/Latin-1 only."""
    builder = PdfBuilder()
    font_id = 2
    builder.add(
        font_id,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>\n",
    )

    max_width = 90
    wrapped: list[str] = []
    if page_title:
        wrapped.append(pdf_safe_text(page_title))
        wrapped.append("")
    for raw in lines:
        text = pdf_safe_text(raw)
        if not text:
            wrapped.append("")
            continue
        while len(text) > max_width:
            cut = text.rfind(" ", 0, max_width)
            if cut < 20:
                cut = max_width
            wrapped.append(text[:cut].rstrip())
            text = text[cut:].lstrip()
        wrapped.append(text)

    line_h = 14
    top = 800
    bottom = 56
    lines_per_page = int((top - bottom) / line_h)
    pages: list[list[str]] = []
    for start in range(0, max(len(wrapped), 1), lines_per_page):
        pages.append(wrapped[start : start + lines_per_page])

    page_ids: list[int] = []
    content_ids: list[int] = []
    next_id = 10
    for page_lines in pages:
        y = top
        commands = ["BT", "/Helv 11 Tf"]
        for line in page_lines:
            escaped = _escape(line)
            commands.append(f"1 0 0 1 48 {y} Tm ({escaped}) Tj")
            y -= line_h
        commands.append("ET")
        content = "\n".join(commands).encode("latin-1", "ignore")
        content_id = next_id
        page_id = next_id + 1
        next_id += 2
        content_ids.append(content_id)
        page_ids.append(page_id)
        builder.add(content_id, _stream(content))

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    builder.add(
        3,
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>\n".encode("ascii"),
    )
    builder.add(1, b"<< /Type /Catalog /Pages 3 0 R >>\n")

    for page_id, content_id in zip(page_ids, content_ids):
        builder.add(
            page_id,
            (
                f"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 {PAGE_W:.2f} {PAGE_H:.2f}] "
                f"/Resources << /Font << /Helv {font_id} 0 R >> >> "
                f"/Contents {content_id} 0 R >>\n"
            ).encode("ascii"),
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    builder.write(path)


def build_schengen_template(path: Path = TEMPLATE_PATH) -> Path:
    builder = PdfBuilder()
    font_id = 2
    builder.add(
        font_id,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>\n",
    )

    content_id = 4
    builder.add(content_id, _stream(_page_content()))

    field_ids: list[int] = []
    field_top = 738.0
    row_h = 42.0
    first_field_id = 20
    for index, (name, _label) in enumerate(FORM_FIELDS):
        obj_id = first_field_id + index
        field_ids.append(obj_id)
        y_label = field_top - index * row_h
        y0 = y_label - 2
        y1 = y0 + 20
        x0, x1 = 250.0, 555.0
        body = (
            f"<< /Type /Annot /Subtype /Widget /FT /Tx /T ({_escape(name)}) "
            f"/Rect [{x0:.1f} {y0:.1f} {x1:.1f} {y1:.1f}] "
            f"/F 4 /P 5 0 R /Ff 0 "
            f"/DA (/Helv 10 Tf 0 g) /V () /DV () "
            f"/MK << /BC [0.45 0.48 0.55] /BG [1 1 1] >> "
            f"/BS << /W 1 /S /S >> >>\n"
        )
        builder.add(obj_id, body.encode("ascii"))

    annots = " ".join(f"{fid} 0 R" for fid in field_ids)
    builder.add(
        5,
        (
            f"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 {PAGE_W:.2f} {PAGE_H:.2f}] "
            f"/Resources << /Font << /Helv {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R /Annots [{annots}] >>\n"
        ).encode("ascii"),
    )
    builder.add(3, b"<< /Type /Pages /Kids [5 0 R] /Count 1 >>\n")

    fields_ref = " ".join(f"{fid} 0 R" for fid in field_ids)
    builder.add(
        6,
        (
            f"<< /Fields [{fields_ref}] /NeedAppearances true "
            f"/DA (/Helv 10 Tf 0 g) "
            f"/DR << /Font << /Helv {font_id} 0 R >> >> >>\n"
        ).encode("ascii"),
    )
    builder.add(1, b"<< /Type /Catalog /Pages 3 0 R /AcroForm 6 0 R >>\n")

    path.parent.mkdir(parents=True, exist_ok=True)
    builder.write(path)
    return path


def looks_like_official_italy_form(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 20_000:
        return False
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if len(reader.pages) < 5:
            return False
        text = reader.pages[0].extract_text() or ""
        return "Cognome" in text and ("Schengen" in text or "visto" in text.lower())
    except Exception:
        return False


def ensure_schengen_template(path: Path = TEMPLATE_PATH) -> Path:
    if path.is_file() and path.stat().st_size > 0:
        return path
    return build_schengen_template(path)


def main() -> int:
    if looks_like_official_italy_form(TEMPLATE_PATH):
        print(
            f"Official embassy blank already at {TEMPLATE_PATH} — not overwritten. "
            "Use scripts/overlay_schengen_fields.py to add AcroForm widgets.",
            flush=True,
        )
        return 0
    destination = build_schengen_template(TEMPLATE_PATH)
    print(f"Created interactive template: {destination}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
