"""DigiLocker: the applicant's Aadhaar (e-Aadhaar) and PAN, read straight from DigiLocker with
their consent, instead of photos of the cards.

The calls follow DigiLocker's Requester (Meri Pehchaan) API Specification v2.4 (NeGD, Sep 2026):
OAuth 2.0, authorization code flow with PKCE (S256).

1. start(): the engine makes a one-time `state` and a PKCE code verifier, and gives the app a
   link to DigiLocker's sign-in and consent page (/public/oauth2/1/authorize). Since v2.4 the
   link must carry `purpose` and `service_name` (letters, digits, spaces and underscores), shown
   on the consent screen. It also asks only for the Aadhaar and PAN records (`req_doctype`), and
   opens on the mobile sign-in with the applicant's number filled in (`prompt=login_mobile`,
   `login_hint`).
2. The applicant signs in to DigiLocker and allows sharing. DigiLocker sends the browser to
   our redirect URI (/v1/digilocker/callback) with ?code=...&state=... (or ?error=...).
3. finish(): the engine swaps the code for an access token (/public/oauth2/2/token), then reads
   the e-Aadhaar XML (/public/oauth2/3/xml/eaadhaar) and, from the list of issued documents
   (/public/oauth2/2/files/issued), the PAN record (doctype PANCR) as XML
   (/public/oauth2/1/xml/{uri}). Each file's `hmac` header (Base64 HMAC-SHA256 of the file,
   keyed with the client secret) is checked when DigiLocker sends one. If the token covers the
   account details (/public/oauth2/1/user, which has the mobile number since v2.4), the engine
   notes whether DigiLocker's mobile number is the one the applicant signed in with, without
   keeping the number. Then it revokes the token (/public/oauth2/1/revoke): nothing more is read.

Real mode needs DIGILOCKER_CLIENT_ID, DIGILOCKER_CLIENT_SECRET and DIGILOCKER_REDIRECT_URI
(exactly as registered on the DigiLocker partner portal, pointing at /v1/digilocker/callback).
DIGILOCKER_PURPOSE (default "kyc"), DIGILOCKER_SERVICE_NAME (default "Nirnay home loan") and
DIGILOCKER_DOCTYPES (default "ADHAR,PANCR"; empty sends none) change what the consent screen
shows. Registering needs an organisation (the lender) approved as a DigiLocker requester. These
calls are written from the published specification; run one real sign-in before inviting applicants.

Or through Cashfree Secure ID, a DigiLocker partner (CashfreeDigiLocker below), with
CASHFREE_CLIENT_ID and CASHFREE_CLIENT_SECRET. Cashfree's sandbox signs in to the real DigiLocker,
so it gives the applicant's own e-Aadhaar and PAN. The engine makes the link, the applicant signs in
and allows sharing, and the engine asks Cashfree for the result when the app checks (no callback).

The sandbox has a test DigiLocker with the fictional demo people: the engine serves a stand-in
consent page, and the same client code below talks to an in-process stand-in of the API.

Kept: name, date of birth, gender, address, the last four Aadhaar digits and the PAN.
Never kept: the Aadhaar number, the XML files, or the Aadhaar photo (used once, in memory, for
the face match with the selfie).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from urllib.parse import quote, urlencode

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

PRODUCTION = "https://digilocker.meripehchaan.gov.in/public"
CASHFREE_SANDBOX = "https://sandbox.cashfree.com/verification"
FLOW_SECONDS = 15 * 60            # a started sign-in must come back within this time
MAX_XML_BYTES = 2 * 1024 * 1024
PAN_RE = r"^[A-Z]{5}[0-9]{4}[A-Z]$"


class DigiLockerError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def pkce_pair() -> tuple[str, str]:
    """A code verifier (64 unreserved characters) and its S256 code challenge."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def file_hmac(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


# --------------------------------------------------------------------------- the API client

