"""What happens when the customer gives consent, takes a selfie or uploads a document.

Each step runs its models straight away, so the app can show the result, and keeps
the evidence on the application for the decision gates.
"""
from __future__ import annotations

import hashlib
from datetime import date

from models.document_classifier import classifier
from models.transaction_tagger import tagger

from . import fields, sandbox
from .checks import links, tamper
from .config import settings
from .store import Application, Store, now

REQUIRED_CONSENTS = ("bureau", "aa", "kyc", "privacy")
SLOT_TYPES = {"pan": "pan_card", "aadhaar": "aadhaar_card", "salarySlip": "salary_slip", "form16": "form_16", "itr": "itr",
              "gst": "gst_certificate", "bankStatement": "bank_statement", "saleDeed": "sale_deed",
              "allotmentLetter": "allotment_letter", "encumbrance": "encumbrance_certificate",
              "propertyTax": "property_tax_receipt"}
TYPE_SLOTS = {v: k for k, v in SLOT_TYPES.items()}
NAMED_SLOTS = ("pan", "aadhaar", "salarySlip", "form16", "itr", "gst", "bankStatement")
ID_PHOTO_SLOTS = ("pan", "aadhaar")


class IntakeError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


# --------------------------------------------------------------------------- consent

def record_consents(app: Application, body: dict) -> dict:
    """Save the customer's consent choices (DPDP Act 2023: specific, informed, and withdrawable)."""
    given = {k: bool(body.get("given", {}).get(k)) for k in REQUIRED_CONSENTS}
    options = body.get("options", {})
    accounts = {k: bool(v) for k, v in options.get("aaAccounts", {"salary": True}).items()}
    months = int(options.get("aaMonths", 12))
    if months not in (6, 12, 24):
        raise IntakeError("bad_aa_months")
    app.consents = {
        "given": given, "at": now().isoformat(),
        "aa": {"months": months, "accounts": accounts},
        "kyc": {"method": options.get("kycMethod", "aadhaarOtp"), "use_ckyc": bool(options.get("useCkyc"))},
        "bureau": {"recheck_before_payout": bool(options.get("recheckBeforePayout"))},
        "optional": {k: bool(v) for k, v in body.get("optional", {}).items()},
        "notice_version": "privacy-notice-v1",
    }
    app.consent_log.append({"at": app.consents["at"], "given": given, "optional": app.consents["optional"]})
    return app.consents


def consents_ok(app: Application) -> bool:
    c = app.consents
    return bool(c and all(c["given"].get(k) for k in REQUIRED_CONSENTS) and any(c["aa"]["accounts"].values()))


# --------------------------------------------------------------------------- data pull (eKYC, PAN, bureau, AA)

