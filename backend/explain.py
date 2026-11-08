"""One decision record, explained for an officer or auditor: GET /v1/staff/records/{seq}/explain.

Everything comes from the record itself (nothing is looked up again), plus two checks made now:
the record's own hash, and a replay of its decision under its own policy version.
"""
from __future__ import annotations

from . import policy as pol, provenance, risk
from .ledger import digest


def _facts(rec: dict, gate: str) -> dict:
    return next((g["facts"] for g in rec["gates"] if g["gate"] == gate), {})


def loan_facts(rec: dict) -> dict:
    """Loan amount, income, existing EMI, FOIR and LTV: shown for every application."""
    ev, aff = rec["evidence"], _facts(rec, "affordability")
    req = ev["request"]
    return {"product": req["product"], "property_type": req.get("property_type", "residential"),
            "amount_asked": req["amount"], "amount_offered": rec["offer"]["amount"], "years": req["years"],
            "rate_pct": rec["offer"]["rate_pct"], "emi": rec["offer"]["emi"],
            "income_used": ev["income"]["income_used"], "income_source": ev["income"]["income_source"],
            "income_proven": ev["income"]["proven"], "existing_emi": ev["income"]["existing_emi"],
            "foir_after": aff.get("foir_after"), "foir_cap": aff.get("foir_cap"),
            "property_value": req["property_value"], "value_for_ltv": rec["offer"].get("value_for_ltv"),
            "ltv": aff.get("ltv"), "ltv_cap": aff.get("ltv_cap"), "limited_by": rec["offer"].get("limited_by")}


def verification(ev: dict) -> dict:
    ident = ev["identity"]
    s, fm = ident.get("selfie") or {}, ident.get("face_match") or {}
    return {"identity": {"live_selfie": bool(s.get("live")), "face_match": fm.get("match"),
                         "face_similarity": fm.get("similarity"), "pan_name_score": ident.get("pan_name_score"),
                         "document_name_scores": ident.get("document_name_scores"),
                         "pan_on_card_matches": ident.get("pan_on_card_matches")},
            "documents": {slot: {"verdict": d.get("verdict"), "score": d.get("verification_score"), "type": d.get("type"),
                                 "type_confidence": d.get("confidence"), "problems": d.get("problems")}
                          for slot, d in ev["documents"].items()},
            "required_documents": ev["required_documents"],
            "aml": {"lists": ev["aml"]["lists"], "hits": len(ev["aml"]["hits"]), "cleared": len(ev["aml"]["cleared"]),
                    "flag": ev["aml"]["flag"]}}


def model_outputs(ev: dict) -> dict:
    m = ev["models"]
    out = {"scorecard": {k: m["scorecard"].get(k) for k in ("model_version", "score", "grade", "probability_of_default")},
           "challenger_shadow": {k: m["challenger"].get(k) for k in ("model_version", "score", "grade")},
           "fraud": {k: m["fraud"].get(k) for k in ("model_version", "fraud_probability", "flag", "top_reasons")},
           "anomaly": {k: m["anomaly"].get(k) for k in ("model_version", "anomaly_score", "alert", "flag")},
           "transaction_tagger": ev["bank"].get("model_version")}
    if ev.get("property"):
        a = ev["property"]["avm"]
        out["property_avm"] = {k: a.get(k) for k in ("model_version", "estimate", "low", "high", "confidence")}
        if ev["property"].get("deed"):
            dd = ev["property"]["deed"]
            out["deed_reader"] = {k: dd.get(k) for k in ("model_version", "engine", "grounded", "flag")}
    return out


def explain(rec: dict, chain: dict, replayed: dict) -> dict:
    ev = rec["evidence"]
    sources = ev.get("data_sources") or provenance.sources(ev)
    body = {k: v for k, v in rec.items() if k not in ("prev_hash", "hash")}
    p = pol.for_version(rec.get("policy_version"))
    return {
        "application_id": rec["application_id"], "record": rec["seq"], "stage": rec["stage"], "decided_at": rec["at"],
        "versions": {"engine": rec["engine_version"], "policy": rec["policy_version"],
                     "policy_effective_from": p.get("effective_from"), "models": rec["model_versions"]},
        "outcome": rec["outcome"],
        "loan": loan_facts(rec),
        "data_sources": sources, "data_quality": provenance.summary(sources),
        "verification": verification(ev),
        "model_outputs": model_outputs(ev),
        "risk": rec.get("risk_summary") or risk.summary(ev, rec),
        "gates": [{"gate": g["gate"], "outcome": g["outcome"],
                   "reasons": [{"code": r["code"], "outcome": r["outcome"], "why": r["officer_text"], "check": r["officer_check"],
                                "values": r["values"]} for r in g["reasons"]], "notes": g["notes"]} for g in rec["gates"]],
        "policy_rules": {"compliance": _facts(rec, "compliance").get("rules", []), "policy": {k: p[k] for k in
                         ("version", "foir_caps", "ltv_home_loan", "ltv_lap", "bureau_reject_below", "bureau_refer_below",
                          "auto_sanction_limit") if k in p}},
        "customer_reasons": rec["customer_reasons"],
        "audit": {"record_hash": rec["hash"], "prev_hash": rec["prev_hash"],
                  "record_hash_ok": digest(body, rec["prev_hash"]) == rec["hash"],
                  "chain": chain, "replay": replayed,
                  "reproducible": bool(replayed.get("same_decision") and replayed.get("models_same"))},
    }