PURPOSE = "kyc"
SERVICE_NAME = "Nirnay home loan"
DOCTYPES = "ADHAR,PANCR"           # the Aadhaar and the PAN Verification Record


def consent_text(value: str | None, default: str) -> str:
    """purpose and service_name may hold only letters, digits, spaces and underscores (spec v2.4)."""
    import re
    v = " ".join(re.sub(r"[^A-Za-z0-9 _]", " ", value or "").split())[:80]
    return v or default


class Client:
    """DigiLocker's Requester API (or, in the sandbox, the stand-in below through `http`)."""

    def __init__(self, base: str, client_id: str, secret: str, redirect_uri: str, *, http: httpx.Client | None = None,
                 authorize_base: str | None = None, name: str = "digilocker", purpose: str = PURPOSE,
                 service_name: str = SERVICE_NAME, doctypes: str = DOCTYPES):
        self.base, self.client_id, self.secret, self.redirect_uri = base.rstrip("/"), client_id, secret, redirect_uri
        self.authorize_base = (authorize_base or base).rstrip("/")
        self.http = http or httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0))
        self.name = name
        self.purpose, self.service_name = consent_text(purpose, PURPOSE), consent_text(service_name, SERVICE_NAME)
        self.doctypes = ",".join(d.strip().upper() for d in doctypes.split(",") if d.strip())

    def authorize_url(self, state: str, challenge: str, mobile: str | None = None) -> str:
        q = {"response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri, "state": state,
             "code_challenge": challenge, "code_challenge_method": "S256", "scope": "openid",
             "purpose": self.purpose, "service_name": self.service_name}
        if self.doctypes:
            q["req_doctype"] = self.doctypes          # only these appear on the consent screen
        if mobile and len(mobile) == 10 and mobile.isdigit():
            q.update(prompt="login_mobile", login_hint=mobile)   # the mobile sign-in, number filled in
        return f"{self.authorize_base}/oauth2/1/authorize?{urlencode(q)}"

    def _call(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            r = self.http.request(method, url, **kw)
        except httpx.HTTPError as e:
            print(f"  !!! DigiLocker unreachable: {type(e).__name__}", flush=True)
            raise DigiLockerError("digilocker_unreachable") from e
        if r.status_code >= 400:
            try:
                why = r.json().get("error") or f"http_{r.status_code}"
            except ValueError:
                why = f"http_{r.status_code}"
            print(f"  !!! DigiLocker said {r.status_code}: {why} ({url.split('?')[0].rsplit('/public', 1)[-1]})", flush=True)
            raise DigiLockerError(f"digilocker_{why}" if not str(why).startswith("http_") else "digilocker_error")
        return r

    def _signed(self, r: httpx.Response) -> bytes:
        body = r.content
        if len(body) > MAX_XML_BYTES:
            raise DigiLockerError("digilocker_file_too_large")
        sent = r.headers.get("hmac")
        if sent is not None and not hmac.compare_digest(sent.strip(), file_hmac(self.secret, body)):
            raise DigiLockerError("digilocker_bad_hmac")          # changed on the way: don't trust it
        return body

    def token(self, code: str, verifier: str, redirect_uri: str) -> dict:
        r = self._call("POST", f"{self.base}/oauth2/2/token", data={
            "code": code, "grant_type": "authorization_code", "client_id": self.client_id,
            "client_secret": self.secret, "redirect_uri": redirect_uri, "code_verifier": verifier})
        tok = r.json()
        if not tok.get("access_token"):
            raise DigiLockerError("digilocker_no_token")
        return tok

    def eaadhaar(self, access: str) -> bytes | None:
        try:
            r = self._call("GET", f"{self.base}/oauth2/3/xml/eaadhaar", headers={"Authorization": f"Bearer {access}"})
        except DigiLockerError as e:
            if e.code in ("digilocker_aadhaar_not_linked", "digilocker_aadhaar_not_available"):
                return None
            raise
        return self._signed(r)

    def issued(self, access: str) -> list[dict]:
        r = self._call("GET", f"{self.base}/oauth2/2/files/issued", headers={"Authorization": f"Bearer {access}"})
        return r.json().get("items") or []

    def xml(self, access: str, uri: str) -> bytes:
        r = self._call("GET", f"{self.base}/oauth2/1/xml/{quote(uri.strip(), safe='-._~')}",
                       headers={"Authorization": f"Bearer {access}"})
        return self._signed(r)

    def mobile(self, access: str) -> str | None:
        """The mobile number on the DigiLocker account (Get User Details, v2.4), if the token covers it."""
        try:
            r = self._call("GET", f"{self.base}/oauth2/1/user", headers={"Authorization": f"Bearer {access}"})
            m = "".join(ch for ch in str(r.json().get("mobile") or "") if ch.isdigit())
        except (DigiLockerError, ValueError, AttributeError):
            return None                                    # no "userdetails" scope, or no answer: not needed
        return m[-10:] if len(m) >= 10 else None

    def revoke(self, access: str) -> None:
        """Done reading: revoke the token, so nothing more can be read with it. Best effort."""
        try:
            self._call("POST", f"{self.base}/oauth2/1/revoke", auth=(self.client_id, self.secret),
                       data={"token": access, "token_type_hint": "access_token"})
        except DigiLockerError:
            pass

    def finish(self, code: str, verifier: str, redirect_uri: str) -> dict:
        """The applicant's e-Aadhaar and PAN record, parsed. Raises DigiLockerError."""
        access = self.token(code, verifier, redirect_uri)["access_token"]
        try:
            raw = self.eaadhaar(access)
            aadhaar = parse_eaadhaar(raw) if raw else None
            pan = None
            for item in self.issued(access):
                if str(item.get("doctype", "")).strip().upper() == "PANCR" and item.get("uri"):
                    pan = parse_pan(self.xml(access, str(item["uri"])))
                    break
            if aadhaar is None and pan is None:
                raise DigiLockerError("digilocker_nothing_shared")
            return {"aadhaar": aadhaar, "pan": pan, "mobile": self.mobile(access)}
        finally:
            self.revoke(access)


# --------------------------------------------------------------------------- through Cashfree Secure ID

class CashfreeDigiLocker:
    """DigiLocker through Cashfree Secure ID's DigiLocker API: create a link, the applicant signs in to
    DigiLocker and allows sharing, then read the status and the shared Aadhaar and PAN.

    Cashfree asks for 2FA in every environment: either this machine's IP is whitelisted in its
    dashboard, or each call carries x-cf-signature, the client ID and the time encrypted with the
    public key from the dashboard (RSA-OAEP, SHA-1). Pass that key to sign the calls."""
    name = "cashfree-digilocker"

    def __init__(self, base: str, client_id: str, secret: str, public_key: bytes | None = None, *,
                 http: httpx.Client | None = None):
        self.base, self.client_id, self.secret, self.public_key = base.rstrip("/"), client_id, secret, public_key
        self.http = http or httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0))

    def signature(self, at: float | None = None) -> str:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        key = serialization.load_pem_public_key(self.public_key)
        plain = f"{self.client_id}.{int(at or time.time())}".encode()
        sealed = key.encrypt(plain, padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None))
        return base64.b64encode(sealed).decode()

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        headers = {"x-client-id": self.client_id, "x-client-secret": self.secret}
        if self.public_key:
            headers["x-cf-signature"] = self.signature()
        try:
            r = self.http.request(method, f"{self.base}{path}", headers=headers, **kw)
        except httpx.HTTPError as e:
            print(f"  !!! Cashfree unreachable: {type(e).__name__}", flush=True)
            raise DigiLockerError("digilocker_unreachable") from e
        if r.status_code >= 400:
            try:
                j = r.json()
                why = str(j.get("code") or j.get("type") or f"http_{r.status_code}")
                said = str(j.get("message") or "")[:160]
            except ValueError:
                why, said = f"http_{r.status_code}", ""
            # Cashfree's reason in the server window, for whoever set up the keys (never the keys themselves).
            print(f"  !!! Cashfree DigiLocker said {r.status_code} {why}: {said} ({path.split('?')[0]})", flush=True)
            raise DigiLockerError(f"digilocker_{why}" if r.status_code < 500 else "digilocker_error")
        return r

    def begin(self, application_id: str, mobile: str | None = None, return_url: str | None = None) -> tuple[str, str]:
        """The DigiLocker link (valid 10 minutes) and its verification id. A known DigiLocker account
        gets the sign-in screens, anyone else the sign-up ones."""
        vid = f"{application_id}-{secrets.token_hex(4)}"
        flow = "signup"
        if mobile:
            try:
                r = self._call("POST", "/digilocker/verify-account", json={"verification_id": f"{vid}-acct", "mobile_number": mobile})
                if r.json().get("status") == "ACCOUNT_EXISTS":
                    flow = "signin"
            except (DigiLockerError, ValueError):
                pass                                           # sign-up screens also offer "sign in"
        body = {"verification_id": vid, "document_requested": ["AADHAAR", "PAN"], "user_flow": flow,
                "purpose": "Home loan KYC", "company_name": "Nirnay Home Loans"}
        if return_url and return_url.startswith("https://"):  # Cashfree takes https only
            body["redirect_url"] = return_url
        url = self._call("POST", "/digilocker", json=body).json().get("url")
        if not url:
            raise DigiLockerError("digilocker_no_link")
        return url, vid

    def _document(self, vid: str, kind: str) -> tuple[str, dict | None]:
        """("ready", answer), ("wait", None) while Cashfree is still fetching, or ("none", None)."""
        try:
            r = self._call("GET", f"/digilocker/document/{kind}", params={"verification_id": vid})
        except DigiLockerError as e:
            if e.code in ("digilocker_unreachable", "digilocker_error"):
                raise
            return "none", None                                # not in this DigiLocker, or not allowed
        return ("wait", None) if r.status_code == 202 else ("ready", r.json())

    def collect(self, vid: str) -> dict | None:
        """The shared e-Aadhaar and PAN, parsed like finish() does; None while the applicant is still in
        DigiLocker. Raises DigiLockerError when they said no, the link expired, or nothing came."""
        st = self._call("GET", "/digilocker", params={"verification_id": vid}).json()
        status = str(st.get("status") or "").upper()
        if status == "PENDING":
            return None
        if status == "CONSENT_DENIED":
            raise DigiLockerError("digilocker_access_denied")
        if status == "EXPIRED":
            raise DigiLockerError("digilocker_expired")
        if status != "AUTHENTICATED":
            raise DigiLockerError("digilocker_error")
        allowed = {str(d).upper() for d in (st.get("document_consent") or ["AADHAAR", "PAN"])}
        got: dict = {"aadhaar": None, "pan": None}
        for kind, key, parse in (("AADHAAR", "aadhaar", cashfree_aadhaar), ("PAN", "pan", cashfree_pan)):
            if kind not in allowed:
                continue
            state, answer = self._document(vid, kind)
            if state == "wait":
                return None
            got[key] = parse(answer) if answer else None
        if got["aadhaar"] is None and got["pan"] is None:
            raise DigiLockerError("digilocker_nothing_shared")
        return got


