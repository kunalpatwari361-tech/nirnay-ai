"""PS2 review, problem 6: the compliance checklist (area -> rule -> source -> test -> evidence),
and each coded rule shown failing when its condition is broken."""
import ast
import copy
from pathlib import Path

import pytest

from backend import compliance, gates, pipeline
from backend.tests.conftest import STAFF

ROOT = Path(__file__).resolve().parents[2]
AREAS = ["KYC", "AML / sanctions", "Fair Practices Code", "Consent", "Loan disclosure", "Audit", "Data privacy"]


@pytest.fixture
def ev(ravi):
    rec = next(r for r in ravi["records"] if r["stage"] == "in_principle")
    return copy.deepcopy(rec["evidence"])


def held(ev, rule, gs=None, **offer_changes):
    """Run the compliance gate with one thing broken; return whether `rule` failed and held the decision."""
    d = pipeline.decide(ev)
    offer = {**d["offer"], **offer_changes}
    if "kfs" in offer_changes and offer_changes["kfs"] is not None:
        offer["kfs"] = {**d["offer"]["kfs"], **offer_changes["kfs"]}
    g = gates.compliance(ev, gs if gs is not None else [], offer, ev["stage"], pipeline.pol.for_version(ev["policy_version"]))
    r = next(x for x in g.facts["rules"] if x["rule"].startswith(rule))
    return not r["ok"] and g.outcome == "refer" and any(x["code"] == "R-CMP-01" for x in g.reasons)


def test_the_checklist_covers_the_seven_areas_and_every_coded_rule(ravi):
    assert [a["area"] for a in compliance.CHECKLIST] == AREAS
    for a in compliance.CHECKLIST:
        assert a["source"] and a["rules"] and a["code"] and a["tests"] and a["evidence"] and a["limits"]
    rec = next(r for r in ravi["records"] if r["stage"] == "final")
    emitted = {r["rule"].split(" ")[0] for g in rec["gates"] if g["gate"] == "compliance" for r in g["facts"]["rules"]}
    assert emitted == set(compliance.coded_rules()) == {f"CMP-{i:02d}" for i in range(1, 11)}


def test_every_test_named_in_the_checklist_exists():
    for a in compliance.CHECKLIST:
        for t in a["tests"]:
            path, name = t.split("::")
            tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
            names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
            assert name in names, t
        for c in a["code"]:
            assert (ROOT / c.split(" ")[0]).exists(), c


def test_the_checklist_says_it_is_not_a_certificate(client):
    body = client.get("/v1/staff/compliance/checklist", headers=STAFF).json()
    assert "does not make the platform compliant" in body["note"] and len(body["areas"]) == 7
    assert client.get("/v1/staff/compliance/checklist").status_code == 403


def test_cmp02_no_selfie_holds_the_decision(ev):
    ev["identity"]["selfie"] = None
    assert held(ev, "CMP-02")


def test_cmp03_no_sanctions_list_holds_the_decision(ev):
    ev["aml"]["lists"] = {"un_list": None, "pep_list": None}
    assert held(ev, "CMP-03")


def test_cmp04_ltv_above_the_rbi_tier_is_held(ev):
    assert held(ev, "CMP-04", amount=2_900_000, value_for_ltv=3_000_000)     # 96.7% against the 90% tier


def test_cmp05_an_offer_without_an_apr_is_held(ev):
    assert held(ev, "CMP-05", kfs={"apr_pct": None})


def test_cmp06_an_adverse_outcome_without_a_reason_is_held(ev):
    assert held(ev, "CMP-06", outcome="refer")


def test_cmp07_an_llm_rejection_is_held(ev):
    prop = gates.Gate("property")
    prop.reject("R-PRP-01")
    assert held(ev, "CMP-07", gs=[prop])


def test_cmp08_an_offer_without_a_cooling_off_period_is_held(ev):
    assert held(ev, "CMP-08", kfs={"cooling_off_days": 0})


def test_cmp09_a_record_without_model_versions_is_held(ev):
    ev["models"]["fraud"]["model_version"] = None
    assert held(ev, "CMP-09")


def test_cmp10_an_aadhaar_number_in_the_record_is_held(ev):
    ev["profile"]["name"] = "Ravi Kumar 4821 7395 2210"          # a full Aadhaar number slipped into the evidence
    assert held(ev, "CMP-10")
    ev["profile"]["name"] = "Ravi Kumar XXXX XXXX 2210"          # masked is fine
    d = pipeline.decide(ev)
    rules = next(g for g in d["gates"] if g["gate"] == "compliance")["facts"]["rules"]
    assert next(r for r in rules if r["rule"].startswith("CMP-10"))["ok"]
