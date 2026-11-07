"""DigiLocker sign-in (PAN and e-Aadhaar straight from DigiLocker) and the credit report pulled with the PAN."""
import base64
import io
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from backend import bureau, digilocker
from backend import otp as otp_module
from backend.config import settings
from backend.tests.conftest import ALL_CONSENTS, Applicant
from backend.tests.test_real_mode import FakeSms, sign_in

TODAY = date(2026, 11, 7)


# --------------------------------------------------------------------------- reading DigiLocker's files

def test_eaadhaar_xml_is_read_and_only_the_last_four_digits_kept():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (60, 80), (200, 160, 140)).save(buf, "JPEG")
    photo = base64.b64encode(buf.getvalue()).decode()
    xml = (f'<Certificate xmlns="http://example.org/kyc"><CertificateData><KycRes ret="Y"><UidData uid="xxxxxxxx4821">'
           f'<Poi name="Ravi  Kumar" dob="14-03-1994" gender="M"/>'
           f'<Poa co="S/O Mohan" house="Flat 304" street="Lake View Road" loc="Kothrud" vtc="Pune" dist="Pune" '
           f'state="Maharashtra" pc="411038"/><Pht>{photo}</Pht></UidData></KycRes></CertificateData></Certificate>').encode()
    a = digilocker.parse_eaadhaar(xml)
    assert (a["name"], a["dob"], a["gender"], a["pincode"]) == ("Ravi Kumar", "1994-03-14", "M", "411038")
    assert a["aadhaar_masked"] == "XXXX XXXX 4821"
    assert a["address"] == "Flat 304, Lake View Road, Kothrud, Pune, Maharashtra 411038"    # no "care of" line
    assert a["photo"].startswith(b"\xff\xd8")


def test_offline_aadhaar_format_and_pan_record_are_read():
    offline = (b'<OfflinePaperlessKyc referenceId="77772026110712000000"><UidData><Poi dob="09-05-1990" gender="F" '
               b'name="Priya Nair"/><Poa pc="411014" vtc="Pune"/></UidData></OfflinePaperlessKyc>')
    a = digilocker.parse_eaadhaar(offline)
    assert a["aadhaar_masked"] == "XXXX XXXX 7777" and a["photo"] is None
    pan = digilocker.parse_pan(b'<PullURIResponse><DocDetails><Certificate type="PANCR" number="dnrpn7731f">'
                               b'<IssuedTo><Person name="PRIYA NAIR" dob="09-05-1990" gender="F"/></IssuedTo>'
                               b'</Certificate></DocDetails></PullURIResponse>')
    assert pan == {"pan": "DNRPN7731F", "name": "PRIYA NAIR", "dob": "1990-05-09", "gender": "F"}


def test_consent_screen_text_keeps_only_the_characters_digilocker_allows():
    assert digilocker.consent_text("Home-loan KYC (Nirnay)!", "x") == "Home loan KYC Nirnay"
    assert digilocker.consent_text("  ", "kyc") == "kyc" and digilocker.consent_text(None, "kyc") == "kyc"
    c = digilocker.Client("https://dl.invalid/public", "id", "s", "https://x/cb", service_name="Nirnay: Home Loans",
                          doctypes=" panCR , ")
    q = parse_qs(urlparse(c.authorize_url("st", "ch", "98765")).query)
    assert q["service_name"] == ["Nirnay Home Loans"] and q["req_doctype"] == ["PANCR"]
    assert "prompt" not in q and "login_hint" not in q                          # not a 10-digit mobile: no hint