def _b64_image(s) -> bytes | None:
    s = str(s or "").strip()
    if not s or s.startswith("http"):
        return None
    if s.startswith("data:") and "," in s:
        s = s.split(",", 1)[1]
    try:
        return base64.b64decode("".join(s.split()), validate=True)
    except ValueError:
        return None


def cashfree_aadhaar(j: dict) -> dict | None:
    """Cashfree's Aadhaar answer, kept the way parse_eaadhaar keeps the XML: the last 4 digits only."""
    name = " ".join(str(j.get("name") or "").split())
    if str(j.get("status") or "").upper() != "SUCCESS" or not name:
        return None                                            # e.g. AADHAAR_NOT_LINKED
    digits = "".join(ch for ch in str(j.get("uid") or "") if ch.isdigit())
    last4 = digits[-4:] if len(digits) >= 4 else None
    sa = j.get("split_address") or {}
    parts: list[str] = []
    for k in ("house", "street", "landmark", "vtc", "po", "subdist", "dist", "state"):
        v = " ".join(str(sa.get(k) or "").split())
        if v and v not in parts:
            parts.append(v)
    pin = str(sa.get("pincode") or "").strip()
    return {"name": name, "dob": _date(j.get("dob")), "gender": _gender(j.get("gender")),
            "address": ", ".join(parts) + (f" {pin}" if pin else ""), "pincode": pin or None,
            "aadhaar_masked": f"XXXX XXXX {last4}" if last4 else None, "photo": _b64_image(j.get("photo_link"))}


