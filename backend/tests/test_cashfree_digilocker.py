"""DigiLocker through Cashfree Secure ID: the link, the status checks and the shared Aadhaar and PAN,
against a stand-in of Cashfree's API. Also DigiLocker opened from the documents step, after the
details were typed."""
import base64
import io
import json

import httpx
import pytest

from backend import digilocker
from backend import otp as otp_module
from backend.config import settings
from backend.tests.conftest import ALL_CONSENTS
from backend.tests.test_real_mode import FakeSms, sign_in


def jpeg() -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (60, 80), (200, 160, 140)).save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


class FakeCashfree:
    """Answers like Cashfree's DigiLocker API: PENDING until the test says the applicant finished."""

    def __init__(self, pan="BXRPK4821K", status="PENDING", consent=("AADHAAR", "PAN")):
        self.pan, self.status, self.consent = pan, status, list(consent)
        self.calls: list[tuple[str, str, dict]] = []
        self.signed: list[bool] = []
        self.created: dict | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        h = dict(request.headers)
        if h.get("x-client-id") != "cf-id" or h.get("x-client-secret") != "cf-secret":
            return httpx.Response(401, json={"type": "authentication_error", "code": "x-client-id_value_invalid"})
        self.signed.append("x-cf-signature" in h)
        path = request.url.path.removeprefix("/verification")
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, path, body or dict(request.url.params)))
        if path == "/digilocker/verify-account":
            return httpx.Response(200, json={"verification_id": body["verification_id"], "status": "ACCOUNT_EXISTS"})
        if path == "/digilocker" and request.method == "POST":
            self.created = body
            return httpx.Response(200, json={"verification_id": body["verification_id"], "reference_id": 4242,
                                             "url": "https://verification-test.digilocker.example/c/abc", "status": "PENDING"})
        if path == "/digilocker":
            done = self.status == "AUTHENTICATED"
            return httpx.Response(200, json={"status": self.status, "verification_id": request.url.params["verification_id"],
                                             "document_requested": ["AADHAAR", "PAN"],
                                             "document_consent": self.consent if done else None,
                                             "user_details": {"name": "Ravi Kumar", "eaadhaar": "Y"} if done else {}})
        if path == "/digilocker/document/AADHAAR":
            return httpx.Response(200, json={"status": "SUCCESS", "name": "Ravi  Kumar", "dob": "14-03-1994", "gender": "M",
                                             "uid": "xxxxxxxx4821", "photo_link": jpeg(), "care_of": "S/O Mohan",
                                             "split_address": {"house": "Flat 304", "street": "Lake View Road", "vtc": "Pune",
                                                               "dist": "Pune", "state": "Maharashtra", "pincode": "411038",
                                                               "country": "India"}})
        if path == "/digilocker/document/PAN":
            return httpx.Response(200, json={"status": "SUCCESS", "pan": self.pan, "name_pan_card": "RAVI KUMAR",
                                             "dob": "14-03-1994", "gender": "M", "type": "Individual"})
        return httpx.Response(404, json={"code": "not_found"})


def cashfree(fake: FakeCashfree, key: bytes | None = None) -> digilocker.CashfreeDigiLocker:
    return digilocker.CashfreeDigiLocker(digilocker.CASHFREE_SANDBOX, "cf-id", "cf-secret", key,
                                         http=httpx.Client(transport=httpx.MockTransport(fake.handle)))


def test_cashfree_answers_are_kept_like_the_xml_last_four_digits_only():
    a = digilocker.cashfree_aadhaar({"status": "SUCCESS", "name": "Ravi  Kumar", "dob": "14-03-1994", "gender": "MALE",
                                     "uid": "999988884821", "photo_link": "data:image/jpeg;base64," + jpeg(),
                                     "split_address": {"house": "Flat 304", "vtc": "Pune", "dist": "Pune", "pincode": "411038"}})
    assert (a["name"], a["dob"], a["gender"], a["aadhaar_masked"]) == ("Ravi Kumar", "1994-03-14", "M", "XXXX XXXX 4821")
    assert a["address"] == "Flat 304, Pune 411038" and a["photo"].startswith(b"\xff\xd8")
    assert "9999" not in json.dumps({k: v for k, v in a.items() if k != "photo"})     # the full number is dropped
    assert digilocker.cashfree_aadhaar({"status": "AADHAAR_NOT_LINKED"}) is None
    assert digilocker.cashfree_pan({"status": "SUCCESS", "pan": "bxrpk4821k", "name_pan_card": "RAVI KUMAR", "dob": "1994-03-14"}) == \
        {"pan": "BXRPK4821K", "name": "RAVI KUMAR", "dob": "1994-03-14", "gender": None}
    assert digilocker.cashfree_pan({"status": "SUCCESS", "pan": "not-a-pan"}) is None


