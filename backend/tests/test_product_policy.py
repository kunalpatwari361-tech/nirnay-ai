"""PS2 review, problem 2: Home Loan and LAP each follow their own policy.

  Home Loan: residential property only; RBI LTV tiers; FOIR; ownership through the sale deed.
  LAP:       residential or commercial; a lower LTV for commercial; income AND cash flow from the
             bank statement; the applicant must own the property; existing charges checked
             (encumbrance certificate and CERSAI).
And every application shows the loan amount, income, existing EMI, FOIR and LTV.
"""
import copy

import pytest

from backend import pipeline, policy as pol
from backend.tests.conftest import PUNE, STAFF, run_flow


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
    return d, {r["code"] for g in d["gates"] for r in g["reasons"]}


def facts(d, gate):
    return next(g for g in d["gates"] if g["gate"] == gate)["facts"]


def lap(ev, amount, value, ptype="residential", credits=1_000_000):
    ev["request"].update(product="lap", amount=amount, property_value=value, years=15, asked_years=15, property_type=ptype)
    ev["bank"]["avg_monthly_credits"] = credits
    return ev


def test_the_policy_table_per_product():
    p = pol.POLICY
    assert p["property_types"] == {"homeLoan": ["residential"], "lap": ["residential", "commercial"]}
    assert pol.ltv_cap("homeLoan", 25e5) == 0.90 and pol.ltv_cap("homeLoan", 50e5) == 0.80 and pol.ltv_cap("homeLoan", 90e5) == 0.75
    assert pol.ltv_cap("lap", 50e5) == 0.60 and pol.ltv_cap("lap", 50e5, "commercial") == 0.50
    assert p["max_tenure_years"] == {"homeLoan": 30, "lap": 15} and p["rate_pct"] == {"homeLoan": 8.75, "lap": 10.50}
    assert pol.foir_cap(40000) == 0.45 and pol.foir_cap(100000) == 0.50 and pol.foir_cap(200000) == 0.60


def test_a_home_loan_is_for_residential_property_only(ev):
    ev["request"]["property_type"] = "commercial"
    d, codes = decide(ev)
    assert d["outcome"] == "reject" and "R-POL-03" in codes
    assert [r["code"] for r in d["customer_reasons"]] == ["R-POL-03"]
    assert d["offer"]["kfs"] is None


def test_a_lap_takes_commercial_property_at_a_lower_ltv(ev):
    ev["income"]["income_used"] = 1_000_000
    res, _ = decide(lap(copy.deepcopy(ev), 9_000_000, 10_000_000, "residential"))
    com, codes = decide(lap(copy.deepcopy(ev), 9_000_000, 10_000_000, "commercial"))
    assert res["offer"]["amount"] == 6_000_000 and com["offer"]["amount"] == 5_000_000
    assert com["offer"]["limited_by"] == "property" and facts(com, "affordability")["ltv_cap"] == 0.5
    assert "R-POL-03" not in codes and facts(com, "policy")["property_type"] == "commercial"


def test_a_lap_needs_cash_flow_from_the_bank_statement(ev):
    enough, codes = decide(lap(copy.deepcopy(ev), 3_000_000, 6_000_000, credits=150_000))
    assert "R-AFF-05" not in codes and facts(enough, "affordability")["credit_cover"] >= 2
    thin, codes = decide(lap(copy.deepcopy(ev), 3_000_000, 6_000_000, credits=60_000))
    assert thin["outcome"] == "refer" and "R-AFF-05" in codes and facts(thin, "affordability")["credit_cover"] < 2
    none = lap(copy.deepcopy(ev), 3_000_000, 6_000_000)
    none["bank"]["available"] = False
    assert "R-AFF-05" in decide(none)[1]


def test_a_home_loan_has_no_cash_flow_rule(ev):
    ev["bank"]["avg_monthly_credits"] = 1.0
    assert "R-AFF-05" not in decide(ev)[1]