def cashfree_pan(j: dict) -> dict | None:
    import re
    pan = str(j.get("pan") or "").strip().upper()
    if str(j.get("status") or "").upper() != "SUCCESS" or not re.fullmatch(PAN_RE, pan):
        return None
    return {"pan": pan, "name": " ".join(str(j.get("name_pan_card") or "").split()), "dob": _date(j.get("dob")),
            "gender": _gender(j.get("gender"))}


# --------------------------------------------------------------------------- reading the files

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(root: ET.Element, name: str) -> ET.Element | None:
    return next((el for el in root.iter() if _local(el.tag) == name), None)


def _xml(data: bytes) -> ET.Element:
    """Parsed with defusedxml: no entities, no external references, whatever the file says."""
    if len(data) > MAX_XML_BYTES:
        raise DigiLockerError("digilocker_bad_file")
    try:
        return safe_fromstring(data)
    except (ET.ParseError, DefusedXmlException) as e:
        raise DigiLockerError("digilocker_bad_file") from e


def _date(s: str | None) -> str | None:
    """DigiLocker writes DD-MM-YYYY (certificates) or DDMMYYYY (token); some files use YYYY-MM-DD."""
    s = (s or "").strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d%m%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _gender(g: str | None) -> str | None:
    g = (g or "").strip().upper()[:1]
    return g if g in ("M", "F", "T") else None