def test_digilocker_mobile_is_compared_not_kept_and_the_token_is_revoked():
    test = digilocker.TestDigiLocker()
    c = digilocker.Client("https://dl.invalid/public", test.CLIENT_ID, test.secret, "https://x/cb",
                          http=httpx.Client(transport=httpx.MockTransport(test.handle)))
    verifier, challenge = digilocker.pkce_pair()
    got = c.finish(test.issue_code("ravi", challenge, "https://x/cb"), verifier, "https://x/cb")
    assert got["mobile"] is None and got["pan"]["pan"] == "BXRPK4821K"   # no "userdetails" scope: not needed
    assert len(test.revoked) == 1 and not test.tokens                  # nothing more can be read with the token
    test.mobiles["ravi"] = "919000077777"
    verifier, challenge = digilocker.pkce_pair()
    got = c.finish(test.issue_code("ravi", challenge, "https://x/cb"), verifier, "https://x/cb")
    assert got["mobile"] == "9000077777" and len(test.revoked) == 2

    from backend import gates, intake
    from backend.store import Application
    app = Application(id="HL-TEST-2", phone="9000088888", device_id="dev-y", persona=None, mode="real")
    ident = intake.apply_digilocker(app, got, "digilocker")
    assert ident["mobile_matches"] is False and "9000077777" not in str(app.sources)
    ev = {"sandbox": False, "identity": {"selfie": None, "face_match": None, "pan_name_score": None, "document_name_scores": {},
                       "pan_on_card_matches": None, "typed_vs_digilocker": None, "digilocker_mobile_matches": False}}
    assert any("mobile number on the DigiLocker account" in n for n in gates.identity(ev).notes)


def test_unsafe_or_tampered_files_are_refused():
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><Certificate>&b;</Certificate>'
    with pytest.raises(digilocker.DigiLockerError) as e:
        digilocker.parse_pan(bomb)
    assert e.value.code == "digilocker_bad_file"
    c = digilocker.Client("http://dl.invalid/public", "id", "secret", "http://x/cb",
                          http=httpx.Client(transport=httpx.MockTransport(
                              lambda r: httpx.Response(200, content=b"<x/>", headers={"hmac": "bm9wZQ=="}))))
    with pytest.raises(digilocker.DigiLockerError) as e:
        c.xml("token", "in.gov.pan-PANCR-ABCDE1234F")
    assert e.value.code == "digilocker_bad_hmac"


def test_eaadhaar_photo_goes_into_the_face_match_and_is_not_kept():
    from PIL import Image

    from backend import intake
    from backend.store import Application
    from backend.tests.conftest import selfie_image
    face_img = selfie_image()
    if face_img is None:
        pytest.skip("no sample face photo on this machine")
    app = Application(id="HL-TEST-1", phone="9000000000", device_id="dev-x", persona=None, mode="real")
    intake.check_selfie(app, face_img)
    from PIL import ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(face_img))).convert("RGB")
    img.thumbnail((200, 200))                                   # e-Aadhaar photos are small
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    got = {"aadhaar": {"name": "Test Person", "dob": "1990-01-01", "gender": "M", "address": "Pune", "pincode": "411001",
                       "aadhaar_masked": "XXXX XXXX 1234", "photo": buf.getvalue()}, "pan": None}
    intake.apply_digilocker(app, got, "digilocker")
    assert app.face_match is not None and app.face_match["id_photo_from"] == "digilocker" and app.face_match["match"]
    assert app.sources["digilocker"]["aadhaar_photo"] is True and "photo" not in app.sources["digilocker"]
    app.sources["digilocker"]["has_pan"] = True
    assert not {"pan", "aadhaar"} & set(intake.required_documents(app))   # the e-Aadhaar face stands in for the cards


# --------------------------------------------------------------------------- the sign-in, sandbox

def _start(client, a):
    r = a.post("/digilocker")
    assert r.status_code == 200
    url = r.json()["url"]
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert urlparse(url).path == "/v1/sandbox/digilocker/oauth2/1/authorize"
    assert q["response_type"] == "code" and q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43
    assert q["redirect_uri"].endswith("/v1/digilocker/callback")
    # Spec v2.4: purpose and service_name are required; only the Aadhaar and PAN records are asked for.
    assert (q["purpose"], q["service_name"], q["req_doctype"]) == ("kyc", "Nirnay home loan", "ADHAR,PANCR")
    assert q["prompt"] == "login_mobile" and len(q["login_hint"]) == 10 and q["login_hint"].isdigit()
    return url, q


