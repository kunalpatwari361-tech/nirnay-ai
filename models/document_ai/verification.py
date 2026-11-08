"""Does the document belong to the applicant? Name and date of birth, compared with what they declared.

The name rule is the engine's: alike at 80/100 or more, ignoring spaces, case and the order of
initials (backend/fields.py name_score). A mismatch is a finding for an officer, never a
rejection by itself: OCR misreads happen.

The result has the shape the LoanGuard prototype reads (models/underwriting/engine.py and
models/explainability/engine.py): verification_status "matched" or not, and a list of mismatches.
"""
from __future__ import annotations

from backend import fields as _fields

from .extract_fields import iso_date

NAME_MATCH_AT = 80


def verify_applicant(declared: dict, extracted: dict) -> dict:
    """declared: {"name", "date_of_birth"}; extracted: extract_fields(...)["extracted_fields"].
    verification_status: matched (everything compared matched), mismatch (something differs: an
    officer compares the document with the applicant), or not_checked (nothing to compare)."""
    checks, mismatches = [], []
    name_d, name_x = (declared.get("name") or "").strip(), (extracted.get("name") or "").strip()
    if name_d and name_x:
        score = round(_fields.name_score(name_d, name_x), 1)
        checks.append({"field": "name", "declared": name_d, "on_document": name_x, "score": score,
                       "ok": score >= NAME_MATCH_AT})
        if score < NAME_MATCH_AT:
            mismatches.append({"field": "name", "reason": f"Name on the document differs from the declared name ({score}/100)"})
    dob_d, dob_x = iso_date(declared.get("date_of_birth")), iso_date(extracted.get("date_of_birth"))
    if dob_d and dob_x:
        checks.append({"field": "date_of_birth", "declared": dob_d, "on_document": dob_x, "ok": dob_d == dob_x})
        if dob_d != dob_x:
            mismatches.append({"field": "date_of_birth", "reason": "Date of birth on the document differs from the declared one"})
    status = "not_checked" if not checks else "mismatch" if mismatches else "matched"
    return {"verification_status": status, "mismatches": mismatches, "checks": checks,
            "action": {"matched": "none", "not_checked": "an officer checks the document",
                       "mismatch": "refer: an officer compares the document with the applicant"}[status]}
