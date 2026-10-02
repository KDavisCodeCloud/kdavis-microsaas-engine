"""
agents/marketing/founder_band.py

Employee-count band lookup for FOUNDER-SHAPED TITLES ONLY
(Kelvin's decision, 2026-10-02).

WHY THIS EXISTS. contact_fit gates founder/CEO/co-founder titles on company
size, because a founder owns infrastructure decisions at a 20-person company
and does not take cold outreach about it at a 2,000-person one. Until now the
only size signal was the ATS open-role count (<=10 proxying <=50 people).
That proxy is weak in both directions: a 15-person startup hiring hard shows
12 open roles, and a 600-person company in a freeze shows 2. SingleStore's
CEO reached the queue precisely because a proxy was standing in for a fact.

So for founder titles only -- never for exec-tech or functional titles, which
are valid at any size -- spend ONE Brave query on the company's LinkedIn
company page and read the employee band it states.

  band upper bound <= 50  -> accept
  band upper bound  > 50  -> reject
  nothing parseable       -> needs_review (a human decides; we do NOT guess)

THE TRAP THIS MODULE IS BUILT AROUND. A LinkedIn company-page snippet leads
with FOLLOWERS, not employees:

    "Learnosity | 15,183 followers on LinkedIn. ... | 201-500 employees"

Reading the first number in that snippet yields 15,183 and rejects a company
that might well be 30 people -- or worse, reads "1-10 followers" on a dead
page and ACCEPTS a large company. Follower counts outnumber employee counts
in these snippets, so a naive numeric parse is wrong far more often than it
is right. Every number here must be anchored to the word "employee" to
count, and `followers` is explicitly excluded rather than merely unmatched.

Budget: one query per founder-titled contact, charged to the same
brave_search_queries_used ledger as every other Brave spend.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# <=50 people is the founder-band ceiling (decision 4, 2026-10-01), now
# checked against a stated employee count rather than an open-role proxy.
FOUNDER_MAX_HEADCOUNT = 50

# "11-50 employees", "51-200 employees", "1,001-5,000 employees",
# "10,001+ employees", "2 to 10 employees", "7 employees".
# The trailing \b(?:employees?)\b is mandatory: it is the whole defence
# against reading a follower count as a headcount.
_BAND_RE = re.compile(
    r"(?<![\d,])(\d[\d,]*)\s*(?:-|–|—|\bto\b)\s*(\d[\d,]*)\s*\+?\s*(?:\w+\s+){0,2}?employees?\b"
    r"|(?<![\d,])(\d[\d,]*)\s*\+\s*(?:\w+\s+){0,2}?employees?\b"
    r"|(?<![\d,])(\d[\d,]*)\s*(?:\w+\s+){0,2}?employees?\b",
    re.IGNORECASE,
)

# A snippet may mention followers AND employees. Follower phrases are cut out
# before parsing so a "1-10 followers" fragment can never be read as a band.
_FOLLOWERS_RE = re.compile(r"(?<![\d,])\d[\d,]*\s*\+?\s*followers?\b", re.IGNORECASE)

_COMPANY_URL_RE = re.compile(r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/", re.IGNORECASE)


@dataclass
class EmployeeBand:
    """A parsed employee range. `upper` is None for an open-ended band
    ("10,001+ employees"), which is unambiguously over any small-company
    threshold."""

    lower: int
    upper: Optional[int]
    raw: str

    @property
    def label(self) -> str:
        return f"{self.lower}-{self.upper}" if self.upper is not None else f"{self.lower}+"

    def within(self, max_headcount: int) -> bool:
        """True only if the WHOLE band sits at or below max_headcount.

        The upper bound decides, not the lower: "51-200" contains no value
        at or below 50, and an open-ended band never qualifies.
        """
        return self.upper is not None and self.upper <= max_headcount


@dataclass
class FounderBandVerdict:
    """accepted=False with needs_review=True means 'unknown', not 'no'."""

    accepted: bool
    needs_review: bool
    band: Optional[EmployeeBand]
    reason: str
    queries_used: int = 0


def _int(raw: str) -> int:
    return int(raw.replace(",", ""))


def parse_employee_band(text: Optional[str]) -> Optional[EmployeeBand]:
    """Read a LinkedIn employee band out of free text, or None.

    Follower counts are removed before matching -- see this module's
    docstring for why that is the single most important line here.
    """
    if not text:
        return None
    cleaned = _FOLLOWERS_RE.sub(" ", text)
    match = _BAND_RE.search(cleaned)
    if not match:
        return None
    lo_lo, lo_hi, open_ended, exact = match.groups()
    if lo_lo and lo_hi:
        return EmployeeBand(_int(lo_lo), _int(lo_hi), match.group(0).strip())
    if open_ended:
        return EmployeeBand(_int(open_ended), None, match.group(0).strip())
    return EmployeeBand(_int(exact), _int(exact), match.group(0).strip())


def _names_company(text: str, company: str) -> bool:
    """Guard against Brave returning a different company's page. Compares
    on alphanumerics only, so "Learnosity Ltd." still matches "Learnosity"."""
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    a, b = norm(text), norm(company)
    return bool(a and b and b in a)


def lookup_employee_band(
    company: Optional[str],
    scraper,
    *,
    stats: Optional[dict] = None,
    sleep: Optional[Callable[[float], None]] = None,
) -> FounderBandVerdict:
    """ONE Brave query: site:linkedin.com/company {company}.

    Only results that are actually LinkedIn company URLs and that name the
    company are read, so a stray directory listing cannot set a headcount.
    """
    if not company:
        return FounderBandVerdict(False, True, None, "no company name to look up", 0)
    if scraper is None or not getattr(scraper, "api_key", None):
        # No key is 'unknown', never 'reject' -- same graceful-skip shape as
        # every other quota/credential-gated path in this codebase.
        return FounderBandVerdict(False, True, None, "Brave is not configured", 0)

    query = f"site:linkedin.com/company {company}"
    if sleep is not None:
        sleep(0)
    try:
        items = scraper._search(query)
    except Exception as exc:
        log.warning("[FounderBand] Brave lookup failed for %r: %s", company, exc)
        return FounderBandVerdict(False, True, None, f"Brave lookup failed: {exc}", 1)

    if stats is not None:
        stats["brave_founder_band_queries"] = stats.get("brave_founder_band_queries", 0) + 1

    for item in items:
        link = item.get("link") or ""
        if not _COMPANY_URL_RE.match(link):
            continue
        blob = f"{item.get('title') or ''} {item.get('snippet') or ''}"
        if not _names_company(blob, company):
            continue
        band = parse_employee_band(blob)
        if band is None:
            continue
        if band.within(FOUNDER_MAX_HEADCOUNT):
            return FounderBandVerdict(
                True, False, band,
                f"LinkedIn states {band.label} employees (<= {FOUNDER_MAX_HEADCOUNT})", 1)
        return FounderBandVerdict(
            False, False, band,
            f"LinkedIn states {band.label} employees (> {FOUNDER_MAX_HEADCOUNT})", 1)

    return FounderBandVerdict(
        False, True, None,
        "no employee band found on a matching LinkedIn company page", 1)
