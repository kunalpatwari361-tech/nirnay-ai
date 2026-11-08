"""Where each piece of evidence came from, when, whether it was verified, and what happens without it.

sources() is called when the evidence is gathered, and its list is kept in the decision record
(evidence.data_sources), so an officer or auditor sees, per source: the provider, the mode
(sandbox, live, uploaded, self-declared, not connected), when it was fetched or read, the
verification status, and the fallback the engine uses when that source is missing or fails.
For records made before this list existed, sources(evidence) rebuilds it from the evidence alone.

No source failing ever stops a decision: the engine refers the case to an officer with a reason
code instead, and never approves on a missing check.
"""
from __future__ import annotations

FALLBACK = {
    "identity": "Without DigiLocker or eKYC, the PAN and Aadhaar cards are uploaded and read; a name that differs refers (R-KYC-02).",
    "pan": "The PAN card is uploaded and read; a PAN that differs from the one entered fails the document (R-DOC-05).",
    "credit_bureau": "No report, a failed pull or a test-server report refers to an officer, who pulls it (R-BUR-06). "
                     "An applicant's own uploaded report counts until sanction, then an officer confirms it (R-BUR-07).",
    "bank_statement": "Income is then taken from the salary slip or ITR; with nothing proven, refer (R-AFF-04). "
                      "A LAP can't be assessed without it (R-AFF-05).",
    "income_tax": "The ITR document is uploaded and read instead; with nothing proven, refer (R-AFF-04).",
    "payroll": "Without the employer's payroll record, a slip well above the bank credits refers (R-FRD-02) instead of rejecting.",
    "sanctions_lists": "Screening didn't run, so refer (R-AML-02); reload the list and screen again before deciding.",
    "selfie_and_face_match": "No live selfie or no face match refers to a video KYC call (R-KYC-01).",
    "documents": "A missing required document refers (R-DOC-04); an unreadable or wrong one asks the applicant to retake it.",
    "same_pan_lookup": "Not checked in the sandbox (the demo people share one sample PAN).",
    "property_valuation": "A low-confidence valuation refers to an empanelled valuer (R-PRP-02).",
    "land_records": "The title is read from the sale deed; with no deed, refer (R-PRP-01). For a LAP, encumbrance "
                    "unchecked refers (R-PRP-06).",
    "sale_deed": "No deed: the title rests on land records only; with neither, refer (R-PRP-01).",
}


def _mode(provider: str | None, sandbox: bool) -> str:
    p = (provider or "").lower()
    if not p or p in ("not-connected", "none"):
        return "not_connected"
    if "staging" in p or "test" in p:
        return "provider_test_server"
    if sandbox or p.startswith("sandbox"):
        return "sandbox"
    return "live"


def _row(source: str, provider: str | None, mode: str, at: str | None, status: str, used_for: str, **extra) -> dict:
    return {"source": source, "provider": provider, "mode": mode, "at": at, "status": status, "used_for": used_for,
            "if_unavailable": FALLBACK[source], **extra}


BUREAU_STATUS = {"scored": "verified", "no_history": "verified (no credit history)", "not_pulled": "not_connected",
                 "unavailable": "failed", "applicant_report": "uploaded (not yet confirmed with the bureau)",
                 "self_declared": "self_declared (not verified)"}