def fetch_profile(app: Application, pan: str, declared: dict | None = None) -> dict:
    if not consents_ok(app):
        raise IntakeError("consent_required", 409)
    pan = pan.strip().upper()
    if not fields.PAN.fullmatch(pan):
        raise IntakeError("bad_pan")
    dl = digilocker_identity(app)
    if dl and dl.get("pan") and dl["pan"] != pan:
        raise IntakeError("pan_mismatch")                 # the PAN must be the one DigiLocker shared
    if app.mode == "real":
        return _declared_profile(app, pan, declared)
    p = app.persona
    app.pan = pan
    aadhaar_last4 = str(int(app.id.split("-")[-1]) % 10000).zfill(4)
    ekyc = sandbox.ekyc(p, aadhaar_last4)
    if dl and dl.get("aadhaar_masked"):
        ekyc["aadhaar_masked"], ekyc["source"] = dl["aadhaar_masked"], dl["source"]
    pan_check = sandbox.pan_verify(p, pan)
    bureau = sandbox.bureau(p)
    aa = sandbox.aa_statement(p, app.consents["aa"]["months"], app.consents["aa"]["accounts"])
    tagged = tagger.tag_statement(aa["transactions"])
    s = tagged["summary"]
    credits = sum(m.get(c, 0) for m in s["by_month"].values() for c in ("salary", "other_income", "cash_deposit"))
    s["avg_monthly_credits"] = round(credits / max(len(s["months"]), 1), 2)
    app.bank = {"model": tagged["model"], "model_version": tagged["model_version"], "summary": s,
                "account_masked": aa["account_masked"], "account_months": aa["account_months"],
                "accounts_shared": aa["accounts"], "months": aa["months"], "transactions": len(aa["transactions"])}
    itr = sandbox.itr(p)
    if p.employment == "salaried":
        income = s["avg_monthly_salary"] if s["salary_months"] else 0.0
    else:
        income = round(itr["total_income"] / 12, 2) if itr else 0.0
    existing_emi = max(bureau["existing_emi"], s["avg_monthly_emi"])
    app.sources = {"ekyc": ekyc, "pan": pan_check, "bureau": bureau, "itr": itr,
                   "aa": {k: v for k, v in aa.items() if k != "transactions"}, **({"digilocker": dl} if dl else {})}
    app.profile = {"name": ekyc["name"], "dob": ekyc["dob"], "gender": ekyc["gender"], "address": ekyc["address"],
                   "pincode": ekyc["pincode"], "address_months": ekyc["address_months"], "email": p.email,
                   "employment": p.employment, "employer": p.employer, "monthly_income": income,
                   "income_source": "bank salary credits (model 5)" if p.employment == "salaried" else "ITR via consent",
                   "existing_emi": existing_emi, "bureau": bureau, "pan_masked": pan_check["pan_masked"],
                   "pan_name": pan_check["name"], "aadhaar_masked": ekyc["aadhaar_masked"]}
    return app.profile


SELF_DECLARED = ("name", "dob", "email", "address", "employment", "employer", "monthly_income", "existing_emi", "credit_score")


def _declared_profile(app: Application, pan: str, d: dict | None) -> dict:
    """Real mode. Identity comes from DigiLocker when the applicant shared it there, and the credit
    report from the bureau when one is connected; whatever is left the applicant types in, marked
    self-declared, and the documents then have to prove it."""
    from . import bureau as bureau_mod
    if not d:
        raise IntakeError("details_required")
    dl = digilocker_identity(app) or {}
    name = dl.get("name") or (d.get("name") or "").strip()
    if len(name) < 2:
        raise IntakeError("details_required")
    try:
        dob = date.fromisoformat(dl.get("dob") or d["dob"])
    except (KeyError, TypeError, ValueError) as e:
        raise IntakeError("bad_dob") from e
    age = (date.today() - dob).days / 365.25
    if not 18 <= age <= 80:
        raise IntakeError("bad_dob")
    app.pan = pan
    app.declared = {k: d.get(k) for k in SELF_DECLARED}
    address = dl.get("address") or d.get("address") or ""
    source = bureau_mod.provider(settings.sandbox)
    if source is not None:
        who = {"application_id": app.id, "name": name, "dob": dob.isoformat(), "mobile": app.phone, "pan": pan,
               "address": address, "pincode": dl.get("pincode"), "persona": app.persona.key if app.persona else None}
        try:
            bureau = source.pull(who, today())
        except bureau_mod.BureauError as e:        # the decision waits for an officer instead of failing
            bureau = bureau_mod.empty(source.source, "unavailable", error=e.code)
        bureau["pulled_at"] = now().isoformat()
    else:
        # No bureau connected. The app no longer asks for a credit score (older versions did), so the
        # report is "not pulled" and an officer pulls it before deciding (R-BUR-06).
        score = d.get("credit_score")
        bureau = (bureau_mod.empty("self-declared", "self_declared", score=int(score)) if score
                  else bureau_mod.empty("not-connected", "not_pulled"))
    existing_emi = max(float(d["existing_emi"]), float(bureau.get("existing_emi") or 0))
    bureau["existing_emi"] = existing_emi
    from_digilocker = [k for k in ("name", "dob", "address") if dl.get(k)]
    declared = [k for k in SELF_DECLARED if k not in from_digilocker
                and not (k == "credit_score" and (source is not None or not d.get("credit_score")))]
    app.sources = {"declared": app.declared, **({"digilocker": dl} if dl else {}),
                   **({"bureau": {k: bureau.get(k) for k in ("source", "reference", "pulled_at", "status")}} if source else {})}
    app.profile = {"name": name, "dob": dob.isoformat(), "gender": dl.get("gender"), "address": address,
                   "pincode": dl.get("pincode"), "address_months": None,
                   "email": (d.get("email") or "").strip().lower() or None,
                   "employment": d["employment"], "employer": d["employer"].strip(),
                   "monthly_income": float(d["monthly_income"]), "income_source": "self-declared, to be proven by documents",
                   "existing_emi": existing_emi, "bureau": bureau, "pan_masked": f"{pan[:2]}XXX{pan[5:9]}X",
                   "pan_name": dl.get("pan_name"), "aadhaar_masked": dl.get("aadhaar_masked"), "self_declared": declared,
                   "identity_source": dl.get("source")}
    return app.profile