def test_sandbox_digilocker_fills_identity_and_drops_the_card_uploads(client):
    a = Applicant(client, "9123400001", "dev-dl-1")          # any other number is the demo person Priya
    assert a.post("/digilocker").json()["error"] == "consent_required"         # nothing is fetched before consent
    a.put("/consents", json=ALL_CONSENTS)
    url, q = _start(client, a)
    page = client.get(url)
    assert page.status_code == 200 and "Priya Nair" in page.text and "TEST DIGILOCKER" in page.text
    assert "Nirnay home loan" in page.text                                        # the service name is shown
    assert client.get(url.replace(q["code_challenge"], "x" * 43)).status_code == 404   # a link that was altered
    assert client.get(url.replace("service_name=", "x=")).status_code == 404     # v2.4: no service name, no page
    allowed = client.get(f"/v1/sandbox/digilocker/consent?state={q['state']}&allow=1", follow_redirects=False)
    assert allowed.status_code == 302 and "code=" in allowed.headers["location"]
    done = client.get(allowed.headers["location"])
    assert done.status_code == 200 and "Done" in done.text and "nirnay://digilocker" in done.text
    assert "expired" in client.get(allowed.headers["location"]).text          # each link works once
    s = a.get("/digilocker").json()
    assert s["status"] == "done" and s["name"] == "Priya Nair" and s["dateOfBirth"] == "1990-05-09"
    assert s["pan"] == "DNRPN7731F" and s["aadhaarMasked"].startswith("XXXX XXXX ") and s["hasPan"] and s["hasAadhaar"]
    assert a.post("/profile", json={"pan": "ABCPK1234K"}).json()["error"] == "pan_mismatch"
    p = a.post("/profile", json={"pan": s["pan"]}).json()
    assert p["identitySource"] == "digilocker" and p["aadhaarMasked"] == s["aadhaarMasked"]
    assert "pan" not in p["requiredDocuments"] and "aadhaar" not in p["requiredDocuments"]
    _start(client, a)                         # DigiLocker can still be opened from the documents step


def test_refusing_on_digilocker_leaves_the_uploads_in_place(client):
    a = Applicant(client, "9123400002", "dev-dl-2")
    a.put("/consents", json=ALL_CONSENTS)
    _, q = _start(client, a)
    denied = client.get(f"/v1/sandbox/digilocker/consent?state={q['state']}&allow=0")
    assert "Nothing was shared" in denied.text
    assert a.get("/digilocker").json() == {"status": "denied", "error": "access_denied"}
    p = a.post("/profile", json={"pan": "ABCPK1234K"}).json()
    assert {"pan", "aadhaar"} <= set(p["requiredDocuments"]) and p["identitySource"] is None


def test_the_phone_app_is_sent_straight_back(client):
    a = Applicant(client, "9123400003", "dev-dl-3")
    a.put("/consents", json=ALL_CONSENTS)
    url = a.post("/digilocker", json={"returnTo": "app"}).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    allowed = client.get(f"/v1/sandbox/digilocker/consent?state={state}&allow=1", follow_redirects=False)
    back = client.get(allowed.headers["location"], follow_redirects=False)
    assert back.status_code == 302 and back.headers["location"] == "nirnay://digilocker?status=done"
    assert a.get("/digilocker").json()["status"] == "done"


def test_health_says_what_is_connected(client):
    h = client.get("/v1/health").json()
    assert h["digilocker"] == "sandbox" and h["bureau"] == "sandbox-bureau"


# --------------------------------------------------------------------------- real mode

@pytest.fixture
def real(monkeypatch):
    sms = FakeSms()
    monkeypatch.setattr(settings, "sandbox", False)
    monkeypatch.setattr(otp_module, "provider", lambda sandbox: sms)
    return sms


def test_real_mode_without_keys_offers_no_digilocker_or_bureau(client, real):
    app_id, h, base = sign_in(client, "9000012121", "dev-dl-r0")
    client.put(base + "/consents", headers=h, json=ALL_CONSENTS)
    assert client.post(base + "/digilocker", headers=h).json()["error"] == "digilocker_not_connected"
    h2 = client.get("/v1/health").json()
    assert h2["digilocker"] is None and h2["bureau"] is None
    # The customer isn't asked for a credit score: with no bureau the report is "not pulled", and an officer pulls it.
    p = client.post(base + "/profile", headers=h, json={"pan": "BXRPK4821K", "declared": {
        **DECLARED, "name": "Ravi Kumar", "dob": "1988-03-14"}}).json()
    assert p["bureauScore"] is None and p["bureauStatus"] == "not_pulled" and p["bureauSource"] == "not-connected"
    d = client.post(base + "/in-principle", headers=h, json={"product": "homeLoan", "amount": 3000000, "years": 20,
                                                              "propertyValue": 5000000}).json()
    assert d["outcome"] != "approve"
    from backend.app import store
    policy = next(g for g in store.apps[app_id].in_principle["gates"] if g["gate"] == "policy")
    codes = {r["code"] for r in policy["reasons"]}
    assert "R-BUR-06" in codes and "R-BUR-04" not in codes     # "not pulled", not "no credit history"