def sources(ev: dict, app_sources: dict | None = None, documents: dict | None = None,
            checked_at: str | None = None) -> list[dict]:
    src, docs, sandbox = app_sources or {}, documents or {}, bool(ev.get("sandbox"))
    out = []

    # identity: eKYC (sandbox) or DigiLocker (real), else typed in and proven by the cards
    dl = src.get("digilocker")
    ident = ev["identity"]
    if dl:
        out.append(_row("identity", dl.get("source"), _mode(dl.get("source"), sandbox), dl.get("at"), "verified",
                        "name, date of birth, address, masked Aadhaar"))
    elif src.get("ekyc"):
        out.append(_row("identity", src["ekyc"].get("source"), "sandbox", checked_at, "verified",
                        "name, date of birth, address, masked Aadhaar"))
    else:
        typed = "name" in (ev.get("self_declared") or [])
        out.append(_row("identity", "typed by the applicant" if typed else None, "self_declared" if typed else "unknown",
                        None, "to be proven by the PAN and Aadhaar cards" if typed else "unknown", "name, date of birth, address"))

    # PAN
    on_card = ident.get("pan_on_card_matches")
    if src.get("pan"):
        out.append(_row("pan", src["pan"].get("source"), "sandbox", checked_at, src["pan"].get("status", "valid"),
                        "PAN holder's name"))
    elif dl and dl.get("has_pan"):
        out.append(_row("pan", dl.get("source"), _mode(dl.get("source"), sandbox), dl.get("at"), "verified", "PAN holder's name"))
    else:
        out.append(_row("pan", "PAN card (uploaded, OCR)", "uploaded", (docs.get("pan") or {}).get("uploaded_at"),
                        {True: "verified", False: "failed", None: "not checked"}[on_card], "PAN on the card vs the PAN entered"))

    # credit bureau
    b = ev["profile"]["bureau"]
    provider = b.get("source")
    mode = "uploaded" if b.get("status") == "applicant_report" else "self_declared" if b.get("status") == "self_declared" \
        else _mode(provider, sandbox)
    out.append(_row("credit_bureau", provider, mode, b.get("pulled_at") or (checked_at if sandbox else None),
                    BUREAU_STATUS.get(b.get("status"), "verified" if b.get("score") else "unknown"),
                    "credit score, accounts, existing EMIs", reference=b.get("reference"),
                    report_date=b.get("report_date")))

    # bank statement: Account Aggregator (sandbox) or an uploaded statement (real)
    bank = ev["bank"]
    shared = bank.get("accounts_shared") or []
    uploaded = "uploaded statement" in shared
    out.append(_row("bank_statement",
                    ("uploaded bank statement (OCR + transaction tagger)" if uploaded else
                     "sandbox-account-aggregator" if sandbox else "account aggregator") if bank.get("available") else None,
                    ("uploaded" if uploaded else "sandbox" if sandbox else "live") if bank.get("available") else "not_connected",
                    (docs.get("bankStatement") or {}).get("uploaded_at") if uploaded else (checked_at if bank.get("available") else None),
                    f"read: {len(bank.get('months') or [])} months" if bank.get("available") else "not shared",
                    "salary credits, EMIs, bounces, cash deposits", model_version=bank.get("model_version")))

    # income tax and payroll (sandbox stand-ins; real mode reads the uploaded ITR)
    if ev["income"]["employment"] == "selfEmployed":
        itr = src.get("itr")
        if itr:
            out.append(_row("income_tax", itr.get("source"), "sandbox", checked_at, "verified", "annual income"))
        else:
            d = docs.get("itr") or {}
            out.append(_row("income_tax", "ITR (uploaded, OCR)" if d else None, "uploaded" if d else "not_connected",
                            d.get("uploaded_at"), ev["documents"].get("itr", {}).get("verdict") or "not uploaded", "annual income"))
    if ev["income"].get("payroll_net_pay") is not None:
        out.append(_row("payroll", "sandbox payroll record", "sandbox", checked_at, "verified", "net pay, to confirm the slip"))

    # sanctions and PEP lists
    lists = ev["aml"]["lists"]
    out.append(_row("sanctions_lists", "UN Security Council consolidated list" + (" + sandbox PEP list" if lists.get("pep_list") else ""),
                    "live" if lists.get("un_list") else "not_connected", checked_at,
                    "screened" if lists.get("un_list") else "not loaded", "name and date of birth screening",
                    list_dates={k: v for k, v in lists.items() if v}))

    # selfie, liveness, face match
    s, fm = ident.get("selfie"), ident.get("face_match")
    out.append(_row("selfie_and_face_match", "in-app live selfie (liveness + face match models)" if s else None,
                    "uploaded" if s else "not_connected", (s or {}).get("at"),
                    "not taken" if not s else "failed liveness" if not s.get("live") else
                    "verified" if fm and fm.get("match") else "face match failed" if fm else "liveness passed; face match pending",
                    "the applicant is a live person and the ID's holder"))

    # documents the applicant uploaded
    for slot, d in sorted(ev["documents"].items()):
        out.append(_row("documents", f"{slot} (uploaded: OCR, classifier, field rules)", "uploaded",
                        (docs.get(slot) or {}).get("uploaded_at"), d.get("verdict") or ("accepted" if d.get("accepted") else "not accepted"),
                        "type, applicant match, editing signs, reuse", document=slot,
                        verification_score=d.get("verification_score"), type_confidence=d.get("confidence")))

    # duplicate lookup on the PAN
    out.append(_row("same_pan_lookup", "this engine's applications", "live" if ev.get("same_pan") is not None else "sandbox",
                    checked_at, f"checked: {len(ev['same_pan'])} other application(s)" if ev.get("same_pan") is not None
                    else "not checked", "duplicate applications on one PAN"))

    # property (final stage)
    pr = ev.get("property")
    if pr:
        a = pr["avm"]
        out.append(_row("property_valuation", "valuation model (comparable listings + circle rates)", "model", checked_at,
                        f"confidence {a.get('confidence')}", "market value for the LTV", model_version=a.get("model_version")))
        land = pr["land_records"]
        out.append(_row("land_records", land.get("source"), _mode(land.get("source"), sandbox),
                        checked_at if land.get("source") else None, land.get("title"), "title, encumbrance, CERSAI charge",
                        encumbrance=land.get("encumbrance_certificate"), cersai=land.get("cersai")))
        deed = pr.get("deed")
        out.append(_row("sale_deed", f"sale deed (uploaded, read by {deed.get('engine')})" if deed else None,
                        "uploaded" if deed else "not_connected", (docs.get("saleDeed") or {}).get("uploaded_at"),
                        ("needs a lawyer" if deed.get("flag") == "refer" else "read, no issue found") if deed else "not uploaded",
                        "owner's name, chain of title, charges"))
    return out


def summary(rows: list[dict]) -> dict:
    """How many sources were verified, missing or failed: the record's data-quality line."""
    bad = ("failed", "not_connected", "not loaded", "not shared", "not taken", "not uploaded")
    missing = [r["source"] for r in rows if r["status"] in bad or r["mode"] == "not_connected"]
    return {"sources": len(rows), "unavailable_or_failed": sorted(set(missing))}