def parse_eaadhaar(data: bytes) -> dict:
    """e-Aadhaar XML (UIDAI's KYC data: UidData with Poi, Poa and Pht), as DigiLocker gives it."""
    root = _xml(data)
    uid, poi, poa, pht = (_find(root, n) for n in ("UidData", "Poi", "Poa", "Pht"))
    if poi is None or not (poi.get("name") or "").strip():
        raise DigiLockerError("digilocker_bad_aadhaar")
    digits = "".join(ch for ch in (uid.get("uid") if uid is not None else "") or "" if ch.isdigit())
    if not digits:                                       # the offline format: referenceId starts with the last 4
        digits = "".join(ch for ch in (root.get("referenceId") or "")[:4] if ch.isdigit())
    last4 = digits[-4:] if len(digits) >= 4 else None
    parts = []
    if poa is not None:
        for k in ("house", "street", "lm", "loc", "vtc", "subdist", "dist", "state"):
            v = (poa.get(k) or "").strip()
            if v and v not in parts:
                parts.append(v)
    pincode = (poa.get("pc") or "").strip() if poa is not None else ""
    photo = None
    if pht is not None and (pht.text or "").strip():
        try:
            photo = base64.b64decode("".join(pht.text.split()))
        except ValueError:
            photo = None
    return {"name": " ".join(poi.get("name").split()), "dob": _date(poi.get("dob")), "gender": _gender(poi.get("gender")),
            "address": ", ".join(parts) + (f" {pincode}" if pincode else ""), "pincode": pincode or None,
            "aadhaar_masked": f"XXXX XXXX {last4}" if last4 else None, "photo": photo}


