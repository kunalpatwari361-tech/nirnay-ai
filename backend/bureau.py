"""The credit report, pulled from a credit bureau with the applicant's consent, using their PAN.

NIRNAY_BUREAU picks the source:
- `sandbox`: the fictional demo people's reports. The default while NIRNAY_SANDBOX=1, and only
  allowed then.
- `decentro`: Decentro's Credit Report API (docs.decentro.tech, Credit Bureau > Credit Report),
  which reaches Equifax, Experian or CRIF:
      POST {DECENTRO_BASE_URL}/v2/financial_services/credit_bureau/credit_report
      headers client_id, client_secret; body reference_id, consent, consent_purpose, name,
      mobile, inquiry_purpose, date_of_birth, address, pincode, document_type=PAN,
      document_id, bureau_code
  Needs DECENTRO_CLIENT_ID and DECENTRO_CLIENT_SECRET. DECENTRO_BASE_URL is
  https://in.staging.decentro.tech (test reports, the default) or https://in.decentro.tech (live).
  DECENTRO_BUREAU is EQ (Equifax, the default), EX (Experian) or CR (CRIF).
- unset in real mode: no bureau is connected, and the applicant may type a score in (self-declared).

Only a summary is kept (score, accounts, overdue, enquiries, EMIs) with the provider's reference
for the pull, not the full report. A report from Decentro's staging server is test data; the
policy gate refers any decision made on it.
"""
from __future__ import annotations

import os
import secrets
from datetime import date, datetime, timedelta

import httpx

TIMEOUT = httpx.Timeout(30.0, connect=5.0)
STAGING = "https://in.staging.decentro.tech"
CONSENT_PURPOSE = "Credit report for a home loan application, with the applicant's consent (Nirnay)"
BAD_STATUS = {"SUB", "DBT", "LSS", "SMA"}        # substandard, doubtful, loss, special mention


