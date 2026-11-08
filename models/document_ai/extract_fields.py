"""Identify a document from its OCR text and read its fields.

The type comes from the document classifier (models/document_classifier); the fields from the
same rules the Nirnay engine uses (backend/fields.py), so the prototype route and the engine
read a document the same way.
"""
from __future__ import annotations

import re
from datetime import date

from backend import fields as _fields
from models.document_classifier import classifier as _classifier

# classifier type -> the engine's document slot (backend/intake.py SLOT_TYPES, reversed)
TYPE_SLOTS = {"pan_card": "pan", "aadhaar_card": "aadhaar", "salary_slip": "salarySlip", "form_16": "form16",
              "itr": "itr", "gst_certificate": "gst", "bank_statement": "bankStatement", "sale_deed": "saleDeed",
              "allotment_letter": "allotmentLetter", "encumbrance_certificate": "encumbrance",
              "property_tax_receipt": "propertyTax", "credit_report": "creditReport"}
_DMY = re.compile(r"\b(\d{2})[/-](\d{2})[/-](\d{4})\b")


def iso_date(text: str | None) -> str | None:
    """'15/08/1990', '15-08-1990' or '1990-08-15' -> '1990-08-15'; anything else -> None."""
    if not text:
        return None
    s = text.strip()
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        pass
    m = _DMY.search(s)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def extract_fields(text: str, expected_type: str | None = None) -> dict:
    """{"document_type", "slot", "type_confidence", "needs_review", "model_version",
        "extracted_fields": {"name", "date_of_birth", "pan", "aadhaar_last4", ...}}"""
    c = _classifier.predict({"ocr_text": text, "expected_type": expected_type})
    slot = TYPE_SLOTS.get(c["document_type"])
    raw = _fields.extract(slot or "", text)
    out: dict = {}
    if raw.get("name"):
        out["name"] = raw["name"]
    dob = iso_date(raw.get("dob"))
    if dob:
        out["date_of_birth"] = dob
    if raw.get("pan"):
        out["pan"] = raw["pan"]
    if raw.get("aadhaar"):
        out["aadhaar_last4"] = raw["aadhaar"][-4:]          # never the full number
    for k in ("employer", "netPay", "period", "year", "income", "business", "account"):
        if raw.get(k):
            out[k] = raw[k]
    return {"document_type": c["document_type"], "slot": slot, "type_confidence": c["confidence"],
            "needs_review": c["needs_review"], "reason_code": c["reason_code"], "model_version": c["model_version"],
            "extracted_fields": out}
