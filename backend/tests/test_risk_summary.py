"""PS2 review, problem 4: every application's risk score, category, contributing factors, model
confidence and decision reason; and no claim that the models are production-ready."""
import copy

import pytest

from backend import pipeline, risk

FACTORS = ["credit history", "income", "existing EMIs", "FOIR", "LTV", "bank behaviour", "employment stability", "property risk"]


@pytest.fixture
def rec(ravi):
    return copy.deepcopy(next(r for r in ravi["records"] if r["stage"] == "final"))


def test_each_record_has_its_risk_summary(rec):
    r = rec["risk_summary"]
    sc = rec["evidence"]["models"]["scorecard"]
    assert r["score"] == sc["score"] and r["grade"] == sc["grade"] and r["category"] == risk.CATEGORY[sc["grade"]]
    assert [f["factor"] for f in r["factors"]] == FACTORS
    assert all(f["rating"] for f in r["factors"])
    assert r["scorecard_points"]["highest"] and r["scorecard_points"]["lowest"]
    assert r["decision"] == {"outcome": "approve", "reasons": [], "summary": "approved: every gate passed"}
    assert r["fraud_signals"]["rule"].startswith("a model score alone only refers")


def test_model_confidence_and_its_honest_limits(rec):
    c = rec["risk_summary"]["model_confidence"]
    assert c["level"] in ("high", "medium", "low") and 0 <= c["input_completeness"] <= 1
    assert c["champion_score"] == rec["evidence"]["models"]["scorecard"]["score"]
    v = c["validation"]
    assert v["production_ready"] is False
    assert v["scorecard"]["test_auc"] == 0.5945 and v["challenger"]["test_auc"] == 0.6206
    assert "not mortgages" in v["note"]


def test_without_a_credit_report_the_confidence_is_low(rec):
    ev = rec["evidence"]
    ev["profile"]["bureau"].update(score=None, status="not_pulled")
    assert risk.model_confidence(ev)["level"] == "low"


def test_a_refer_names_its_reasons(rec):
    ev = rec["evidence"]
    ev["bank"]["bounces"] = 3
    d = pipeline.decide(ev)
    r = risk.summary(ev, d)
    assert r["decision"]["outcome"] == "refer" and "R-AFF-02" in r["decision"]["summary"]
    assert next(f for f in r["factors"] if f["factor"] == "bank behaviour")["rating"] == "weak"


def test_the_summary_never_changes_the_decision(rec):
    ev = rec["evidence"]
    d = pipeline.decide(ev)
    before = copy.deepcopy(d)
    risk.summary(ev, d)
    assert d == before and pipeline.decide(ev) == before