def test_the_2fa_signature_is_the_client_id_and_time_sealed_with_the_public_key():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    c = cashfree(FakeCashfree(), pem)
    sealed = base64.b64decode(c.signature(at=1762500000))
    oaep = padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)
    assert private.decrypt(sealed, oaep) == b"cf-id.1762500000"
    fake = FakeCashfree()
    cashfree(fake, pem).begin("HL-1", "9000000000")
    assert fake.signed == [True, True]                            # account check and link: both signed
    cashfree(fake).begin("HL-1", "9000000000")
    assert fake.signed[2:] == [False, False]                      # no key: IP whitelisting instead


def test_link_status_and_documents():
    fake = FakeCashfree()
    c = cashfree(fake)
    url, vid = c.begin("HL-2611-10001", "9000013131", "https://nirnay.example/v1/digilocker/return")
    assert url.startswith("https://verification-test.") and vid.startswith("HL-2611-10001-") and len(vid) <= 50
    assert fake.created["document_requested"] == ["AADHAAR", "PAN"] and fake.created["user_flow"] == "signin"
    assert fake.created["redirect_url"] == "https://nirnay.example/v1/digilocker/return"
    cashfree(fake).begin("HL-1", None, "http://127.0.0.1:8000/v1/digilocker/return")
    assert "redirect_url" not in fake.created                     # Cashfree takes https only: plain http is left out
    assert fake.created["user_flow"] == "signup"                  # no mobile number to look the account up with
    assert c.collect(vid) is None                                 # still in DigiLocker
    fake.status = "AUTHENTICATED"
    got = c.collect(vid)
    assert got["aadhaar"]["aadhaar_masked"] == "XXXX XXXX 4821" and got["pan"]["pan"] == "BXRPK4821K"
    fake.consent = ["AADHAAR"]                                    # PAN not allowed: only the Aadhaar comes
    assert c.collect(vid)["pan"] is None
    for status, code in (("CONSENT_DENIED", "digilocker_access_denied"), ("EXPIRED", "digilocker_expired")):
        fake.status = status
        with pytest.raises(digilocker.DigiLockerError) as e:
            c.collect(vid)
        assert e.value.code == code
    bad = digilocker.CashfreeDigiLocker(digilocker.CASHFREE_SANDBOX, "cf-id", "wrong", None,
                                        http=httpx.Client(transport=httpx.MockTransport(fake.handle)))
    with pytest.raises(digilocker.DigiLockerError) as e:
        bad.begin("HL-1")
    assert e.value.code == "digilocker_x-client-id_value_invalid"


def test_health_says_cashfree_when_its_keys_are_set(client, monkeypatch):
    monkeypatch.setenv("CASHFREE_CLIENT_ID", "cf-id")
    monkeypatch.setenv("CASHFREE_CLIENT_SECRET", "cf-secret")
    monkeypatch.setattr(settings, "sandbox", False)
    assert client.get("/v1/health").json()["digilocker"] == "cashfree"


# --------------------------------------------------------------------------- real mode, from the documents step

TYPED = {"name": "Ravi Kumar", "dob": "1994-03-14", "email": "ravi@example.in", "employment": "salaried",
         "employer": "Nimbus Software Pvt Ltd", "monthly_income": 100000, "existing_emi": 2000}


@pytest.fixture
def real(monkeypatch):
    monkeypatch.setattr(settings, "sandbox", False)
    monkeypatch.setattr(otp_module, "provider", lambda sandbox: FakeSms())