# --------------------------------------------------------------------------- DigiLocker (backend/digilocker.py)

def digilocker_identity(app: Application) -> dict | None:
    return app.sources.get("digilocker") if (app.digilocker or {}).get("status") == "done" else None


def apply_digilocker(app: Application, got: dict, source: str) -> dict:
    """Keep what DigiLocker shared (never the Aadhaar number, the files or the photo). The
    e-Aadhaar photo goes straight into the face match with the selfie, in memory only.

    Before the details are saved, DigiLocker fills them in. After (from the documents step), it
    stands in for photos of the PAN and Aadhaar cards: the PAN must be the one entered, and the
    verified name, date of birth and address replace what was typed."""
    a, p = got.get("aadhaar") or {}, got.get("pan") or {}
    if app.profile is not None and p.get("pan") and app.pan and p["pan"] != app.pan:
        raise IntakeError("pan_mismatch", 409)
    photo = a.get("photo")
    ident = {"source": source, "at": now().isoformat(), "name": a.get("name") or p.get("name"),
             "dob": a.get("dob") or p.get("dob"), "gender": a.get("gender") or p.get("gender"),
             "address": a.get("address") or None, "pincode": a.get("pincode"), "aadhaar_masked": a.get("aadhaar_masked"),
             "pan": p.get("pan"), "pan_name": p.get("name"), "has_aadhaar": bool(a), "has_pan": bool(p),
             "aadhaar_photo": photo is not None,
             # Is DigiLocker's mobile number the one the applicant signed in with? (The number isn't kept.)
             "mobile_matches": got["mobile"] == app.phone if got.get("mobile") else None}
    if p.get("pan"):
        app.pan = p["pan"]
    if a and p.get("name"):
        ident["pan_name_score"] = round(fields.name_score(p["name"], a["name"]), 1)
    if app.profile is not None:
        _verified_profile(app, ident)
    app.sources["digilocker"] = ident
    app.digilocker = {"status": "done", "at": ident["at"]}
    if photo:
        _id_photo_bytes(app, "digilocker", photo)
    return ident


def _verified_profile(app: Application, ident: dict) -> None:
    """DigiLocker came after the details were saved. Its verified facts replace the typed ones; how
    far the typed name and date of birth were from them stays on record for the identity gate."""
    prof = app.profile
    ident["typed_vs_digilocker"] = {
        "name_score": round(fields.name_score(ident["name"], prof["name"]), 1) if ident.get("name") else None,
        "dob_matches": ident["dob"] == prof["dob"] if ident.get("dob") else None,
    }
    for k in ("name", "dob", "gender", "address", "pincode"):
        if ident.get(k):
            prof[k] = ident[k]
    if ident.get("pan_name"):
        prof["pan_name"] = ident["pan_name"]
    if ident.get("aadhaar_masked"):
        prof["aadhaar_masked"] = ident["aadhaar_masked"]
    prof["identity_source"] = ident["source"]
    verified = {k for k in ("name", "dob", "address") if ident.get(k)}
    prof["self_declared"] = [k for k in prof.get("self_declared") or [] if k not in verified]
    for d in app.documents.values():                 # papers already read: check them against the verified name
        found = (d.get("fields") or {}).get("name")
        if found and d.get("name_score") is not None:
            d["name_score"] = round(fields.name_score(found, prof["name"]), 1)