def parse_pan(data: bytes) -> dict:
    """The PAN Verification Record (doctype PANCR) in DigiLocker's certificate XML format."""
    import re
    root = _xml(data)
    cert = root if _local(root.tag) == "Certificate" else _find(root, "Certificate")
    person = _find(cert, "Person") if cert is not None else None
    pan = (cert.get("number") if cert is not None else "") or ""
    pan = pan.strip().upper()
    if not re.fullmatch(PAN_RE, pan) or person is None:
        raise DigiLockerError("digilocker_bad_pan")
    return {"pan": pan, "name": " ".join((person.get("name") or "").split()), "dob": _date(person.get("dob")),
            "gender": _gender(person.get("gender"))}


# --------------------------------------------------------------------------- sign-ins in progress

_flows: dict[str, dict] = {}
_lock = threading.Lock()


def start(client: Client, application_id: str, return_to: str | None = None, mobile: str | None = None) -> str:
    """The DigiLocker link for this application. The PKCE verifier stays in this process only.
    return_to="app": the callback sends the browser straight back to the phone app (nirnay://).
    mobile: the number the applicant signed in with, filled in on DigiLocker's sign-in page."""
    state = secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    with _lock:
        cutoff = time.time() - FLOW_SECONDS
        for k in [k for k, f in _flows.items() if f["at"] < cutoff or f["application_id"] == application_id]:
            del _flows[k]                                 # one sign-in at a time per application
        _flows[state] = {"application_id": application_id, "verifier": verifier, "challenge": challenge,
                         "redirect_uri": client.redirect_uri, "return_to": return_to, "at": time.time()}
    return client.authorize_url(state, challenge, mobile)


def flow(state: str) -> dict | None:
    """The sign-in this state belongs to, if it's still fresh (without using it up)."""
    with _lock:
        f = _flows.get(state or "")
        return f if f and f["at"] >= time.time() - FLOW_SECONDS else None


def take(state: str) -> dict | None:
    """Use up a sign-in: each state works once."""
    with _lock:
        f = _flows.pop(state or "", None)
    return f if f and f["at"] >= time.time() - FLOW_SECONDS else None


# --------------------------------------------------------------------------- which DigiLocker

def configured() -> bool:
    return all(os.environ.get(k) for k in ("DIGILOCKER_CLIENT_ID", "DIGILOCKER_CLIENT_SECRET", "DIGILOCKER_REDIRECT_URI"))


def cashfree_configured() -> bool:
    return all(os.environ.get(k) for k in ("CASHFREE_CLIENT_ID", "CASHFREE_CLIENT_SECRET"))


def available(sandbox: bool) -> str | None:
    """'digilocker', 'cashfree', 'sandbox' or None (not connected): the app offers DigiLocker only
    when connected."""
    if os.environ.get("NIRNAY_DIGILOCKER", "").strip().lower() == "off":
        return None
    if configured():
        return "digilocker"
    if cashfree_configured():
        return "cashfree"
    return "sandbox" if sandbox else None


_cashfree: tuple[tuple, CashfreeDigiLocker] | None = None


def _cashfree_client() -> CashfreeDigiLocker:
    """One client (and connection pool) for as long as the keys stay the same."""
    global _cashfree
    key_file = os.environ.get("CASHFREE_PUBLIC_KEY_FILE", "").strip()
    conf = (os.environ.get("CASHFREE_VERIFICATION_URL", CASHFREE_SANDBOX), os.environ["CASHFREE_CLIENT_ID"],
            os.environ["CASHFREE_CLIENT_SECRET"], key_file)
    if _cashfree is None or _cashfree[0] != conf:
        key = None
        if key_file:
            with open(key_file, "rb") as f:
                key = f.read()
        _cashfree = (conf, CashfreeDigiLocker(conf[0], conf[1], conf[2], key))
    return _cashfree[1]