def test_a_lap_property_must_be_the_applicants_and_free_of_charges(final_ev):
    e = lap(final_ev, 3_000_000, 6_000_000)
    e["property"]["deed"] = None
    e["property"]["deed_buyer_score"] = None
    d, codes = decide(copy.deepcopy(e))
    assert d["outcome"] == "refer" and "R-PRP-05" in codes                       # no deed: ownership not shown
    e["property"]["deed_buyer_score"] = 96.0
    assert "R-PRP-05" not in decide(copy.deepcopy(e))[1]
    e["property"]["deed_buyer_score"] = 40.0
    assert "R-PRP-05" in decide(copy.deepcopy(e))[1]                             # the deed is someone else's
    e["property"]["deed_buyer_score"] = 96.0
    charged = copy.deepcopy(e)
    charged["property"]["land_records"].update(encumbrance_certificate="mortgage to a bank (2019)", cersai="charge registered")
    assert "R-PRP-06" in decide(charged)[1]
    unknown = copy.deepcopy(e)
    unknown["property"]["land_records"] = {"source": None, "title": "not_connected", "detail": "not connected"}
    assert "R-PRP-06" in decide(unknown)[1]                                     # real mode: no EC or CERSAI connected
    d, codes = decide(copy.deepcopy(e))
    assert "R-PRP-06" not in codes and facts(d, "property")["encumbrance"].startswith("nil")


def test_a_home_loan_has_no_lap_property_rules(final_ev):
    final_ev["property"]["deed_buyer_score"] = None
    final_ev["property"]["land_records"].pop("encumbrance_certificate")
    assert not {"R-PRP-05", "R-PRP-06"} & decide(final_ev)[1]


def test_commercial_property_goes_to_a_valuer(final_ev):
    e = lap(final_ev, 3_000_000, 6_000_000, "commercial")
    e["property"]["deed_buyer_score"] = 96.0
    d, codes = decide(e)
    assert "R-PRP-02" in codes
    assert any("commercial property" in n for g in d["gates"] if g["gate"] == "property" for n in g["notes"])


def test_meenas_lap_meets_the_new_lap_rules(meena):
    rec = _record(meena, "final")
    codes = {r["code"] for g in rec["gates"] for r in g["reasons"]}
    assert not {"R-AFF-05", "R-PRP-05", "R-PRP-06", "R-POL-03"} & codes
    assert next(g for g in rec["gates"] if g["gate"] == "affordability")["facts"]["credit_cover"] >= 2
    prop = next(g for g in rec["gates"] if g["gate"] == "property")["facts"]
    assert prop["owner_name_score"] >= 80 and prop["cersai"] == "no charge registered"


def test_every_application_shows_the_loan_facts(client, ravi, meena):
    rows = {r["applicationId"]: r for r in client.get("/v1/staff/applications", headers=STAFF).json()}
    for flow in (ravi, meena):
        loan = rows[flow["applicant"].id]["loan"]
        for k in ("amount_asked", "amount_offered", "income_used", "existing_emi", "foir_after", "ltv", "ltv_cap"):
            assert loan[k] is not None, k
    assert rows[meena["applicant"].id]["loan"]["product"] == "lap"


def test_the_api_takes_the_property_type(client):
    flow = run_flow(client, "priya", {"product": "homeLoan", "amount": 3000000, "years": 20, "propertyValue": 5000000,
                                      "propertyType": "commercial"}, PUNE)
    assert flow["in_principle"]["outcome"] == "reject"
    assert [r["code"] for r in flow["in_principle"]["reasons"]] == ["R-POL-03"]
    assert flow["records"][0]["evidence"]["request"]["property_type"] == "commercial"
    bad = flow["applicant"].post("/in-principle", json={"product": "lap", "amount": 1, "years": 5, "propertyValue": 9,
                                                        "propertyType": "farmland"})
    assert bad.status_code == 422