def _id_photo_bytes(app: Application, slot: str, photo: bytes) -> None:
    """The e-Aadhaar photo is a small JPEG 2000 (or JPEG): convert it, find the face, match."""
    import io
    from PIL import Image, ImageOps
    from models.face_match import face
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(photo))).convert("RGB")
        if min(img.size) < 160:                       # e-Aadhaar photos are tiny; give the detector room
            k = 160 / min(img.size)
            img = img.resize((round(img.width * k), round(img.height * k)))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=95)
        d = face.model().describe(buf.getvalue())
    except Exception:                                 # an unreadable photo just means no face match from it
        return
    if d["fingerprint"] is not None:
        app.id_fingerprint, app.id_photo_slot = d["fingerprint"], slot
        _match_faces(app)


def _bank_from_statement(app: Application, text: str, found: dict) -> dict | None:
    """Real mode: the uploaded statement's rows go through model 5, as AA data would."""
    from . import statement
    parsed = statement.parse(text)
    if not parsed["usable"]:
        return {"usable": False, "rows_read": parsed["rows_read"]}
    tagged = tagger.tag_statement(parsed["transactions"])
    s = tagged["summary"]
    credits = sum(m.get(c, 0) for m in s["by_month"].values() for c in ("salary", "other_income", "cash_deposit"))
    s["avg_monthly_credits"] = round(credits / max(len(s["months"]), 1), 2)
    app.bank = {"model": tagged["model"], "model_version": tagged["model_version"], "summary": s,
                "account_masked": found.get("account"), "account_months": None, "accounts_shared": ["uploaded statement"],
                "months": len(s["months"]), "transactions": parsed["rows_read"], "source": "uploaded statement"}
    if app.profile is not None:
        app.profile["existing_emi"] = max(app.profile["existing_emi"], s["avg_monthly_emi"])
    return {"usable": True, "rows_read": parsed["rows_read"], "rows_skipped": parsed["rows_skipped"],
            "months": parsed["months"]}


def required_documents(app: Application) -> list[str]:
    from .policy import POLICY
    emp = (app.profile or {}).get("employment", "salaried")
    docs = list(POLICY["required_documents"][emp])
    dl = digilocker_identity(app) or {}
    # DigiLocker shared the PAN record: no photo of the card needed. Real mode never skips the face
    # match, so the PAN card is still asked for when the e-Aadhaar photo gave no face to match with.
    if dl.get("has_pan") and (app.mode != "real" or app.id_photo_slot == "digilocker"):
        docs.remove("pan")
    if dl.get("has_aadhaar"):
        docs.remove("aadhaar")              # the same for the e-Aadhaar
    if app.mode == "real":
        docs.append("bankStatement")        # no Account Aggregator, so the statement is uploaded
    return docs


# --------------------------------------------------------------------------- selfie (models 9 and 10)

def check_selfie(app: Application, data: bytes) -> dict:
    from models.face_match import face
    from models.liveness import liveness
    try:
        d = face.model().describe(data)
    except ValueError as e:
        raise IntakeError("not_an_image", 415) from e
    result = {"face_found": d["faces_found"] > 0, "faces": d["faces_found"], "live": False, "liveness": None,
              "face_score": d["face_score"], "too_small": d["too_small"], "at": now().isoformat()}
    if d["fingerprint"] is not None:
        live = liveness.model().check(d["image"], d["face_box"])
        result.update(live=live["live"], liveness=live)
        app.selfie_fingerprint = d["fingerprint"]
    app.selfie = result
    _match_faces(app)
    return result


def check_live_selfie(app: Application, challenge_id: str, frames: list[bytes]) -> dict:
    """The in-app live selfie: one frame per step of the head-turn challenge (backend/live_selfie.py)."""
    from . import live_selfie
    try:
        steps = live_selfie.take(challenge_id, app.id)
        result = live_selfie.check(frames, steps)
    except live_selfie.LiveSelfieError as e:
        raise IntakeError(e.code, 415 if e.code == "not_an_image" else 409 if e.code.startswith("challenge") else 400) from e
    fingerprint = result.pop("fingerprint")
    result["at"] = now().isoformat()
    if fingerprint is not None:
        app.selfie_fingerprint = fingerprint
    app.selfie = result
    _match_faces(app)
    return result