def client(sandbox: bool, engine_url: str) -> Client | CashfreeDigiLocker | None:
    """The DigiLocker to use. `engine_url` is this engine's address as the phone sees it (for the
    sandbox's stand-in consent page and callback)."""
    kind = available(sandbox)
    if kind == "digilocker":
        return Client(os.environ.get("DIGILOCKER_BASE_URL", PRODUCTION), os.environ["DIGILOCKER_CLIENT_ID"],
                      os.environ["DIGILOCKER_CLIENT_SECRET"], os.environ["DIGILOCKER_REDIRECT_URI"],
                      purpose=os.environ.get("DIGILOCKER_PURPOSE", PURPOSE),
                      service_name=os.environ.get("DIGILOCKER_SERVICE_NAME", SERVICE_NAME),
                      doctypes=os.environ.get("DIGILOCKER_DOCTYPES", DOCTYPES))
    if kind == "cashfree":
        return _cashfree_client()
    if kind == "sandbox":
        test = test_digilocker()
        engine_url = engine_url.rstrip("/")
        return Client("http://test-digilocker.invalid/public", TestDigiLocker.CLIENT_ID, test.secret,
                      f"{engine_url}/v1/digilocker/callback", http=httpx.Client(transport=httpx.MockTransport(test.handle)),
                      authorize_base=f"{engine_url}/v1/sandbox/digilocker", name="sandbox-digilocker")
    return None


# --------------------------------------------------------------------------- the sandbox's test DigiLocker

SANDBOX_PANS = {"ravi": "BXRPK4821K", "meena": "AFRPS4821K", "arjun": "CMHPM6610Q", "priya": "DNRPN7731F"}


class TestDigiLocker:
    """A stand-in for DigiLocker's API with the fictional demo people, answering the same calls
    (token, e-Aadhaar, issued files, file XML) in-process. Codes and tokens live in memory."""
    CLIENT_ID = "nirnay-sandbox"

    def __init__(self):
        self.secret = secrets.token_urlsafe(24)
        self.codes: dict[str, dict] = {}
        self.tokens: dict[str, str] = {}
        self.revoked: list[str] = []
        self.mobiles: dict[str, str] = {}     # persona -> the mobile on their DigiLocker (tests set it)
        self.lock = threading.Lock()

    def issue_code(self, persona_key: str, challenge: str, redirect_uri: str) -> str:
        code = secrets.token_urlsafe(16)
        with self.lock:
            self.codes[code] = {"persona": persona_key, "challenge": challenge, "redirect_uri": redirect_uri, "at": time.time()}
        return code

    def _json(self, status: int, body: dict) -> httpx.Response:
        return httpx.Response(status, json=body)

    def _file(self, body: bytes) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"Content-Type": "application/xml", "hmac": file_hmac(self.secret, body)})

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth2/2/token") and request.method == "POST":
            from urllib.parse import parse_qs
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            with self.lock:
                c = self.codes.pop(form.get("code", ""), None)
            if (c is None or time.time() - c["at"] > 600 or form.get("client_id") != self.CLIENT_ID
                    or form.get("client_secret") != self.secret or form.get("redirect_uri") != c["redirect_uri"]):
                return self._json(400, {"error": "invalid_grant", "error_description": "Invalid authorization code"})
            challenge = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).decode().rstrip("=")
            if not hmac.compare_digest(challenge, c["challenge"]):
                return self._json(400, {"error": "invalid_grant", "error_description": "PKCE verification failed"})
            token = secrets.token_hex(20)
            with self.lock:
                self.tokens[token] = c["persona"]
            scope = "openid files.issueddocs" + (" userdetails" if c["persona"] in self.mobiles else "")
            return self._json(200, {"access_token": token, "expires_in": 3600, "token_type": "Bearer", "scope": scope})
        auth = request.headers.get("Authorization", "")
        if path.endswith("/oauth2/1/revoke") and request.method == "POST":
            from urllib.parse import parse_qs
            basic = "Basic " + base64.b64encode(f"{self.CLIENT_ID}:{self.secret}".encode()).decode()
            if not hmac.compare_digest(auth, basic):
                return self._json(400, {"error": "invalid_client", "error_description": "The client credentials are invalid"})
            token = (parse_qs(request.content.decode()).get("token") or [""])[0]
            with self.lock:
                if self.tokens.pop(token, None) is not None:
                    self.revoked.append(token)
            return httpx.Response(200)
        with self.lock:
            key = self.tokens.get(auth[7:]) if auth.startswith("Bearer ") else None
        if key is None:
            return self._json(401, {"error": "invalid_token", "error_description": "The access token is invalid"})
        from .sandbox import PERSONAS
        p = PERSONAS[key]
        if path.endswith("/oauth2/1/user"):
            if key not in self.mobiles:
                return self._json(403, {"error": "insufficient_scope",
                                        "error_description": "The request requires higher privileges than provided by the access token"})
            return self._json(200, {"digilockerid": hashlib.sha256(key.encode()).hexdigest()[:36], "name": p.name,
                                    "dob": f"{p.dob:%d%m%Y}", "gender": p.gender, "eaadhaar": "Y", "mobile": self.mobiles[key]})
        if path.endswith("/oauth2/3/xml/eaadhaar"):
            return self._file(sandbox_eaadhaar(p))
        if path.endswith("/oauth2/2/files/issued"):
            pan = SANDBOX_PANS.get(key, "DNRPN7731F")
            return self._json(200, {"items": [{"name": "PAN Verification Record", "type": "file", "size": "", "date": "",
                                               "parent": "", "mime": ["application/xml"], "uri": f"in.gov.pan-PANCR-{pan}",
                                               "doctype": "PANCR", "description": "PAN Verification Record",
                                               "issuerid": "in.gov.pan", "issuer": "Income Tax Department"}]})
        if "/oauth2/1/xml/" in path and "PANCR" in path:
            return self._file(sandbox_pan(p, SANDBOX_PANS.get(key, "DNRPN7731F")))
        return self._json(404, {"error": "invalid_uri", "error_description": "No file found for given URI"})


