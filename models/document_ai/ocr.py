"""Read the text of a photo or PDF on disk, with the engine's OCR (models/ocr)."""
from __future__ import annotations

from pathlib import Path

from models.ocr import ocr as _ocr

MIN_CHARS = 20          # fewer readable characters than this: ask for a clearer photo


def read(path: str | Path) -> dict:
    """The OCR result with how it was read (pages, passes, confidence)."""
    p = Path(path)
    return _ocr.read(p.read_bytes(), p.name)


def extract_text(path: str | Path) -> str:
    """The document's text. Raises ValueError when nothing readable is on it (api/document.py
    answers that with a 400, so the applicant is asked for a clearer photo)."""
    r = read(path)
    text = (r.get("text") or "").strip()
    if r.get("error") and not text:
        raise ValueError(f"The document could not be read: {r['error']}")
    if sum(c.isalnum() for c in text) < MIN_CHARS:
        raise ValueError("No readable text was found. Take the photo again in good light, flat and in focus.")
    return text