class BureauError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _num(x) -> float:
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _day(s) -> date | None:
    s = str(s or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d%m%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _late(status) -> bool:
    s = str(status or "").strip().upper()
    if s.isdigit():
        return int(s) > 0                      # days past due
    return s in BAD_STATUS


def empty(source: str, status: str, **extra) -> dict:
    return {"source": source, "score": None, "status": status, "history_years": None, "total_accounts": 0,
            "active_accounts": 0, "overdue_accounts": 0, "recent_delinquencies": 0, "enquiries_6m": 0,
            "new_accounts_6m": 0, "outstanding": 0, "sanctioned_total": 0, "disbursed_total": 0,
            "avg_account_age_years": None, "has_credit_card": None, "existing_emi": 0.0, "write_offs": 0, **extra}


def summarise(body: dict, today: date, source: str) -> dict:
    """Decentro's credit report JSON, reduced to what the gates and models use."""
    data = body.get("data") or {}
    lst = (data.get("cCRResponse") or {}).get("cIRReportDataLst") or []
    first = lst[0] if lst else {}
    ref = {"reference": body.get("decentroTxnId"), "report_order": data.get("reportOrderNumber") or None}
    rep = first.get("cIRReportData")
    if not rep:                                  # "Consumer not found in bureau": new to credit
        return empty(source, "no_history", **ref)
    score = None
    for s in rep.get("scoreDetails") or []:
        v = str(s.get("value", "")).strip()
        if v.lstrip("-").isdigit() and int(v) > 0:
            score = int(v)
            break
    accounts = rep.get("retailAccountDetails") or []
    opened = [d for d in (_day(a.get("dateOpened")) for a in accounts) if d]
    ages = [(today - d).days / 365.25 for d in opened]
    recent = today - timedelta(days=183)
    late = 0
    for a in accounts:
        for h in a.get("history48Months") or []:
            try:
                mm, yy = str(h.get("key", "")).split("-")
                month = date(2000 + int(yy), int(mm), 1)
            except ValueError:
                continue
            if (today - month).days <= 366 and _late(h.get("paymentStatus")):
                late += 1
                break
    open_accounts = [a for a in accounts if str(a.get("open", "")).strip().lower() in ("yes", "y", "true")]
    cards = [a for a in accounts if "credit card" in str(a.get("accountType", "")).lower()]
    emi = sum(_num(a.get("installmentAmount")) for a in open_accounts if a not in cards)
    enquiries = [e for e in rep.get("enquiries") or [] if (_day(e.get("date")) or date.min) >= recent]
    summary = rep.get("retailAccountsSummary") or {}
    return {"source": source, "score": score, "status": "scored" if score else "no_history",
            "history_years": round(max(ages), 1) if ages else None, "total_accounts": len(accounts),
            "active_accounts": len(open_accounts),
            "overdue_accounts": sum(1 for a in accounts if _num(a.get("pastDueAmount")) > 0),
            "recent_delinquencies": late, "enquiries_6m": len(enquiries),
            "new_accounts_6m": sum(1 for d in opened if d >= recent),
            "outstanding": round(sum(_num(a.get("balance")) for a in open_accounts)),
            "sanctioned_total": round(sum(_num(a.get("sanctionAmount")) for a in accounts)),
            "disbursed_total": round(sum(_num(a.get("sanctionAmount")) for a in accounts)),
            "avg_account_age_years": round(sum(ages) / len(ages), 1) if ages else None,
            "has_credit_card": bool(cards), "existing_emi": round(emi, 2),
            "write_offs": int(_num(summary.get("noOfWriteOffs"))), **ref}


class Decentro:
    name = "decentro"
    PATH = "/v2/financial_services/credit_bureau/credit_report"

    def __init__(self, client: httpx.Client | None = None):
        self.id, self.secret = os.environ["DECENTRO_CLIENT_ID"], os.environ["DECENTRO_CLIENT_SECRET"]
        self.base = os.environ.get("DECENTRO_BASE_URL", STAGING).rstrip("/")
        self.bureau = os.environ.get("DECENTRO_BUREAU", "EQ").strip().upper()
        if self.bureau not in ("EQ", "EX", "CR"):
            raise KeyError("DECENTRO_BUREAU")
        self.http = client or httpx.Client(timeout=TIMEOUT)

    @property
    def source(self) -> str:
        return f"decentro-{'staging' if 'staging' in self.base else 'live'}-{self.bureau.lower()}"

    def pull(self, who: dict, today: date) -> dict:
        body = {"reference_id": f"nirnay-{who['application_id']}-{secrets.token_hex(4)}", "consent": True,
                "consent_purpose": CONSENT_PURPOSE, "name": who["name"][:40], "mobile": who["mobile"],
                "inquiry_purpose": os.environ.get("DECENTRO_INQUIRY_PURPOSE", "HL"), "document_type": "PAN",
                "document_id": who["pan"], "bureau_code": self.bureau}
        if who.get("dob"):
            body["date_of_birth"] = who["dob"]
        if who.get("address"):
            body.update(address=who["address"][:200], address_type="H")
        if who.get("pincode"):
            body["pincode"] = who["pincode"]
        try:
            r = self.http.post(self.base + self.PATH, json=body, headers={"client_id": self.id, "client_secret": self.secret})
        except httpx.HTTPError as e:
            print(f"  !!! Decentro unreachable: {type(e).__name__}", flush=True)
            raise BureauError("bureau_unreachable") from e
        try:
            j = r.json()
        except ValueError:
            j = {}
        if r.status_code >= 400 or str(j.get("status", "")).upper() == "FAILURE":
            print(f"  !!! Decentro refused the credit report ({r.status_code}): "
                  f"{j.get('responseCode') or j.get('response_code') or ''} {j.get('message', '')}".rstrip(), flush=True)
            raise BureauError("bureau_failed")
        return summarise(j, today, self.source)


class SandboxBureau:
    name = "sandbox"
    source = "sandbox-bureau"

    def pull(self, who: dict, today: date) -> dict:
        from .sandbox import PERSONAS, bureau
        p = PERSONAS.get(who.get("persona") or "priya", PERSONAS["priya"])
        return {**empty(self.source, "scored"), **bureau(p)}


def provider(sandbox: bool):
    """The bureau to pull from, or None when none is connected (real mode without keys)."""
    name = os.environ.get("NIRNAY_BUREAU", "sandbox" if sandbox else "").strip().lower()
    if name == "sandbox":
        return SandboxBureau() if sandbox else None        # test reports never decide real applications
    if name == "decentro":
        try:
            return Decentro()
        except KeyError:
            print("  !!! NIRNAY_BUREAU=decentro but DECENTRO_CLIENT_ID / DECENTRO_CLIENT_SECRET are not set", flush=True)
            return None
    return None
