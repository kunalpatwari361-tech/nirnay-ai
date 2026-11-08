"""The decision gates. Each is a pure function of the evidence and the policy table.

Gates never call a model or a data source; the pipeline gathers everything first. So the
same evidence always gives the same decision, and an auditor can replay any record.
Outcomes: pass < refer < reject. The most severe gate result wins.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import policy as pol
from .reasons import REASONS

SEVERITY = {"pass": 0, "refer": 1, "reject": 2}


class Gate:
    def __init__(self, name: str):
        self.name, self.outcome, self.reasons, self.notes, self.facts = name, "pass", [], [], {}

    def refer(self, code: str, **values):
        self._add(code, "refer", values)

    def reject(self, code: str, **values):
        self._add(code, "reject", values)

    def _add(self, code: str, outcome: str, values: dict):
        assert code in REASONS, code
        same = next((r for r in self.reasons if r["code"] == code), None)
        if same is not None:          # one entry per reason; later findings add their values
            same["values"].update({k: str(v) for k, v in values.items()})
            if SEVERITY[outcome] > SEVERITY[same["outcome"]]:
                same["outcome"] = outcome
            if SEVERITY[outcome] > SEVERITY[self.outcome]:
                self.outcome = outcome
            return
        self.reasons.append({"code": code, "outcome": outcome, "values": {k: str(v) for k, v in values.items()},
                             "officer_text": REASONS[code].officer_text, "officer_check": REASONS[code].officer_check})
        if SEVERITY[outcome] > SEVERITY[self.outcome]:
            self.outcome = outcome

    def to_dict(self) -> dict:
        return {"gate": self.name, "outcome": self.outcome, "reasons": self.reasons, "notes": self.notes, "facts": self.facts}


def _age(dob: str, day: date) -> float:
    return (day - date.fromisoformat(dob)).days / 365.25


# --------------------------------------------------------------------------- 1 identity

def identity(ev: dict) -> Gate:
    g = Gate("identity")
    s = ev["identity"]["selfie"]
    if not s:
        g.refer("R-KYC-01")
        g.notes.append("no selfie taken")
    elif not s["face_found"]:
        g.refer("R-KYC-01")
        g.notes.append("no face in the selfie")
    elif not s["live"]:
        g.refer("R-KYC-01", liveness=s["liveness"]["score"])
        if s.get("method") == "live" and s.get("problems"):
            g.notes.append("live selfie failed: " + "; ".join(s["problems"]))
        else:
            g.notes.append("liveness failed: possible photo or screen")
    elif s.get("method") != "live" and not ev["sandbox"]:
        g.refer("R-KYC-01")                 # real mode: a photo alone can be an old picture; the live check can't
        g.notes.append("the selfie wasn't taken live in the app (no head-turn check): do a video KYC call")
    if s and s.get("method") == "live":
        g.facts["live_selfie"] = {"head_turn": s["head_turn"]["direction"], "steps": s["steps"]}
    fm = ev["identity"]["face_match"]
    if fm is None:
        if ev["sandbox"]:
            g.notes.append("face match not run: the sandbox has no UIDAI eKYC photo and no ID document photo with a face")
        else:
            g.refer("R-KYC-01")
            g.notes.append("face match not run: no reference photo")
    elif not fm["match"]:
        g.refer("R-KYC-01", similarity=fm["similarity"])
    g.facts["face_match_similarity"] = fm and fm["similarity"]
    g.facts["pan_name_score"] = ev["identity"]["pan_name_score"]
    if ev["identity"]["pan_name_score"] is not None and ev["identity"]["pan_name_score"] < 85:
        g.refer("R-KYC-02", score=ev["identity"]["pan_name_score"])
    low = {k: v for k, v in ev["identity"]["document_name_scores"].items() if v < 80}
    if low:
        g.refer("R-KYC-02", documents=",".join(sorted(low)))
    if ev["identity"]["pan_on_card_matches"] is False:
        g.refer("R-KYC-02", documents="pan")
        g.notes.append("PAN on the card differs from the PAN entered")
    typed = ev["identity"].get("typed_vs_digilocker") or {}
    if typed.get("name_score") is not None and typed["name_score"] < 80:
        g.refer("R-KYC-02", documents="digilocker")
        g.notes.append("the name typed in differs from the name in DigiLocker")
    if typed.get("dob_matches") is False:
        g.notes.append("the date of birth typed in differs from DigiLocker's; DigiLocker's is used")
    if ev["identity"].get("digilocker_mobile_matches") is False:
        g.notes.append("the mobile number on the DigiLocker account isn't the one the applicant signed in with")
    return g


# --------------------------------------------------------------------------- 2 AML

def aml(ev: dict) -> Gate:
    g = Gate("aml")
    a = ev["aml"]
    g.facts.update(lists=a["lists"], hits=len(a["hits"]), cleared=len(a["cleared"]))
    if a["flag"] == "refer":
        g.refer(a["reason_code"])
    if a["cleared"]:
        g.notes.append(f"{len(a['cleared'])} namesake(s) cleared automatically: different date of birth")
    return g


# --------------------------------------------------------------------------- 3 fraud and documents

def fraud_documents(ev: dict, p: dict) -> Gate:
    g = Gate("fraud_documents")
    m = ev["models"]
    if m["fraud"]["flag"] == "refer":
        g.refer("R-FRD-01", probability=m["fraud"]["fraud_probability"])
    if m["anomaly"]["flag"] == "refer":
        g.refer("R-FRD-04", alert=m["anomaly"]["alert"])
    if ev["links"]["ring"]["flag"] == "refer":
        g.refer("R-FRD-05", linked=ev["links"]["ring"]["linked_applications"])
    g.facts.update(fraud_probability=m["fraud"]["fraud_probability"], anomaly_alert=m["anomaly"]["alert"])
    docs = ev["documents"]
    for slot in ev["required_documents"]:
        d = docs.get(slot)
        if d is None:
            g.refer("R-DOC-04", document=slot)
        elif not d["accepted"]:
            g.refer(d["reason_code"] or "R-DOC-01", document=slot)
    for slot, d in docs.items():
        # A document that failed verification rejects: edited, reused from another applicant, or
        # not the applicant's (backend/intake.py verify). Records from before verdicts have none.
        if d.get("verdict") == "failed" or d.get("failed_before"):
            g.reject("R-DOC-05", document=slot)
            g.notes.append(f"{slot}: failed verification: " + "; ".join(d.get("problems") or ["an earlier upload failed"]))
        if d["tamper_signals"]:
            g.refer("R-DOC-03", document=slot)
            g.notes.append(f"{slot}: " + "; ".join(d["tamper_signals"]))
        if d["reused_from"]:
            g.refer("R-FRD-06", document=slot)
    if ev.get("same_pan") is not None and "duplicate_window_days" in p:
        _same_pan(g, ev, p)
    share = ev["bank"].get("cash_deposit_share_of_credits")
    if "cash_deposit_share_refer_at" in p and share is not None:
        g.facts["cash_deposit_share"] = share
        if share >= p["cash_deposit_share_refer_at"]:
            g.refer("R-FRD-08", share=round(share, 2))
    if ev["bank"].get("entries_needing_review"):
        g.notes.append(f"{ev['bank']['entries_needing_review']} bank entries the transaction tagger wasn't sure of")
    inc = ev["income"]
    if inc["employment"] == "salaried" and inc["slip_net_pay"] and inc["bank_salary"]:
        over = inc["slip_net_pay"] / inc["bank_salary"] - 1
        g.facts["slip_over_bank"] = round(over, 3)
        if over > p["slip_over_bank_tolerance"]:
            payroll = inc["payroll_net_pay"]
            if payroll and inc["slip_net_pay"] / payroll - 1 > p["slip_over_bank_tolerance"]:
                g.reject("R-FRD-03", slip=inc["slip_net_pay"], payroll=payroll)
                g.notes.append("salary slip net pay does not match the employer's payroll record")
            elif payroll:
                g.notes.append("slip is above bank credits, but the payroll record confirms it")
            else:
                g.refer("R-FRD-02", slip=inc["slip_net_pay"], bank=inc["bank_salary"])
    return g


def _same_pan(g: Gate, ev: dict, p: dict) -> None:
    """Other applications on the same PAN. The same person applying again is normal; the same PAN
    with another name or date of birth, or (within the window) another income or employer, is not."""
    from .fields import name_score
    me, day = ev["profile"], date.fromisoformat(ev["today"])
    since = day - timedelta(days=p["duplicate_window_days"])
    differs: set[str] = set()
    for o in ev["same_pan"]:
        if o.get("name") and me.get("name") and name_score(o["name"], me["name"]) < 80:
            differs.add("name")
        if o.get("dob") and me.get("dob") and o["dob"] != me["dob"]:
            differs.add("date of birth")
        recent = date.fromisoformat(o["created_at"][:10]) >= since
        if recent and o.get("monthly_income") and me.get("monthly_income"):
            if abs(o["monthly_income"] - me["monthly_income"]) > p["duplicate_income_tolerance"] * me["monthly_income"]:
                differs.add("income")
        if recent and o.get("employer") and me.get("employer") and name_score(o["employer"], me["employer"]) < 80:
            differs.add("employer")
    g.facts["same_pan_applications"] = len(ev["same_pan"])
    if differs:
        g.refer("R-FRD-07", applications=len(ev["same_pan"]), differs=", ".join(sorted(differs)))
        g.notes.append(f"same PAN on {len(ev['same_pan'])} other application(s) with a different {', '.join(sorted(differs))}")
    elif ev["same_pan"]:
        g.notes.append(f"same PAN on {len(ev['same_pan'])} earlier application(s) with the same details: a re-application")


# --------------------------------------------------------------------------- 4 policy

def policy_rules(ev: dict, p: dict) -> Gate:
    g = Gate("policy")
    prof, req = ev["profile"], ev["request"]
    age = _age(prof["dob"], date.fromisoformat(ev["today"]))
    g.facts.update(age=round(age, 1), age_at_end=round(age + req["years"], 1))
    if age < p["min_age"]:
        g.reject("R-POL-01", age=int(age))
    allowed = (p.get("property_types") or {}).get(req.get("product"))
    if allowed is not None:
        ptype = req.get("property_type", "residential")
        g.facts["property_type"] = ptype
        if ptype not in allowed:
            g.reject("R-POL-03", product=req["product"], property_type=ptype)
    if age + req["years"] > p["max_age_at_end"][prof["employment"]]:
        g.refer("R-POL-02", age_at_end=int(age + req["years"]))
    b = prof["bureau"]
    score = b.get("score")
    source = str(b.get("source") or "")
    g.facts["bureau_source"] = source or None
    if b.get("status") == "self_declared":
        g.notes.append("credit score is self-declared: no bureau is connected")
    elif "staging" in source:
        g.refer("R-BUR-06")
        g.notes.append(f"credit report is test data from the provider's staging server ({source})")
    elif source.startswith("decentro"):
        g.notes.append(f"credit report pulled from the bureau with the applicant's consent ({source})")
    if b.get("status") == "unavailable":
        g.refer("R-BUR-06")
        g.notes.append(f"the credit report could not be pulled ({b.get('error') or 'bureau error'})")
    elif b.get("status") == "not_pulled":
        g.refer("R-BUR-06")
        g.notes.append("no credit bureau is connected, so the report was not pulled: an officer pulls it with PAN")
    elif b.get("status") == "applicant_report":
        # The score was read from a report the applicant uploaded (name and PAN checked). It counts
        # for the in-principle offer; an officer confirms it with the bureau before sanction. A low
        # one goes to an officer too: a misread score must never reject on its own.
        g.notes.append(f"credit score {score} read from the applicant's own {b.get('report_bureau')} report dated "
                       f"{b.get('report_date')}: not yet confirmed with the bureau")
        if score < p["bureau_reject_below"] or ev["stage"] == "final":
            g.refer("R-BUR-07", score=score)
        if p["bureau_reject_below"] <= score < p["bureau_refer_below"]:
            g.refer("R-BUR-05", score=score)
    elif not score:
        g.refer("R-BUR-04")
    elif score < p["bureau_reject_below"]:
        g.reject("R-BUR-01", score=score)
    elif score < p["bureau_refer_below"]:
        g.refer("R-BUR-05", score=score)
    if req["asked_years"] > req["years"]:
        g.notes.append(f"tenure capped at the product maximum of {req['years']} years")
    return g


# --------------------------------------------------------------------------- 5 affordability

def affordability(ev: dict, p: dict) -> tuple[Gate, pol.Eligibility]:
    g = Gate("affordability")
    req, inc, bank = ev["request"], ev["income"], ev["bank"]
    income = inc["income_used"]
    if not inc["proven"]:
        g.refer("R-AFF-04")
        g.notes.append("income not proven by a document; the declared figure was used")
    elif inc.get("declared") and inc["declared"] > 1.25 * income:
        g.notes.append(f"declared income {inc['declared']:.0f} is above what the documents show; using {income:.0f}")
    if inc["employment"] == "selfEmployed" and income and bank["avg_monthly_credits"] < p["business_credits_min_share"] * income:
        g.refer("R-AFF-03", credits=bank["avg_monthly_credits"], declared=income)
    if bank["bounces"] >= p["bounces_refer_at"]:
        g.refer("R-AFF-02", bounces=bank["bounces"])
    e = pol.eligibility(req["product"], income, inc["existing_emi"], req["property_value"], req["amount"], req["years"],
                        req.get("property_type", "residential"), p)
    if e.max_emi <= 0:
        g.reject("R-AFF-01")
    if req["product"] == "lap" and "lap_credit_cover_min" in p:
        # A LAP is repaid from the applicant's cash flow, so the bank statement must show enough coming in.
        total = e.emi + inc["existing_emi"]
        if not bank.get("available"):
            g.refer("R-AFF-05")
            g.notes.append("LAP: no bank statement, so the cash flow could not be assessed")
        elif total > 0:
            cover = bank["avg_monthly_credits"] / total
            g.facts["credit_cover"] = round(cover, 2)
            if cover < p["lap_credit_cover_min"]:
                g.refer("R-AFF-05", cover=round(cover, 2), minimum=p["lap_credit_cover_min"])
                g.notes.append(f"LAP: monthly bank credits are {cover:.2f}x the EMIs; the policy asks for "
                               f"{p['lap_credit_cover_min']}x")
    g.facts.update(income_used=income, income_source=inc["income_source"], existing_emi=inc["existing_emi"],
                   **{k: v for k, v in e.to_dict().items()})
    return g, e


# --------------------------------------------------------------------------- 6 risk grade

NOT_PULLED = ("not_pulled", "unavailable")   # no credit report: no bureau connected, or the bureau failed
UNCONFIRMED = NOT_PULLED + ("applicant_report",)   # ... or only the applicant's own report, not yet confirmed


def risk_grade(ev: dict) -> Gate:
    g = Gate("risk_grade")
    sc, ch = ev["models"]["scorecard"], ev["models"]["challenger"]
    # Without a credit report the scorecard is missing its main input (the bureau score counts as
    # missing), so its grade is provisional: a low one goes to an officer, who grades the case once
    # the report is pulled. It never rejects on its own.
    provisional = ev["profile"]["bureau"].get("status") in NOT_PULLED
    g.facts.update(score=sc["score"], grade=sc["grade"], challenger_score=ch["score"], challenger_grade=ch["grade"],
                   provisional=provisional)
    if provisional:
        g.notes.append(f"grade {sc['grade']} is provisional: worked out without a credit report")
        if sc["grade"] in ("C", "D", "E"):
            g.refer("R-RSK-01", grade=sc["grade"])
    elif sc["grade"] == "C":
        g.refer("R-RSK-01", grade="C")
    elif sc["grade"] in ("D", "E") and ev["profile"]["bureau"].get("status") in UNCONFIRMED:
        g.refer("R-RSK-01", grade=sc["grade"])    # from the applicant's own report: an officer confirms it first
    elif sc["grade"] in ("D", "E"):
        g.reject("R-RSK-02", grade=sc["grade"])
    g.notes.append(f"challenger (shadow, not used): {ch['score']} grade {ch['grade']}")
    return g


# --------------------------------------------------------------------------- 7 authority

def authority(e: pol.Eligibility, p: dict) -> Gate:
    g = Gate("authority")
    if e.eligible > p["auto_sanction_limit"]:
        g.refer("R-AUT-01", amount=e.eligible)
    return g


# --------------------------------------------------------------------------- 8 property (final stage)

def property_gate(ev: dict, p: dict | None = None) -> tuple[Gate, float | None]:
    g = Gate("property")
    pr = ev["property"]
    p = p or {}
    avm = pr["avm"]
    g.facts.update(estimate=avm["estimate"], low=avm["low"], high=avm["high"], confidence=avm["confidence"],
                   circle_value=avm["circle_value"], declared=avm["declared_price"])
    if avm["confidence"] == "low":
        g.refer("R-PRP-02")
        g.notes.append("valuation model unsure (few comparable listings or a wide range)")
    if avm["reason_code"] == "R-PRP-03":
        g.refer("R-PRP-03", declared=avm["declared_price"], high=avm["high"])
    if avm["reason_code"] == "R-PRP-04":
        g.refer("R-PRP-04", declared=avm["declared_price"], circle=avm["circle_value"])
    land = pr["land_records"]
    g.facts["land_records"] = land["title"]
    if land["title"] == "not_connected":
        g.notes.append(land["detail"])
        if pr["deed"] is None:
            g.refer("R-PRP-01")
            g.notes.append("no sale deed uploaded, so the title could not be checked")
    elif land["title"] != "clear":
        g.refer("R-PRP-01")
        g.notes.append(f"land records: {land['detail']}")
    deed = pr["deed"]
    if deed is None:
        g.notes.append("no sale deed uploaded; title checked from land records only")
    else:
        g.facts.update(deed_engine=deed["engine"], deed_grounded=deed["grounded"], deed_issues=len(deed["issues"]))
        if deed["flag"] == "refer":
            g.refer("R-PRP-01")
            g.notes += [f"deed: {i['kind']}: \"{i['quote'][:120]}\"" for i in deed["issues"]]
            if deed["unverified"]:
                g.notes.append(f"deed: {len(deed['unverified'])} fact(s) could not be found in the deed text")
        if pr["deed_buyer_score"] is not None and pr["deed_buyer_score"] < 80:
            g.refer("R-PRP-01")
            g.notes.append("deed: buyer's name differs from the applicant")
    if "property_types" in p:
        _product_property(g, ev, pr)
    photos = pr["photos"]
    if not photos:
        g.refer("R-PRP-02")
        g.notes.append("no property photos")
    for ph in photos:
        problems = [k for k in ("not_geotagged", "old", "reused") if ph.get(k)]
        if problems:
            g.refer("R-PRP-02")
            g.notes.append(f"photo '{ph['label']}': {', '.join(problems)}")
    return g, avm["value_for_ltv"]


def _product_property(g: Gate, ev: dict, pr: dict) -> None:
    """policy-v2: a LAP mortgages a property the applicant already owns, so ownership and existing
    charges are checked; a commercial property is valued by an empanelled valuer."""
    ptype = ev["request"].get("property_type", "residential")
    g.facts["property_type"] = ptype
    if ptype == "commercial":
        g.refer("R-PRP-02")
        g.notes.append("commercial property: the valuation model learnt from residential listings, so a valuer values it")
    if ev["request"]["product"] != "lap":
        return
    owner = pr.get("deed_buyer_score")
    g.facts["owner_name_score"] = owner
    if owner is None:
        g.refer("R-PRP-05")
        g.notes.append("LAP: no sale deed in the applicant's name, so ownership isn't shown")
    elif owner < 80:
        g.refer("R-PRP-05")
        g.notes.append("LAP: the deed names someone else as the owner")
    land = pr["land_records"]
    ec, cersai = land.get("encumbrance_certificate"), land.get("cersai")
    g.facts.update(encumbrance=ec, cersai=cersai)
    if ec is None or cersai is None:
        g.refer("R-PRP-06")
        g.notes.append("LAP: the encumbrance certificate and CERSAI search aren't connected, so existing charges weren't checked")
    elif not (str(ec).lower().startswith("nil") and "no charge" in str(cersai).lower()):
        g.refer("R-PRP-06")
        g.notes.append(f"LAP: existing charge: {ec}; CERSAI: {cersai}")


# --------------------------------------------------------------------------- compliance gate

AADHAAR_NUMBER = re.compile(r"(?<![\w.])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![\w.])")

def compliance(ev: dict, gates: list[Gate], offer: dict, stage: str, p: dict | None = None) -> Gate:
    """Regulation as code. Every rule names its source; one failure holds the decision."""
    g = Gate("compliance")
    rules = []

    def rule(rid: str, source: str, ok: bool, detail: str = ""):
        rules.append({"rule": rid, "source": source, "ok": bool(ok), "detail": detail})

    c = ev["consents"]
    rule("CMP-01 consent before data pull", "DPDP Act 2023 s.6; RBI Digital Lending Directions 2025 (consent for data)",
         c and all(c["given"].get(k) for k in ("bureau", "aa", "kyc", "privacy")))
    rule("CMP-02 KYC completed", "RBI KYC Directions 2016 (as amended): customer identification",
         ev["identity"]["selfie"] is not None and bool(ev["profile"].get("pan_masked")))
    rule("CMP-03 sanctions screening ran", "PMLA 2002 and RBI KYC Directions: screening against UN lists",
         ev["aml"]["lists"].get("un_list") is not None)
    if offer["product"] == "homeLoan" and offer["outcome"] != "reject" and offer["value_for_ltv"]:
        ltv = offer["amount"] / offer["value_for_ltv"]
        cap = pol.ltv_cap("homeLoan", offer["amount"])
        rule("CMP-04 LTV within RBI tiers", "RBI Housing Finance Companies Directions: LTV limits for housing loans",
             ltv <= cap + 1e-9, f"{ltv:.3f} vs {cap}")
    rule("CMP-05 Key Facts Statement with APR", "RBI Key Facts Statement circular (2024); Digital Lending Directions",
         offer["outcome"] == "reject" or (offer.get("kfs") or {}).get("apr_pct") is not None)
    adverse = [r for gt in gates for r in gt.reasons]
    rule("CMP-06 every adverse outcome has a reason", "RBI Fair Practices Code for HFCs: reasons for rejection",
         offer["outcome"] == "approve" or bool(adverse))
    llm_rejects = [r for gt in gates for r in gt.reasons if gt.name == "property" and r["outcome"] == "reject"]
    rule("CMP-07 AI is not the final judge", "RBI FREE-AI framework (2025): human oversight of AI outcomes",
         not llm_rejects, "LLM findings can only refer")
    rule("CMP-08 cooling-off period stated", "RBI Digital Lending Directions 2025: cooling-off / look-up period",
         offer["outcome"] == "reject" or (offer.get("kfs") or {}).get("cooling_off_days", 0) >= 1)
    if p is not None and p["version"] != "policy-v1":
        import json
        models = ev.get("models") or {}
        rule("CMP-09 decision can be reproduced", "RBI FREE-AI framework (2025): audit trail for AI-assisted decisions",
             ev.get("policy_version") in pol.POLICIES and bool(ev.get("model_inputs"))
             and bool(models) and all(m.get("model_version") for m in models.values()),
             "policy version, model inputs and model versions are in the record")
        rule("CMP-10 no Aadhaar number in the record", "Aadhaar Act 2016 and UIDAI rules: only the masked number is kept; "
             "DPDP Act 2023: collect only what is needed",
             not AADHAAR_NUMBER.search(json.dumps(ev, ensure_ascii=False, default=str)), "Aadhaar shown as XXXX XXXX 1234 only")
    g.facts["rules"] = rules
    for r in rules:
        if not r["ok"]:
            g.refer("R-CMP-01", rule=r["rule"])
    return g
