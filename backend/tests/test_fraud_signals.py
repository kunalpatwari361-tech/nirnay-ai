"""PS2 review, problem 3: fraud, KYC and AML signals refer to an officer; none rejects on a model score.

New in policy-v2: a duplicate application on the same PAN, and bank statements where most of the
money coming in is cash. (Identity, PAN and name matching, sanctions screening and the fraud
models were already gates: see test_gates.py and test_decision_table.py.)
"""
import copy
from datetime import date, timedelta

import pytest

from backend import pipeline
from backend.tests import realdocs
from backend.tests.conftest import ALL_CONSENTS
from backend.tests.test_real_mode import internal, real, sign_in  # noqa: F401  (real is a fixture)


@pytest.fixture
def ev(ravi):
    rec = next(r for r in ravi["records"] if r["stage"] == "in_principle")
    return copy.deepcopy(rec["evidence"])


def codes(ev):
    d = pipeline.decide(ev)
    return d, {r["code"] for g in d["gates"] for r in g["reasons"]}


def other(ev, days_ago=5, **changes):
    p = ev["profile"]
    when = (date.fromisoformat(ev["today"]) - timedelta(days=days_ago)).isoformat() + "T10:00:00+05:30"
    return {"application_id": "HL-OTHER", "created_at": when, "name": p["name"], "dob": p["dob"], "employer": p["employer"],
            "monthly_income": p["monthly_income"], **changes}


def test_reapplying_with_the_same_details_is_not_a_duplicate(ev):
    ev["same_pan"] = [other(ev)]
    d, found = codes(ev)
    assert d["outcome"] == "approve" and "R-FRD-07" not in found
    gate = next(g for g in d["gates"] if g["gate"] == "fraud_documents")
    assert gate["facts"]["same_pan_applications"] == 1 and any("re-application" in n for n in gate["notes"])


@pytest.mark.parametrize("changes, what", [
    ({"name": "Suresh Iyer"}, "name"),
    ({"dob": "1970-01-01"}, "date of birth"),
    ({"monthly_income": 300000}, "income"),
    ({"employer": "Orbit Logistics Ltd"}, "employer"),
])
def test_the_same_pan_with_other_details_refers(ev, changes, what):
    ev["same_pan"] = [other(ev, **changes)]
    d, found = codes(ev)
    assert d["outcome"] == "refer" and "R-FRD-07" in found
    r = next(r for g in d["gates"] for r in g["reasons"] if r["code"] == "R-FRD-07")
    assert what in r["values"]["differs"]
    assert [c["code"] for c in d["customer_reasons"]] == ["R-RSK-01"]          # no tipping off


def test_a_new_job_or_raise_long_ago_is_not_a_duplicate(ev):
    ev["same_pan"] = [other(ev, days_ago=400, monthly_income=40000, employer="Old Employer Pvt Ltd")]
    assert "R-FRD-07" not in codes(ev)[1]


def test_mostly_cash_deposits_refers(ev):
    ev["bank"]["cash_deposit_share_of_credits"] = 0.55
    d, found = codes(ev)
    assert d["outcome"] == "refer" and "R-FRD-08" in found
    ev["bank"]["cash_deposit_share_of_credits"] = 0.2
    assert "R-FRD-08" not in codes(ev)[1]


def test_kyc_name_mismatch_and_pan_on_the_card_refer(ev):
    ev["identity"]["pan_name_score"] = 60.0
    assert "R-KYC-02" in codes(ev)[1]
    ev["identity"]["pan_name_score"] = 98.0
    ev["identity"]["pan_on_card_matches"] = False
    assert "R-KYC-02" in codes(ev)[1]


def test_real_mode_finds_the_same_pan_with_another_date_of_birth(client, real):  # noqa: F811
    pan = "QWEPZ9876Q"                  # a PAN no other test uses
    first_id, h, base = sign_in(client, "9000077001", "dev-dup-1")
    client.put(base + "/consents", headers=h, json=ALL_CONSENTS)
    declared = {k: v for k, v in realdocs.DECLARED.items() if k != "credit_score"}
    assert client.post(base + "/profile", headers=h, json={"pan": pan, "declared": declared}).status_code == 200
    loan = {"product": "homeLoan", "amount": 3000000, "years": 20, "propertyValue": 5000000}
    client.post(base + "/in-principle", headers=h, json=loan)
    _, found = internal(client, first_id, "in_principle")
    assert "R-FRD-07" not in found                                   # the first application on this PAN
    second_id, h2, base2 = sign_in(client, "9000077002", "dev-dup-2")
    client.put(base2 + "/consents", headers=h2, json=ALL_CONSENTS)
    client.post(base2 + "/profile", headers=h2, json={"pan": pan, "declared": {**declared, "dob": "1985-01-01"}})
    client.post(base2 + "/in-principle", headers=h2, json=loan)
    rec, found = internal(client, second_id, "in_principle")
    assert "R-FRD-07" in found
    assert rec["evidence"]["same_pan"][0]["application_id"] == first_id
    lookup = next(s for s in rec["evidence"]["data_sources"] if s["source"] == "same_pan_lookup")
    assert lookup["status"].startswith("checked: 1")
    bureau = next(s for s in rec["evidence"]["data_sources"] if s["source"] == "credit_bureau")
    assert bureau["mode"] == "not_connected" and "R-BUR-06" in bureau["if_unavailable"]
