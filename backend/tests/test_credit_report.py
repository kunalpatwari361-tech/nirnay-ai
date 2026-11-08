"""The applicant's own credit report fills the credit score when no bureau is connected."""
from datetime import date, timedelta

from backend import credit_report
from backend.tests import realdocs
from backend.tests.conftest import ALL_CONSENTS
from backend.tests.test_real_mode import LOAN, internal, real, sign_in, upload  # noqa: F401  (real is a fixture)

TODAY = date(2026, 11, 8)
CIBIL = """TransUnion CIBIL
CREDIT INFORMATION REPORT
Report Date : 02-11-2026
Consumer Name : KAVYA RAO
Date of Birth : 19-08-1992
Income Tax ID (PAN) : AKVPR4821K
CIBIL Score 772
Scores range from 300 to 900."""


def test_a_cibil_report_is_read():
    f = credit_report.read(CIBIL)
    assert f == {"bureau": "TransUnion CIBIL", "score": "772", "reportDate": "2026-11-02", "name": "KAVYA RAO",
                 "pan": "AKVPR4821K"}
    assert credit_report.check(f, TODAY) == ("pass", None)


def test_the_scale_and_dates_are_not_read_as_the_score():
    text = """Experian Credit Report
Date of Issue: 5 Oct 2026
Name: Arjun Mehta
Your Experian Credit Score (300 - 900) is
812"""
    f = credit_report.read(text)
    assert f["bureau"] == "Experian" and f["score"] == "812" and f["reportDate"] == "2026-10-05"


def test_other_bureaus_and_date_styles():
    assert credit_report.read("Equifax credit score: 701\nReport Date: Nov 1, 2026\nName: A B")["reportDate"] == "2026-11-01"
    assert credit_report.read("CRIF High Mark Credit Score 690\nas on 01/11/2026\nName: A B")["bureau"] == "CRIF High Mark"
    plain = credit_report.read("CIBIL Credit Report\nDate: 03/11/2026\nDate of Birth: 19/08/1992\nName: A B\nScore 701")
    assert plain["reportDate"] == "2026-11-03"                 # the report's date, not the date of birth

def test_what_is_not_a_usable_report():
    assert credit_report.check(credit_report.read("Salary slip for October 2026\nNet pay 95,000"), TODAY) == ("refer", "R-DOC-01")
    no_score = CIBIL.replace("CIBIL Score 772", "")
    assert credit_report.check(credit_report.read(no_score), TODAY) == ("refer", "R-DOC-02")
    old = CIBIL.replace("02-11-2026", "01-05-2026")
    assert credit_report.check(credit_report.read(old), TODAY) == ("refer", "R-DOC-06")   # more than 90 days old
    undated = CIBIL.replace("Report Date : 02-11-2026\n", "")
    assert credit_report.check(credit_report.read(undated), TODAY) == ("refer", "R-DOC-06")


def _applicant(client, phone):
    app_id, h, base = sign_in(client, phone, f"dev-{phone}")
    client.put(base + "/consents", headers=h, json=ALL_CONSENTS)
    declared = {k: v for k, v in realdocs.DECLARED.items() if k != "credit_score"}   # the app doesn't ask for one
    prof = client.post(base + "/profile", headers=h, json={"pan": realdocs.PAN, "declared": declared}).json()
    assert prof["bureauStatus"] == "not_pulled"
    return app_id, h, base


def test_an_uploaded_report_fills_the_credit_score(client, real):
    app_id, h, base = _applicant(client, "9000022222")
    r = upload(client, base, h, "creditReport", realdocs.credit_report(seed=app_id, issued=date.today() - timedelta(days=5)))
    assert r["accepted"] and r["verification"]["verdict"] == "verified", r
    assert r["fields"]["score"] == "772" and r["fields"]["bureau"] == "TransUnion CIBIL"
    assert r["verification"]["checks"]["name"] and r["verification"]["checks"]["pan"]
    ip = client.post(base + "/in-principle", headers=h, json=LOAN).json()
    assert ip["credit"] == {"score": 772, "status": "applicant_report", "bureau": "TransUnion CIBIL",
                            "reportDate": (date.today() - timedelta(days=5)).isoformat()}
    assert ip["score"]["provisional"] is False                       # the scorecard had a credit score
    rec, codes = internal(client, app_id, "in_principle")
    assert "R-BUR-06" not in codes and "R-BUR-07" not in codes        # counts for the in-principle offer
    policy = next(g for g in rec["gates"] if g["gate"] == "policy")
    assert any("applicant's own" in n for n in policy["notes"])


def test_an_old_report_is_turned_away_and_the_score_stays_unpulled(client, real):
    app_id, h, base = _applicant(client, "9000033333")
    r = upload(client, base, h, "creditReport", realdocs.credit_report(seed=app_id, issued=date.today() - timedelta(days=200)))
    assert not r["accepted"] and r["reasonCode"] == "R-DOC-06" and r["verification"]["verdict"] == "retake"
    ip = client.post(base + "/in-principle", headers=h, json=LOAN).json()
    assert ip["credit"]["status"] == "not_pulled" and ip["credit"]["score"] is None


def test_someone_elses_report_fails_and_takes_the_score_away(client, real):
    app_id, h, base = _applicant(client, "9000044444")
    good = upload(client, base, h, "creditReport", realdocs.credit_report(seed=app_id))
    assert good["verification"]["verdict"] == "verified", good
    other = upload(client, base, h, "creditReport",
                   realdocs.credit_report(seed=app_id + "x", score=850, name="ROHAN VERMA", pan="BXRPV7310Q"))
    assert other["verification"]["verdict"] == "failed"
    ip = client.post(base + "/in-principle", headers=h, json=LOAN).json()
    assert ip["outcome"] == "reject" and ip["credit"]["status"] == "not_pulled"   # R-DOC-05: not the applicant's


def test_the_final_stage_waits_for_an_officer_to_confirm_the_score():
    from backend import gates
    from backend.policy import POLICY
    ev = {"stage": "final", "today": "2026-11-08", "request": {"years": 20, "asked_years": 20},
          "profile": {"dob": "1992-08-19", "employment": "salaried",
                      "bureau": {"status": "applicant_report", "score": 772, "source": "applicant-report (TransUnion CIBIL)",
                                 "report_bureau": "TransUnion CIBIL", "report_date": "2026-11-02"}}}
    g = gates.policy_rules(ev, POLICY)
    assert [r["code"] for r in g.reasons] == ["R-BUR-07"]
    ev["stage"] = "in_principle"
    assert gates.policy_rules(ev, POLICY).reasons == []
    ev["profile"]["bureau"]["score"] = 610                          # below the minimum: an officer looks, no reject
    g = gates.policy_rules(ev, POLICY)
    assert [r["code"] for r in g.reasons] == ["R-BUR-07"] and g.outcome == "refer"
