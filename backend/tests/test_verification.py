"""Document verification: every upload gets a verdict and a 0-100 score, with no one confirming
what was read. A document that fails (edited, reused, or not the applicant's) rejects."""
from backend import sandbox
from backend.intake import verify
from backend.tests.conftest import ALL_CONSENTS, STAFF, Applicant

REQUEST = {"product": "homeLoan", "amount": 3000000, "years": 20, "propertyValue": 5000000}


def _rec(**changes):
    rec = {"accepted": True, "reason_code": None, "classifier": {"confidence": 0.95}, "tamper": {"signals": []},
           "reuse": {"reused_from": []}}
    return {**rec, **changes}


def test_the_score_is_the_weakest_check():
    assert verify(_rec()) == {"verdict": "verified", "score": 95, "problems": [],
                              "checks": {"type": True, "readable": True, "untouched": True, "unique": True}}
    assert verify(_rec(name_score=72.4))["score"] == 72                 # a name that's close: verified, lower score
    doubtful = verify(_rec(tamper={"signals": ["1 later revision(s) appended to the file"]}))
    assert doubtful["verdict"] == "verified" and doubtful["score"] == 60  # signed PDFs append revisions too


def test_edited_reused_or_someone_elses_document_fails():
    for changes in ({"tamper": {"signals": ["made with an editing tool (Sejda)"]}},
                    {"tamper": {"signals": ["edited with Adobe Photoshop 25.0"]}},
                    {"reuse": {"reused_from": ["HL-2611-00001"]}},
                    {"pan_matches": False},
                    {"name_score": 31.0},
                    {"failed_before": [{"uploaded_at": "2026-11-07T10:00:00", "problems": ["the file names an editing tool"]}]}):
        v = verify(_rec(**changes))
        assert v["verdict"] == "failed" and v["score"] <= 10 and v["problems"], changes


def test_a_document_that_couldnt_be_checked_is_a_retake_not_a_failure():
    for code in ("R-DOC-01", "R-DOC-02"):
        v = verify(_rec(accepted=False, reason_code=code, tamper={"signals": ["edited with Snapseed"]}))
        assert v["verdict"] == "retake" and v["score"] is None and not v["problems"]


def test_a_card_with_another_pan_rejects_and_a_retake_doesnt_clear_it(client):
    a = Applicant(client, "9000000031", "device-v1")
    a.put("/consents", json=ALL_CONSENTS)
    a.post("/profile", json={"pan": "ABCPK1234K"})
    priya = sandbox.PERSONAS["priya"]   # phones outside the demo cases get Priya
    other = a.upload("pan", sandbox.sample_document(priya, "pan", pan="ZZZPK9999Z", seed=a.id)).json()
    assert other["accepted"] and other["verification"]["verdict"] == "failed"
    assert other["verification"]["checks"]["pan"] is False and other["verification"]["checks"]["name"] is True
    assert set(other["verification"]["checks"]) <= {"type", "readable", "name", "pan"}   # the fraud checks aren't shown
    d = a.post("/in-principle", json=REQUEST).json()
    assert d["outcome"] == "reject" and [r["code"] for r in d["reasons"]] == ["R-DOC-05"]
    assert d["verification"]["score"] <= 10 and d["verification"]["documents"]["pan"] <= 10
    assert set(d["credit"]) == {"score", "status"}

    right = a.upload("pan", sandbox.sample_document(priya, "pan", seed=a.id + "-2")).json()
    assert right["verification"]["verdict"] == "failed"                  # the earlier failure stays on the file
    assert a.post("/in-principle", json=REQUEST).json()["outcome"] == "reject"
    recs = client.get(f"/v1/staff/applications/{a.id}/records", headers=STAFF).json()
    gate = next(g for g in recs[-1]["gates"] if g["gate"] == "fraud_documents")
    assert any("PAN on the card" in n or "earlier upload" in n for n in gate["notes"])


def test_the_applicants_own_files_on_their_earlier_application_are_not_reuse(client):
    data = sandbox.sample_document(sandbox.PERSONAS["priya"], "pan", seed="same-person")
    results = []
    for phone, device in (("9000000032", "device-v2"), ("9000000033", "device-v3")):
        a = Applicant(client, phone, device)
        a.put("/consents", json=ALL_CONSENTS)
        a.post("/profile", json={"pan": "ABCPK1234K"})
        results.append(a.upload("pan", data).json())
    assert [r["verification"]["verdict"] for r in results] == ["verified", "verified"]
    assert results[0]["verification"]["score"] >= 50