def _match_faces(app: Application) -> None:
    """Selfie against the ID photo, whichever of the two arrives second."""
    if app.selfie_fingerprint is None or app.id_fingerprint is None:
        return
    from models.face_match import face
    m = face.model()
    a = {"faces_found": 1, "fingerprint": app.selfie_fingerprint, "too_small": False}
    b = {"faces_found": 1, "fingerprint": app.id_fingerprint, "too_small": False}
    app.face_match = {**m.compare(a, b), "id_photo_from": app.id_photo_slot, "at": now().isoformat()}


# --------------------------------------------------------------------------- documents (models 8, 6, 11 + checks)

def _kind(data: bytes) -> str | None:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def upload_document(store: Store, app: Application, slot: str, data: bytes, filename: str) -> dict:
    from models.ocr import ocr
    if slot not in SLOT_TYPES:
        raise IntakeError("unknown_slot")
    if len(data) > settings.max_upload_bytes:
        raise IntakeError("file_too_large", 413)
    kind = _kind(data)
    if kind is None:
        raise IntakeError("unsupported_file", 415)
    read = ocr.read(data, filename)
    text = read["text"]
    cls = classifier.predict({"ocr_text": text, "expected_type": SLOT_TYPES[slot]})
    found = fields.extract(slot, text) if cls["flag"] == "pass" else {}
    rec = {"slot": slot, "file_kind": kind, "uploaded_at": now().isoformat(),
           "ocr": {k: read[k] for k in ("model_version", "pages", "sources", "passes", "mean_confidence", "has_hindi", "error")},
           "text_chars": len(text), "classifier": {k: cls[k] for k in ("model_version", "document_type", "confidence", "top3",
                                                                      "needs_review", "flag", "reason_code")},
           "accepted": cls["flag"] == "pass", "reason_code": cls["reason_code"], "fields": found,
           "tamper": tamper.check(data)}
    # The same file on another applicant's application. Documents are compared byte for byte:
    # every PAN card shares one template, so "looks alike" would match different people.
    digest = hashlib.sha256(data).hexdigest()
    mine = {a for a, i in store.identities().items() if app.pan and i.get("pan") == app.pan}
    rec["reuse"] = links.duplicate_check(app.id, digest, store.file_digests(), same_person=mine)
    app.file_digests.add(digest)
    if app.profile and found.get("name") and slot in NAMED_SLOTS:
        rec["name_score"] = round(fields.name_score(found["name"], app.profile["name"]), 1)
    if slot == "pan" and found.get("pan") and app.pan:
        rec["pan_matches"] = found["pan"] == app.pan
    if slot in ID_PHOTO_SLOTS and kind != "pdf" and rec["accepted"]:
        _id_photo(app, slot, data, rec)
    if slot == "saleDeed" and rec["accepted"]:
        from models.llm import llm
        rec["deed"] = llm.read_deed(text)
    if slot == "aadhaar" and found.get("aadhaar") and app.profile is not None:
        app.profile["aadhaar_masked"] = found["aadhaar"]            # only ever the last four digits
    if slot == "pan" and found.get("name") and app.profile is not None and app.mode == "real":
        app.profile["pan_name"] = found["name"]                      # no NSDL check in real mode: the card's name
    if slot == "bankStatement" and rec["accepted"] and app.mode == "real":
        rec["statement"] = _bank_from_statement(app, text, found)
    # A failed document can't be cleared by uploading another one: the failure stays on the file.
    prev = app.documents.get(slot) or {}
    earlier = list(prev.get("failed_before") or [])
    if (prev.get("verification") or {}).get("verdict") == "failed":
        earlier.append({"uploaded_at": prev["uploaded_at"], "problems": prev["verification"]["problems"]})
    if earlier:
        rec["failed_before"] = earlier
    rec["verification"] = verify(rec)
    app.documents[slot] = rec
    # Keep the file for officers, except Aadhaar: UIDAI rules forbid storing Aadhaar copies,
    # so only the masked number and the checks are kept.
    keep = settings.store_files and slot != "aadhaar"
    store.save_document(app, rec, digest, data if keep else None, kind)
    return rec


