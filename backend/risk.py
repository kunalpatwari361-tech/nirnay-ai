"""The risk summary of one application: score, category, the factors behind it, how far the
model can be trusted on this case, and the decision's reasons.

It explains a decision; it never makes one. The gates decide (backend/gates.py), and nothing
here changes an outcome. It is written into each decision record (risk_summary) and shown to
officers by GET /v1/staff/records/{seq}/explain.

The credit models were trained and tested on Indian vehicle-loan data (L&T), not on mortgage
outcomes, and their test AUCs are low (read from the models' own metrics files below). So the
score is one input for an officer, not a validated probability of default for a home loan.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATEGORY = {"A": "low", "B": "low", "C": "medium", "D": "high", "E": "high"}


@lru_cache(maxsize=1)
def validation() -> dict:
    """What the credit models were validated on, from their metrics files."""
    out = {}
    for name, path in (("scorecard", "models/credit_scorecard/reports/metrics.json"),
                       ("challenger", "models/credit_challenger/reports/metrics.json")):
        try:
            m = json.loads((ROOT / path).read_text(encoding="utf-8"))
            out[name] = {"version": m.get("version"), "dataset": m.get("dataset_title"), "split": m.get("split"),
                         "test_auc": (m.get("test") or {}).get("auc")}
        except (OSError, ValueError):
            out[name] = {"version": None, "dataset": None, "split": None, "test_auc": None}
    out["note"] = ("Trained and tested on Indian vehicle loans, not mortgages, with a test AUC near 0.6: not validated "
                   "for home-loan default. The score guides an officer; the policy rules and gates make the decision.")
    out["production_ready"] = False
    return out


def _rate(value, good, fair, watch, higher_is_better=True) -> str:
    if value is None:
        return "unknown"
    v = value if higher_is_better else -value
    g, f, w = (good, fair, watch) if higher_is_better else (-good, -fair, -watch)
    return "good" if v >= g else "fair" if v >= f else "watch" if v >= w else "weak"


def _gate(d: dict, name: str) -> dict:
    return next((g for g in d["gates"] if g["gate"] == name), {"facts": {}, "reasons": [], "outcome": "pass"})


def factors(ev: dict, d: dict) -> list[dict]:
    b, inc, bank = ev["profile"]["bureau"], ev["income"], ev["bank"]
    aff = _gate(d, "affordability")["facts"]
    out = []
    score = b.get("score")
    status = b.get("status")
    out.append({"factor": "credit history", "value": score, "detail": f"bureau status {status}",
                "rating": "unknown" if status in ("not_pulled", "unavailable") else "thin" if not score
                else _rate(score, 750, 700, 650)})
    income = inc["income_used"]
    out.append({"factor": "income", "value": income, "detail": inc["income_source"],
                "rating": "good" if inc["proven"] else "weak"})
    share = inc["existing_emi"] / income if income else None
    out.append({"factor": "existing EMIs", "value": inc["existing_emi"],
                "detail": f"{share:.0%} of income" if share is not None else "no income",
                "rating": _rate(share, 0.15, 0.30, 0.45, higher_is_better=False)})
    foir, cap = aff.get("foir_after"), aff.get("foir_cap")
    out.append({"factor": "FOIR", "value": foir, "detail": f"cap {cap}",
                "rating": "unknown" if foir is None or not cap else _rate(foir / cap, 0.8, 0.95, 1.0, higher_is_better=False)})
    ltv, lcap = aff.get("ltv"), aff.get("ltv_cap")
    out.append({"factor": "LTV", "value": ltv, "detail": f"cap {lcap}",
                "rating": "unknown" if ltv is None or not lcap else _rate(ltv / lcap, 0.8, 0.95, 1.0, higher_is_better=False)})
    if bank.get("available"):
        cash = bank.get("cash_deposit_share_of_credits")
        rating = "weak" if bank["bounces"] >= 2 or (cash or 0) >= 0.5 else "watch" if bank["bounces"] or (cash or 0) >= 0.3 else "good"
        detail = f"{bank['bounces']} bounces, cash deposits {cash:.0%} of credits" if cash is not None else f"{bank['bounces']} bounces"
    else:
        rating, detail = "unknown", "no bank statement"
    out.append({"factor": "bank behaviour", "value": bank.get("bounces"), "detail": detail, "rating": rating})
    if inc["employment"] == "salaried":
        months = bank.get("salary_months") or 0
        out.append({"factor": "employment stability", "value": months,
                    "detail": f"salary credited in {months} of the months shared; employer {ev['profile'].get('employer')}",
                    "rating": "unknown" if not bank.get("available") else _rate(months, 12, 6, 3)})
    else:
        out.append({"factor": "employment stability", "value": None,
                    "detail": "self-employed: " + ("income proven by the ITR" if inc["proven"] else "income not proven"),
                    "rating": "fair" if inc["proven"] else "weak"})
    pr = ev.get("property")
    if pr:
        conf = pr["avm"].get("confidence")
        title = pr["land_records"].get("title")
        prop = _gate(d, "property")
        rating = "good" if prop["outcome"] == "pass" else "weak" if conf == "low" or title not in ("clear", "not_connected") else "watch"
        out.append({"factor": "property risk", "value": conf,
                    "detail": f"valuation confidence {conf}, title {title}, type {ev['request'].get('property_type', 'residential')}",
                    "rating": rating})
    else:
        out.append({"factor": "property risk", "value": None, "detail": "assessed at the final stage, with the property's details",
                    "rating": "not yet assessed"})
    return out


def model_confidence(ev: dict) -> dict:
    sc, ch = ev["models"]["scorecard"], ev["models"]["challenger"]
    points = sc.get("points") or []
    have = [p for p in points if p.get("value") is not None]
    completeness = round(len(have) / len(points), 2) if points else None
    gap = abs(sc["score"] - ch["score"])
    agree = sc["grade"] == ch["grade"]
    provisional = ev["profile"]["bureau"].get("status") in ("not_pulled", "unavailable")
    if provisional or (completeness is not None and completeness < 0.7) or gap > 60:
        level = "low"
    elif agree and (completeness or 0) >= 0.9 and gap <= 30:
        level = "high"
    else:
        level = "medium"
    return {"level": level, "input_completeness": completeness, "missing_inputs": [p["input"] for p in points if p.get("value") is None],
            "champion_score": sc["score"], "challenger_score": ch["score"], "score_gap": gap, "grades_agree": agree,
            "provisional": provisional, "probability_of_default": sc.get("probability_of_default"),
            "validation": validation()}


def summary(ev: dict, d: dict) -> dict:
    sc = ev["models"]["scorecard"]
    reasons = [{"gate": g["gate"], "code": r["code"], "outcome": r["outcome"], "why": r["officer_text"]}
               for g in d["gates"] for r in g["reasons"]]
    top = sorted(sc.get("points") or [], key=lambda p: p.get("points", 0))
    return {"score": sc["score"], "grade": sc["grade"], "category": CATEGORY.get(sc["grade"], "unknown"),
            "factors": factors(ev, d),
            "scorecard_points": {"base": sc.get("base_score"),
                                 "lowest": [{"input": p["input"], "bin": p["bin"], "points": p["points"]} for p in top[:3]],
                                 "highest": [{"input": p["input"], "bin": p["bin"], "points": p["points"]} for p in top[-3:][::-1]]},
            "model_confidence": model_confidence(ev),
            "fraud_signals": {"fraud_probability": ev["models"]["fraud"].get("fraud_probability"),
                              "fraud_flag": ev["models"]["fraud"].get("flag"), "anomaly_alert": ev["models"]["anomaly"].get("alert"),
                              "rule": "a model score alone only refers; it never rejects"},
            "decision": {"outcome": d["outcome"], "reasons": reasons,
                         "summary": "approved: every gate passed" if d["outcome"] == "approve" else
                         f"{d['outcome']}: " + "; ".join(sorted({r['code'] for r in reasons if r['outcome'] == d['outcome']}))}}