DECLARED = {"email": "ravi@example.in", "employment": "salaried", "employer": "Nimbus Software Pvt Ltd",
            "monthly_income": 100000, "existing_emi": 2000}


def test_real_mode_digilocker_and_bureau(client, real, monkeypatch):
    test = digilocker.TestDigiLocker()
    real_client = digilocker.Client("https://dl.invalid/public", test.CLIENT_ID, test.secret,
                                    "https://nirnay.invalid/v1/digilocker/callback",
                                    http=httpx.Client(transport=httpx.MockTransport(test.handle)))
    monkeypatch.setattr(digilocker, "client", lambda sandbox, engine_url: real_client)
    seen = {}

    def decentro(request):
        seen.update(headers=dict(request.headers), body=__import__("json").loads(request.content))
        return httpx.Response(200, json=report())
    monkeypatch.setenv("DECENTRO_CLIENT_ID", "cid")
    monkeypatch.setenv("DECENTRO_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("NIRNAY_BUREAU", "decentro")
    monkeypatch.setattr(bureau.Decentro, "__init__", _decentro_init(httpx.Client(transport=httpx.MockTransport(decentro))))

    app_id, h, base = sign_in(client, "9000013131", "dev-dl-r1")
    client.put(base + "/consents", headers=h, json=ALL_CONSENTS)
    url = client.post(base + "/digilocker", headers=h).json()["url"]
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith("https://dl.invalid/public/oauth2/1/authorize?") and q["scope"] == "openid"
    assert q["login_hint"] == "9000013131" and q["service_name"] == "Nirnay home loan"
    test.mobiles["ravi"] = "9000013131"                       # DigiLocker's mobile is the sign-in number
    flow = digilocker.flow(q["state"])
    code = test.issue_code("ravi", flow["challenge"], flow["redirect_uri"])
    assert "Done" in client.get(f"/v1/digilocker/callback?code={code}&state={q['state']}").text
    from backend.app import store as app_store
    assert app_store.apps[app_id].sources["digilocker"]["mobile_matches"] is True and test.revoked
    p = client.post(base + "/profile", headers=h, json={"pan": "BXRPK4821K", "declared": DECLARED}).json()
    assert p["name"] == "Ravi Kumar" and p["dateOfBirth"] == "1994-03-14"          # from DigiLocker, not typed
    assert p["bureauScore"] == 777 and p["bureauSource"] == "decentro-staging-eq"   # from the bureau, not typed
    assert p["existingEmi"] == 12000                                               # the bureau's EMIs beat the typed 2,000
    # The test e-Aadhaar has no photo, so the PAN card is still needed for the face match; Aadhaar isn't.
    assert "pan" in p["requiredDocuments"] and "aadhaar" not in p["requiredDocuments"]
    assert seen["headers"]["client_id"] == "cid" and seen["headers"]["client_secret"] == "csecret"
    b = seen["body"]
    assert (b["document_type"], b["document_id"], b["mobile"], b["name"], b["consent"]) == \
        ("PAN", "BXRPK4821K", "9000013131", "Ravi Kumar", True)
    assert len(b["consent_purpose"]) > 20 and b["bureau_code"] == "EQ" and b["date_of_birth"] == "1994-03-14"
    from backend.app import store
    a = store.apps[app_id]
    assert "name" not in a.profile["self_declared"] and "credit_score" not in a.profile["self_declared"]
    assert not any(isinstance(v, bytes) for v in a.sources["digilocker"].values())        # no photo or file is kept
    r = client.post(base + "/in-principle", headers=h, json={"product": "homeLoan", "amount": 3000000, "years": 20,
                                                             "propertyValue": 5000000}).json()
    assert r["outcome"] != "approve"                     # a staging (test) credit report never decides a real case


def _decentro_init(http):
    real_init = bureau.Decentro.__init__

    def init(self, client=None):
        real_init(self, http)
    return init


# --------------------------------------------------------------------------- the credit report

def report(found=True):
    if not found:
        return {"decentroTxnId": "EE3B", "status": "SUCCESS", "responseCode": "S00000", "responseKey": "success_no_record_found",
                "data": {"cCRResponse": {"status": "1", "cIRReportDataLst": [
                    {"error": {"errorCode": "00", "errorDesc": "Consumer not found in bureau"}}]}, "reportOrderNumber": ""}}
    accounts = [
        {"seq": "1", "institution": "ICICI BANK LIMITED", "accountType": "Credit Card", "balance": "50000", "pastDueAmount": "0",
         "open": "Yes", "sanctionAmount": "100000", "installmentAmount": "0", "dateOpened": "2019-04-10",
         "history48Months": [{"key": "10-26", "paymentStatus": "000"}]},
        {"seq": "2", "institution": "HDFC Bank", "accountType": "Auto Loan", "balance": "300000", "pastDueAmount": "4500",
         "open": "Yes", "sanctionAmount": "600000", "installmentAmount": "12000", "dateOpened": "2026-08-01",
         "history48Months": [{"key": "10-26", "paymentStatus": "030"}]},
        {"seq": "3", "institution": "SBI", "accountType": "Personal Loan", "balance": "0", "pastDueAmount": "0", "open": "No",
         "sanctionAmount": "200,000", "installmentAmount": "8000", "dateOpened": "2016-01-15",
         "history48Months": [{"key": "01-19", "paymentStatus": "060"}]}]
    return {"decentroTxnId": "84A44C4F", "status": "SUCCESS", "responseCode": "S00000", "responseKey": "success_credit_report",
            "data": {"reportOrderNumber": "1000000000", "cCRResponse": {"status": "1", "cIRReportDataLst": [{"cIRReportData": {
                "scoreDetails": [{"type": "ERS", "version": "4.0", "name": "ERS4.0", "value": "777"}],
                "retailAccountDetails": accounts, "retailAccountsSummary": {"noOfAccounts": "3", "noOfWriteOffs": "0"},
                "enquiries": [{"seq": "1", "institution": "ICICI Bank", "date": "2026-09-01", "requestPurpose": "10"},
                              {"seq": "2", "institution": "Axis Bank", "date": "2020-01-01", "requestPurpose": "05"}]}}]}}}


def test_credit_report_summary():
    s = bureau.summarise(report(), TODAY, "decentro-live-eq")
    assert s["score"] == 777 and s["status"] == "scored" and s["reference"] == "84A44C4F"
    assert (s["total_accounts"], s["active_accounts"], s["overdue_accounts"]) == (3, 2, 1)
    assert s["recent_delinquencies"] == 1                  # the 30-days-late auto loan; the 2019 one is too old
    assert (s["enquiries_6m"], s["new_accounts_6m"]) == (1, 1)
    assert (s["outstanding"], s["sanctioned_total"]) == (350000, 900000)
    assert s["existing_emi"] == 12000 and s["has_credit_card"] is True and s["history_years"] == 10.8
    none = bureau.summarise(report(found=False), TODAY, "decentro-live-eq")
    assert none["score"] is None and none["status"] == "no_history"


def test_bureau_failures_are_clean(monkeypatch):
    monkeypatch.setenv("DECENTRO_CLIENT_ID", "cid")
    monkeypatch.setenv("DECENTRO_CLIENT_SECRET", "csecret")
    who = {"application_id": "HL-1", "name": "Ravi Kumar", "mobile": "9000013131", "pan": "BXRPK4821K"}

    def refused(request):
        return httpx.Response(400, json={"status": "FAILURE", "responseCode": "E00009", "message": "bad"})

    def down(request):
        raise httpx.ConnectTimeout("timed out", request=request)
    for handler, code in ((refused, "bureau_failed"), (down, "bureau_unreachable")):
        d = bureau.Decentro(httpx.Client(transport=httpx.MockTransport(handler)))
        with pytest.raises(bureau.BureauError) as e:
            d.pull(who, TODAY)
        assert e.value.code == code
    monkeypatch.setenv("NIRNAY_BUREAU", "sandbox")
    assert bureau.provider(False) is None                   # test reports are never used in real mode
