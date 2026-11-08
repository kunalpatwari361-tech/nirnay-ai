"""Read a credit report the applicant uploads (rules, no model).

Used when no credit bureau is connected, or the bureau pull failed. Each bureau gives a free
report once a year (TransUnion CIBIL, Experian, Equifax, CRIF High Mark), and the applicant
downloads it as a PDF. The engine reads the bureau, the score, the report date, the name and
the PAN. intake.py checks the name and PAN against the application like any other document,
and only a recent, verified report fills the credit score. An officer still confirms the score
with the bureau before the loan is sanctioned (R-BUR-07).
"""
from __future__ import annotations

import re
from datetime import date

from . import fields

MAX_AGE_DAYS = 90            # an older report is out of date: the applicant downloads a new one
BUREAUS = (                  # (key, name shown, how the report names it)
    ("cibil", "TransUnion CIBIL", r"cibil|trans\s*union"),
    ("experian", "Experian", r"experian"),
    ("equifax", "Equifax", r"equifax"),
    ("crif", "CRIF High Mark", r"crif|high\s*mark"),
)
REPORT_WORDS = re.compile(r"credit\s*(?:information\s*)?(?:score|report)|\bcir\b|consumer\s*credit", re.I)
MONTH_NUM = {m: i + 1 for i, m in enumerate(fields.MONTHS.split("|"))}

# The report's own date, not the date of birth or an account's dates.
# Labels stay on one line ([ \t], not \s), so "Credit Report" and the "Date ..." line below it aren't one label.
DATE_LABEL = re.compile(r"(?:report[ \t]*date|date[ \t]*of[ \t]*(?:report|issue)|(?:report[ \t]*)?generated[ \t]*on|"
                        r"issued[ \t]*on|as[ \t]*on|\bdate[ \t]*:)[ \t]*[:.\-]?[ \t]*", re.I)
NUMERIC_DATE = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})")
WORD_DATE = re.compile(rf"(\d{{1,2}})(?:st|nd|rd|th)?[\s\-,]*({fields.MONTHS})[a-z]*[\s\-,]*(\d{{4}})", re.I)
WORD_DATE_US = re.compile(rf"({fields.MONTHS})[a-z]*[\s\-]*(\d{{1,2}})(?:st|nd|rd|th)?,?[\s\-]*(\d{{4}})", re.I)
NAME_LINE = re.compile(r"^[ \t]*(?:consumer|customer|full|applicant)?[ \t]*name[ \t]*[:.\-]?[ \t]*(.+)$", re.I | re.M)
RANGE = re.compile(r"\b\d{3}\s*(?:-|–|to)\s*\d{3}\b")
SCORE_WORD = re.compile(r"score", re.I)
THREE_DIGITS = re.compile(r"(?<![\d,.])(\d{3})(?![\d,.])")


def bureau(text: str) -> tuple[str, str] | None:
    """(key, name) of the bureau whose report this is, or None when it isn't a credit report."""
    if not REPORT_WORDS.search(text):
        return None
    for key, name, rx in BUREAUS:
        if re.search(rx, text, re.I):
            return key, name
    return None


def score(text: str) -> int | None:
    """The credit score: the first 300-900 number shortly after the word "score". The scale
    ("300-900", "300 to 900") and dates are taken out first, so they aren't read as the score."""
    clean = RANGE.sub(" ", NUMERIC_DATE.sub(" ", text))
    for m in SCORE_WORD.finditer(clean):
        for n in THREE_DIGITS.finditer(clean[m.end(): m.end() + 120]):
            if 300 <= int(n.group(1)) <= 900:
                return int(n.group(1))
    return None


def _to_date(day: str, month: str | int, year: str) -> date | None:
    try:
        mon = month if isinstance(month, int) else (int(month) if month.isdigit() else MONTH_NUM[month[:3].lower()])
        return date(int(year), mon, int(day))
    except (KeyError, ValueError):
        return None


def report_date(text: str) -> date | None:
    """The date the report was made, from a label such as "Report Date" or "Date of Issue"."""
    for m in DATE_LABEL.finditer(text):
        after = text[m.end(): m.end() + 30]
        for rx, order in ((NUMERIC_DATE, (0, 1, 2)), (WORD_DATE, (0, 1, 2)), (WORD_DATE_US, (1, 0, 2))):
            d = rx.match(after)
            if d:
                g = d.groups()
                found = _to_date(g[order[0]], g[order[1]], g[order[2]])
                if found:
                    return found
    return None


def name(text: str) -> str | None:
    for m in NAME_LINE.finditer(text):
        v = re.split(r"\s{2,}|\t|\bDOB\b|\bdate\b|\bgender\b", m.group(1), flags=re.I)[0].strip(" :.-")
        # A label on its own ("Name of the lender") is not the applicant's name.
        if v and not re.search(r"lender|member|bank|institution|of\s+the|number", v, re.I) and re.search(r"[A-Za-z]{2}", v):
            return v
    return None


def read(text: str) -> dict:
    """What the report says. Missing fields are left out."""
    who = bureau(text)
    if who is None:
        return {}
    out: dict = {"bureau": who[1]}
    s = score(text)
    if s is not None:
        out["score"] = str(s)
    d = report_date(text)
    if d is not None:
        out["reportDate"] = d.isoformat()
    n = name(text)
    if n:
        out["name"] = n
    pan = fields._find(fields.PAN, text.upper())
    if pan:
        out["pan"] = pan
    return out


def check(found: dict, today: date) -> tuple[str, str | None]:
    """("pass", None), or ("refer", reason): R-DOC-01 not a credit report, R-DOC-02 the score,
    name or PAN couldn't be read, R-DOC-06 too old (or its date couldn't be read)."""
    if not found.get("bureau"):
        return "refer", "R-DOC-01"
    if not found.get("score") or not (found.get("name") or found.get("pan")):
        return "refer", "R-DOC-02"
    if not found.get("reportDate"):
        return "refer", "R-DOC-06"
    age = (today - date.fromisoformat(found["reportDate"])).days
    if age > MAX_AGE_DAYS or age < -1:          # older than three months, or dated in the future
        return "refer", "R-DOC-06"
    return "pass", None