def sandbox_eaadhaar(p) -> bytes:
    from xml.sax.saxutils import quoteattr as q
    last4 = f"{int(hashlib.sha256(p.key.encode()).hexdigest()[:6], 16) % 10000:04d}"
    house, _, rest = p.address.partition(", ")
    return (f"<Certificate><CertificateData><KycRes ret=\"Y\"><UidData uid=\"xxxxxxxx{last4}\">"
            f"<Poi name={q(p.name)} dob=\"{p.dob:%d-%m-%Y}\" gender=\"{p.gender}\"/>"
            f"<Poa house={q(house)} loc={q(rest.rsplit(',', 2)[0])} vtc={q(p.city)} dist={q(p.city)} state=\"Maharashtra\" "
            f"country=\"India\" pc=\"{p.pincode}\"/></UidData></KycRes></CertificateData></Certificate>").encode()


def sandbox_pan(p, pan: str) -> bytes:
    from xml.sax.saxutils import quoteattr as q
    g = {"M": "M", "F": "F"}.get(p.gender, "T")
    return (f"<Certificate language=\"99\" name=\"PAN Verification Record\" type=\"PANCR\" number=\"{pan}\" "
            f"issueDate=\"{date(2015, 4, 1):%d-%m-%Y}\" status=\"A\"><IssuedBy><Organization name=\"Income Tax Department\"/>"
            f"</IssuedBy><IssuedTo><Person name={q(p.name.upper())} dob=\"{p.dob:%d-%m-%Y}\" gender=\"{g}\"/></IssuedTo>"
            f"</Certificate>").encode()


_test: TestDigiLocker | None = None


def test_digilocker() -> TestDigiLocker:
    global _test
    if _test is None:
        _test = TestDigiLocker()
    return _test