def _id_photo(app: Application, slot: str, data: bytes, rec: dict) -> None:
    from models.face_match import face
    d = face.model().describe(data)
    rec["id_photo_face"] = d["fingerprint"] is not None
    if d["fingerprint"] is not None and (app.id_fingerprint is None or slot == "aadhaar"):
        app.id_fingerprint, app.id_photo_slot = d["fingerprint"], slot
        _match_faces(app)


# --------------------------------------------------------------------------- document verification

NAME_MISMATCH_BELOW = 50    # a name this far from the applicant's is another person's, not an OCR misread
DOUBTFUL_CAP = 60           # a later edit date or an appended revision: an officer looks, the score shows the doubt
FAILED_CAP = 10


def verify(rec: dict) -> dict:
    """The verdict on one uploaded document, and its 0-100 verification score. No one confirms
    what was read: the checks decide.

    retake:   the classifier couldn't confirm it's the document asked for, or OCR couldn't read it,
              so nothing could be checked. The applicant uploads it again.
    failed:   it is the document asked for, and it names an editing tool, or is the same file as on
              another applicant's application, or its PAN isn't the one entered, or its name is
              another person's, or an earlier upload in this slot failed. The application is
              rejected (R-DOC-05).
    verified: every check passed.
    The score is the weakest check: the classifier's confidence, the name match, and a cap for a
    doubtful or failed check. Rules decide the rejection, not a model on its own (CMP-07).
    """
    signals = rec["tamper"]["signals"]
    edited = tamper.edited(signals)
    reused = bool((rec.get("reuse") or {}).get("reused_from"))
    name, pan, earlier = rec.get("name_score"), rec.get("pan_matches"), rec.get("failed_before") or []
    checks = {"type": rec["accepted"], "readable": rec["reason_code"] != "R-DOC-02", "untouched": not edited, "unique": not reused}
    if name is not None:
        checks["name"] = name >= NAME_MISMATCH_BELOW
    if pan is not None:
        checks["pan"] = pan
    if not rec["accepted"]:
        return {"verdict": "retake", "score": None, "checks": checks, "problems": []}
    problems = []
    if edited:
        problems.append("the file names an editing tool")
    if reused:
        problems.append("the same file is on another applicant's application")
    if pan is False:
        problems.append("the PAN on the card isn't the one entered")
    if name is not None and name < NAME_MISMATCH_BELOW:
        problems.append(f"the name is another person's (name score {name})")
    if earlier:
        problems.append("an earlier upload of this document failed verification")
    score = rec["classifier"]["confidence"] * 100
    if name is not None:
        score = min(score, name)
    if signals and not edited:
        score = min(score, DOUBTFUL_CAP)
    if problems:
        score = min(score, FAILED_CAP)
    return {"verdict": "failed" if problems else "verified", "score": int(round(score)), "checks": checks, "problems": problems}


def verification(rec: dict) -> dict:
    """A document's verification, worked out again for files uploaded before verdicts existed."""
    return rec.get("verification") or verify(rec)


def doc_result(rec: dict) -> dict:
    """The app's DocResult. The fraud checks (editing tools, reused files) are left out of what the
    applicant sees, so the checks aren't taught; a failure shows only as the verdict."""
    detected = TYPE_SLOTS.get(rec["classifier"]["document_type"], rec["classifier"]["document_type"])
    shown = {k: v for k, v in rec["fields"].items() if k in ("name", "pan", "aadhaar", "employer", "netPay", "period", "year",
                                                              "income", "business")}
    v = verification(rec)
    return {"slot": rec["slot"], "detectedType": detected, "confidence": rec["classifier"]["confidence"],
            "accepted": rec["accepted"], "reasonCode": rec["reason_code"], "fields": shown,
            "verification": {"verdict": v["verdict"], "score": v["score"],
                             "checks": {k: ok for k, ok in v["checks"].items() if k in ("type", "readable", "name", "pan")}}}


def today() -> date:
    return now().date()
