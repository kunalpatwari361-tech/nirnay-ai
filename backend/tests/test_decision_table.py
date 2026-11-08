"""PS2 review, problem 7: one decision engine, and the outcome table it follows.

  every check passes          -> approve
  information missing         -> refer
  a provider unavailable      -> refer
  a hard policy breach        -> reject
  a suspicious fraud signal   -> refer (a model score alone never rejects)

Each row starts from Ravi's real recorded evidence and changes one fact. The last tests show
all the gates run end to end, and that each record can be explained and replayed (problem 8).
"""
import copy

import pytest

from backend import pipeline, policy as pol, provenance
from backend.tests.conftest import STAFF

IN_PRINCIPLE = ["identity", "aml", "fraud_documents", "policy", "affordability", "risk_grade", "authority", "compliance"]
FINAL = ["identity", "aml", "fraud_documents", "policy", "property", "affordability", "risk_grade", "authority", "compliance"]


def _record(flow, stage):
    return next(r for r in flow["records"] if r["stage"] == stage)


@pytest.fixture
def ev(ravi):
    return copy.deepcopy(_record(ravi, "in_principle")["evidence"])


@pytest.fixture
def final_ev(ravi):
    return copy.deepcopy(_record(ravi, "final")["evidence"])


def decide(ev):
    d = pipeline.decide(ev)
    return d["outcome"], {r["code"] for g in d["gates"] for r in g["reasons"]}, d


# --------------------------------------------------------------------------- the table

def test_everything_passes_approves(ev, final_ev):
    assert decide(ev)[0] == "approve" and decide(final_ev)[0] == "approve"


@pytest.mark.parametrize("change, code", [
    (lambda e: e["documents"].pop("form16"), "R-DOC-04"),                                   # a document not uploaded
    (lambda e: e["income"].update(proven=False, income_source="self-declared, not proven"), "R-AFF-04"),
    (lambda e: e["identity"].update(selfie=None), "R-KYC-01"),                              # no selfie taken
    (lambda e: e["profile"]["bureau"].update(score=None), "R-BUR-04"),                       # no credit history
])
def test_missing_information_refers(ev, change, code):
    change(ev)
    outcome, codes, _ = decide(ev)
    assert outcome == "refer" and code in codes


def test_provider_unavailable_refers(ev, final_ev):
    bureau_down = copy.deepcopy(ev)
    bureau_down["profile"]["bureau"].update(score=None, status="unavailable", error="timeout")
    aml_down = copy.deepcopy(ev)
    aml_down["aml"] = {**aml_down["aml"], "lists": {"un_list": None, "pep_list": None}, "hits": [], "cleared": [],
                       "flag": "refer", "reason_code": "R-AML-02"}
    no_aa = copy.deepcopy(ev)
    no_aa["bank"].update(available=False, salary_months=0, avg_monthly_salary=0.0)
    no_aa["income"].update(proven=False, bank_salary=None, slip_net_pay=None)
    final_ev["property"]["land_records"] = {"source": None, "title": "not_connected", "detail": "not connected"}
    final_ev["property"]["deed"] = None
    for e, code in ((bureau_down, "R-BUR-06"), (aml_down, "R-AML-02"), (no_aa, "R-AFF-04"), (final_ev, "R-PRP-01")):
        outcome, codes, _ = decide(e)
        assert outcome == "refer" and code in codes, (code, codes)
    assert "R-CMP-01" in decide(aml_down)[1]           # screening didn't run: the compliance gate holds it too


@pytest.mark.parametrize("change, code", [
    (lambda e: e["profile"].update(dob="2007-01-01"), "R-POL-01"),                           # under 21
    (lambda e: e["profile"]["bureau"].update(score=600), "R-BUR-01"),                        # below the minimum score
    (lambda e: e["income"].update(existing_emi=60000), "R-AFF-01"),                          # EMIs already at the limit
    (lambda e: e["request"].update(property_type="commercial"), "R-POL-03"),                 # home loan on a shop
    (lambda e: e["documents"]["salarySlip"].update(verdict="failed", problems=["names an editing tool"]), "R-DOC-05"),
])
def test_a_hard_policy_breach_rejects(ev, change, code):
    change(ev)
    outcome, codes, _ = decide(ev)
    assert outcome == "reject" and code in codes


@pytest.mark.parametrize("change, code", [
    (lambda e: e["models"]["fraud"].update(flag="refer", fraud_probability=0.99), "R-FRD-01"),
    (lambda e: e["models"]["anomaly"].update(flag="refer", alert=True), "R-FRD-04"),
    (lambda e: e["links"]["ring"].update(flag="refer", linked_applications=4), "R-FRD-05"),
    (lambda e: e["bank"].update(cash_deposit_share_of_credits=0.72), "R-FRD-08"),
    (lambda e: e.update(same_pan=[{"application_id": "X", "created_at": e["today"], "name": e["profile"]["name"],
                                   "dob": "1970-01-01", "employer": None, "monthly_income": None}]), "R-FRD-07"),
])
def test_a_suspicious_signal_refers_and_never_rejects(ev, change, code):
    change(ev)
    outcome, codes, d = decide(ev)
    assert outcome == "refer" and code in codes
    fraud = next(g for g in d["gates"] if g["gate"] == "fraud_documents")
    assert all(r["outcome"] == "refer" for r in fraud["reasons"])        # a signal is never a verdict


def test_even_a_certain_fraud_score_only_refers(ev):
    ev["models"]["fraud"].update(flag="refer", fraud_probability=1.0)
    ev["models"]["anomaly"].update(flag="refer", alert=True)
    assert decide(ev)[0] == "refer"