def typed_in(client, phone, device, fake, monkeypatch, **typed):
    c = cashfree(fake)
    monkeypatch.setattr(digilocker, "client", lambda sandbox, engine_url: c)
    app_id, h, base = sign_in(client, phone, device)
    client.put(base + "/consents", headers=h, json=ALL_CONSENTS)
    p = client.post(base + "/profile", headers=h, json={"pan": "BXRPK4821K", "declared": {**TYPED, **typed}}).json()
    assert {"pan", "aadhaar"} <= set(p["requiredDocuments"])     # nothing from DigiLocker yet
    return app_id, h, base


def test_digilocker_from_the_documents_step_replaces_the_card_photos(client, real, monkeypatch):
    fake = FakeCashfree()
    app_id, h, base = typed_in(client, "9000014141", "dev-cf-1", fake, monkeypatch, name="Ravi K", dob="1994-03-15")
    assert client.post(base + "/digilocker", headers=h, json={"returnTo": "app"}).json()["url"].startswith("https://verification-test.")
    assert client.get(base + "/digilocker", headers=h).json()["status"] == "pending"
    fake.status = "AUTHENTICATED"                                 # the applicant allowed sharing
    s = client.get(base + "/digilocker", headers=h).json()
    assert s["status"] == "done" and s["name"] == "Ravi Kumar" and s["aadhaarMasked"] == "XXXX XXXX 4821"
    assert "aadhaar" not in s["requiredDocuments"]
    assert "pan" in s["requiredDocuments"]           # this e-Aadhaar photo has no face, so the PAN card is the face match
    from backend.app import store
    a = store.apps[app_id]
    assert (a.profile["name"], a.profile["dob"]) == ("Ravi Kumar", "1994-03-14")   # DigiLocker's, not the typed ones
    assert a.profile["identity_source"] == "cashfree-digilocker" and a.profile["pan_name"] == "RAVI KUMAR"
    assert "name" not in a.profile["self_declared"] and "dob" not in a.profile["self_declared"]
    assert a.sources["digilocker"]["typed_vs_digilocker"]["dob_matches"] is False
    assert not any(isinstance(v, bytes) for v in a.sources["digilocker"].values())  # the photo isn't kept
    d = client.post(base + "/in-principle", headers=h, json={"product": "homeLoan", "amount": 3000000, "years": 20,
                                                             "propertyValue": 5000000}).json()
    ident = next(g for g in store.apps[app_id].in_principle["gates"] if g["gate"] == "identity")
    assert any("date of birth typed in differs" in n for n in ident["notes"])
    assert d["outcome"] != "approve"                 # no bureau, no documents yet: an officer looks at it


def test_a_different_pan_in_digilocker_is_refused(client, real, monkeypatch):
    fake = FakeCashfree(pan="ZZZPZ9999Z", status="AUTHENTICATED")
    app_id, h, base = typed_in(client, "9000015151", "dev-cf-2", fake, monkeypatch)
    client.post(base + "/digilocker", headers=h)
    s = client.get(base + "/digilocker", headers=h).json()
    assert (s["status"], s["error"]) == ("failed", "pan_mismatch") and "name" not in s
    from backend.app import store
    assert store.apps[app_id].profile["name"] == "Ravi Kumar" and "digilocker" not in store.apps[app_id].sources


def test_saying_no_in_digilocker(client, real, monkeypatch):
    fake = FakeCashfree(status="CONSENT_DENIED")
    _, h, base = typed_in(client, "9000016161", "dev-cf-3", fake, monkeypatch)
    client.post(base + "/digilocker", headers=h)
    s = client.get(base + "/digilocker", headers=h).json()
    assert (s["status"], s["error"]) == ("denied", "digilocker_access_denied")
    assert {"pan", "aadhaar"} <= set(s["requiredDocuments"])     # the card photos are still needed


def test_the_return_page_sends_the_browser_back_to_the_app(client):
    r = client.get("/v1/digilocker/return")
    assert r.status_code == 200 and "nirnay://digilocker" in r.text
