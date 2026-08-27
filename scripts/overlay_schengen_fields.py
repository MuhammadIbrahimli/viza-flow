#!/usr/bin/env python3
"""Overlay AcroForm widgets on the official static Italy Schengen PDF."""

from __future__ import annotations

import sys
from pathlib import Path

import fitz
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ROOT / "templates" / "schengen_italy.pdf"

# Official embassy form: label column ~28-198, fill column ~198-486, admin ~487-567.
FILL_LEFT = 198.0
FILL_RIGHT = 484.0
INSET = 1.6

# (name, page_index, y0, y1, x0, x1, multiline)
# Coordinates measured from page.get_drawings() on templates/schengen_italy.pdf.
TEXT_FIELDS: tuple[tuple[str, int, float, float, float, float, bool], ...] = (
    ("Last_Name", 0, 244.4, 275.8, FILL_LEFT, FILL_RIGHT, False),
    ("Birth_Surname", 0, 275.8, 319.1, FILL_LEFT, FILL_RIGHT, False),
    ("First_Name", 0, 319.1, 351.2, FILL_LEFT, FILL_RIGHT, False),
    ("DOB", 0, 351.2, 386.2, FILL_LEFT, FILL_RIGHT, False),
    ("Place_Of_Birth", 0, 386.2, 418.4, FILL_LEFT, FILL_RIGHT, False),
    ("Country_Of_Birth", 0, 418.4, 450.4, FILL_LEFT, FILL_RIGHT, False),
    ("Nationality", 0, 450.4, 484.9, FILL_LEFT, FILL_RIGHT, False),
    ("Nationality_At_Birth", 0, 484.9, 520.1, FILL_LEFT, FILL_RIGHT, False),
    ("Other_Nationalities", 0, 520.1, 556.1, FILL_LEFT, FILL_RIGHT, False),
    ("FIN_Code", 0, 750.2, 793.6, FILL_LEFT, FILL_RIGHT, False),
    ("Passport_No", 1, 96.6, 134.5, FILL_LEFT, FILL_RIGHT, False),
    ("Issue_Date", 1, 134.5, 163.8, FILL_LEFT, FILL_RIGHT, False),
    ("Expiry_Date", 1, 163.8, 194.2, FILL_LEFT, FILL_RIGHT, False),
    ("Issuing_Country", 1, 194.2, 224.4, FILL_LEFT, FILL_RIGHT, False),
    ("Address", 1, 524.2, 571.6, FILL_LEFT, FILL_RIGHT, True),
    ("Phone", 1, 571.6, 610.0, FILL_LEFT, FILL_RIGHT, False),
    ("Job_Title", 1, 685.2, 726.0, FILL_LEFT, FILL_RIGHT, False),
    ("Employer_Name", 1, 726.0, 793.0, FILL_LEFT, FILL_RIGHT, True),
    ("Travel_Purpose_Other", 2, 118.0, 175.0, FILL_LEFT, FILL_RIGHT, True),
    ("Main_Destination", 2, 181.1, 261.7, FILL_LEFT, FILL_RIGHT, True),
    ("First_Entry", 2, 261.7, 301.6, FILL_LEFT, FILL_RIGHT, False),
    ("Arrival_Date", 2, 340.6, 410.6, FILL_LEFT, FILL_RIGHT, False),
    ("Departure_Date", 2, 410.6, 480.4, FILL_LEFT, FILL_RIGHT, False),
    ("Place_And_Date", 6, 28.0, 78.0, 37.8, 290.0, False),
)


def list_pdf_fields(path: Path) -> list[str]:
    reader = PdfReader(str(path))
    fields = reader.get_fields() or {}
    return list(fields.keys())


def _add_text_widget(
    page: fitz.Page,
    name: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    multiline: bool,
) -> None:
    rect = fitz.Rect(x0 + INSET, y0 + INSET, x1 - INSET, y1 - INSET)
    page_rect = page.rect
    rect = rect & page_rect
    if rect.is_empty or rect.width < 8 or rect.height < 8:
        return
    widget = fitz.Widget()
    widget.field_name = name
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = rect
    widget.field_value = ""
    widget.text_font = "Helv"
    widget.text_fontsize = 8 if rect.height >= 16 else 7
    widget.text_color = (0, 0, 0)
    widget.fill_color = None
    widget.border_color = None
    widget.border_width = 0
    if multiline:
        widget.field_flags = fitz.PDF_TX_FIELD_IS_MULTILINE
    page.add_widget(widget)


def _add_checkbox(page: fitz.Page, name: str, x: float, y: float, size: float = 10.0) -> None:
    rect = fitz.Rect(x, y, x + size, y + size) & page.rect
    if rect.is_empty:
        return
    widget = fitz.Widget()
    widget.field_name = name
    widget.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
    widget.rect = rect
    widget.field_value = False
    widget.border_color = (0, 0, 0)
    widget.border_width = 0.6
    widget.fill_color = None
    page.add_widget(widget)


def _add_ordinary_passport_checkbox(page: fitz.Page) -> None:
    hits = page.search_for("Passaporto ordinario")
    if not hits:
        hits = page.search_for("Adi pasport")
    if not hits:
        _add_checkbox(page, "DocType_Ordinary_Passport", 204.2, 38.0)
        return
    hit = hits[0]
    _add_checkbox(page, "DocType_Ordinary_Passport", hit.x0 - 14.0, hit.y0 + 0.5)


def overlay_fields(path: Path = TEMPLATE_PATH) -> list[str]:
    doc = fitz.open(str(path))
    try:
        for name, page_i, y0, y1, x0, x1, multiline in TEXT_FIELDS:
            if page_i >= doc.page_count:
                continue
            _add_text_widget(doc[page_i], name, x0, y0, x1, y1, multiline=multiline)
        if doc.page_count >= 2:
            _add_ordinary_passport_checkbox(doc[1])
        tmp = path.with_suffix(".overlay.tmp.pdf")
        doc.save(tmp, deflate=True, garbage=4)
    finally:
        doc.close()
    path.write_bytes(tmp.read_bytes())
    tmp.unlink(missing_ok=True)
    return list_pdf_fields(path)


def ensure_interactive_form(path: Path = TEMPLATE_PATH) -> tuple[Path, list[str], bool]:
    """Return (path, field_names, overlay_applied). Never rebuilds an official blank."""
    if not path.is_file():
        raise FileNotFoundError(f"Нет бланка {path}")
    names = list_pdf_fields(path)
    if names:
        return path, names, False
    names = overlay_fields(path)
    return path, names, True


def main() -> int:
    if not TEMPLATE_PATH.is_file():
        print(f"Missing {TEMPLATE_PATH}", flush=True)
        return 1
    path, names, applied = ensure_interactive_form(TEMPLATE_PATH)
    print(f"template: {path}", flush=True)
    print(f"overlay_applied: {applied}", flush=True)
    print(f"get_fields(): {names}", flush=True)
    return 0 if names else 1


if __name__ == "__main__":
    sys.exit(main())