def test_the_most_severe_gate_wins(ev):
    ev["models"]["fraud"].update(flag="refer", fraud_probability=0.9)     # refer ...
    ev["profile"]["bureau"]["score"] = 600                               # ... and reject: reject wins
    outcome, codes, _ = decide(ev)
    assert outcome == "reject" and {"R-FRD-01", "R-BUR-01"} <= codes


# --------------------------------------------------------------------------- one engine, every gate

def test_every_gate_runs_end_to_end(ravi):
    ip, fin = _record(ravi, "in_principle"), _record(ravi, "final")
    assert [g["gate"] for g in ip["gates"]] == IN_PRINCIPLE
    assert [g["gate"] for g in fin["gates"]] == FINAL
    for rec in (ip, fin):
        assert rec["policy_version"] == pol.POLICY["version"] and rec["evidence"]["data_sources"]
        assert rec["risk_summary"]["decision"]["outcome"] == rec["outcome"] == "approve"
        rules = next(g for g in rec["gates"] if g["gate"] == "compliance")["facts"]["rules"]
        assert {r["rule"].split(" ")[0] for r in rules} >= {"CMP-01", "CMP-02", "CMP-03", "CMP-05", "CMP-06", "CMP-07",
                                                            "CMP-08", "CMP-09", "CMP-10"} and all(r["ok"] for r in rules)


# --------------------------------------------------------------------------- explain and replay (problem 8)

def test_explain_shows_everything_for_one_record(client, ravi):
    rec = _record(ravi, "final")
    x = client.get(f"/v1/staff/records/{rec['seq']}/explain", headers=STAFF).json()
    assert x["application_id"] == ravi["applicant"].id and x["record"] == rec["seq"] and x["decided_at"] == rec["at"]
    assert x["versions"]["policy"] == "policy-v2" and x["versions"]["engine"] == pipeline.ENGINE_VERSION
    assert {"credit_scorecard", "fraud_classifier", "property_avm"} <= set(x["versions"]["models"])
    loan = x["loan"]
    for k in ("amount_asked", "amount_offered", "income_used", "existing_emi", "foir_after", "foir_cap", "ltv", "ltv_cap"):
        assert loan[k] is not None, k
    sources = {s["source"] for s in x["data_sources"]}
    assert {"identity", "pan", "credit_bureau", "bank_statement", "sanctions_lists", "documents", "property_valuation",
            "land_records", "sale_deed"} <= sources
    assert all(s["if_unavailable"] and s["status"] for s in x["data_sources"])
    assert x["verification"]["documents"]["salarySlip"]["verdict"] and x["verification"]["aml"]["flag"] == "pass"
    assert x["model_outputs"]["scorecard"]["score"] == rec["evidence"]["models"]["scorecard"]["score"]
    assert x["risk"]["category"] in ("low", "medium", "high") and x["risk"]["model_confidence"]["validation"]["production_ready"] is False
    assert [g["gate"] for g in x["gates"]] == FINAL and x["policy_rules"]["compliance"]
    a = x["audit"]
    assert a["record_hash"] == rec["hash"] and a["record_hash_ok"] and a["chain"]["ok"] and a["reproducible"]
    assert client.get(f"/v1/staff/records/{rec['seq']}/explain").status_code == 403         # staff only


def test_an_old_policy_v1_record_replays_under_v1(ev):
    """A rule added in v2 must not change a decision made under v1."""
    ev["request"]["property_type"] = "commercial"
    assert decide(ev)[0] == "reject"                                   # under v2: a home loan on a shop
    old = copy.deepcopy(ev)
    old["policy_version"] = "policy-v1"
    for k in ("same_pan", "data_sources"):
        old.pop(k, None)
    outcome, codes, d = decide(old)
    assert outcome == "approve" and "R-POL-03" not in codes
    rules = next(g for g in d["gates"] if g["gate"] == "compliance")["facts"]["rules"]
    assert not any(r["rule"].startswith(("CMP-09", "CMP-10")) for r in rules)
    rec = {"seq": 0, "evidence": old, **d}
    assert pipeline.replay(rec)["same_decision"]
    assert pol.for_version("policy-v1") is pol.POLICY_V1 and pol.for_version("unknown") is pol.POLICY
    assert all(pol.POLICY_V1[k] == pol.POLICY[k] for k in pol.POLICY_V1 if k not in ("version", "effective_from"))


def test_explain_rebuilds_the_sources_for_a_record_made_before_they_were_kept(ev):
    ev.pop("data_sources")
    rows = provenance.sources(ev)
    assert {r["source"] for r in rows} >= {"identity", "credit_bureau", "bank_statement", "sanctions_lists"}


def test_explain_works_on_a_record_made_before_this_review(ravi):
    """Records already in a database have no data_sources, risk_summary or property_type."""
    from backend import explain
    from backend.ledger import digest
    rec = copy.deepcopy(_record(ravi, "in_principle"))
    for k in ("same_pan", "data_sources"):
        rec["evidence"].pop(k, None)
    rec["evidence"]["request"].pop("property_type", None)
    rec.pop("risk_summary")
    rec["evidence"]["policy_version"] = rec["policy_version"] = "policy-v1"
    body = {k: v for k, v in rec.items() if k not in ("prev_hash", "hash")}
    rec["hash"] = digest(body, rec["prev_hash"])
    x = explain.explain(rec, {"ok": True}, pipeline.replay(rec))
    assert x["versions"]["policy"] == "policy-v1" and x["loan"]["property_type"] == "residential"
    assert x["data_sources"] and x["risk"]["factors"] and x["audit"]["record_hash_ok"] and x["audit"]["reproducible"]
