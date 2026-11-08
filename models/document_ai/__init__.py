"""Document AI for the LoanGuard prototype's document route (api/document.py).

api/document.py imports three functions from this package; it was missing from the repo, so
that route never loaded. This package fills it with the parts the Nirnay engine already uses,
so both read a document the same way:

  ocr.extract_text(path)                       models/ocr (RapidOCR, English and Hindi; PDF text layer first)
  extract_fields.extract_fields(text)          models/document_classifier (type) + backend/fields.py (field rules)
  verification.verify_applicant(declared, x)   name and date-of-birth match, with the engine's 80/100 name rule

Nothing here decides a loan. The Nirnay engine (backend/) checks documents fully at upload
(type, applicant match, PAN, editing tools, reuse across applicants) and its gates decide.
A full Aadhaar number is never returned: only its last four digits.
"""
