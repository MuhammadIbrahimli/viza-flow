"""Gemini API extraction from passport / ID / certificate scans."""

from __future__ import annotations

from app.services.pipeline import (
    ApplicantFields,
    extract_fields,
    frames_from_uploads,
    log,
)


def rasterize_uploads(uploads: list[tuple[str, bytes]]) -> list[bytes]:
    """Convert uploaded JPG/PNG/PDF bytes into JPEG frames for Gemini."""
    return frames_from_uploads(uploads)


def extract_applicant(frames: list[bytes], extra_text: str = "") -> ApplicantFields:
    """Call Gemini vision models and return ICAO-normalized applicant fields."""
    log("Gemini: extracting applicant fields")
    return extract_fields(frames, extra_text)
