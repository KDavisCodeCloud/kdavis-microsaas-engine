"""
agents/marketing/lead_qualification.py

Scraper v2 qualification layer (2026-10-01). Pure functions only: no
network, no Supabase, no Brave. Everything here takes a search result (or
already-extracted fields) and returns structured, scored, graded output.

Why a separate module: agents/marketing/mkt_lead_finder.py is already
~1500 lines (well past this repo's 300-line REFACTOR CANDIDATE line), and
all of the logic below is decision logic rather than orchestration -- it
needs to be unit-testable without a Brave key, a database, or a network.

What lives here:
  1. Structured parsing      -- parse_person_result / parse_location
  2. Seniority classification -- classify_seniority, is_negative_title
  3. Firmographics            -- headcount_band, funding_recency_months
  4. Technographics           -- extract_stack
  5. Email confidence grades  -- grade_email
  6. Scoring                  -- fit_score / intent_score + thresholds

THE RULE THAT DRIVES THE DESIGN (2026-10-01, Kelvin): title matching
happens on the TITLE FIELD ONLY, never on a concatenated blob. A person
named "Avery Talent" is not a recruiter; "Head of Internal Audit" is not
an intern; "AEgis Platform Lead" is not an account executive. Every
matcher below is word-boundary anchored and scoped to one field, and
tests/test_lead_qualification.py pins each of those cases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

# ── 1. Structured parsing ────────────────────────────────────────────────

# LinkedIn/Brave result titles arrive in a small number of shapes:
#   "Jane Doe - VP of Engineering - Acme Corp | LinkedIn"
#   "Jane Doe - Acme Corp | LinkedIn"
#   "Jane Doe – Head of Platform – Acme | LinkedIn"
#   "Jane Doe on LinkedIn: we're hiring ..."
# Splitting on the separator set and dropping the site suffix handles all
# of them; what each remaining segment MEANS is decided by the seniority
# classifier below rather than by position alone, because the 2-segment
# shape is genuinely ambiguous (title or company?).
_SEGMENT_SEP_RE = re.compile(r"\s+[-–—|]\s+")
_SITE_SUFFIXES = ("linkedin", "linkedin.com", "li", "xing")

# Every captured word must be Capitalised, so prose after the place name
# stops the match: "Based in Greater Boston Area and leading platform
# work" must yield the place, not the rest of the sentence. A greedy
# [A-Za-z\s]+ here swallowed the whole clause.
_LOCATION_LABEL_RE = re.compile(
    r"\b(?:location|located\s+in|based\s+in)\s*[:\-]?\s*"
    r"((?:[A-Z][A-Za-z.]*)(?:\s+[A-Z][A-Za-z.]*){0,3}"
    r"(?:,\s*(?:[A-Z]{2}|[A-Z][A-Za-z.]*(?:\s+[A-Z][A-Za-z.]*){0,2}))?)",
)
# "Greater Boston Area", "San Francisco Bay Area", "Austin, Texas, United States"
_LOCATION_AREA_RE = re.compile(r"\b((?:Greater\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\s+(?:Area|Metropolitan Area))\b")
_LOCATION_CITY_REGION_RE = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?,\s*(?:[A-Z]{2}|[A-Z][a-z]+(?:\s[A-Z][a-z]+)?))\b")


@dataclass
class ParsedPerson:
    """What was actually parseable out of one search result. Every field
    is Optional and None means "not established" -- never a guess. The
    caller decides what to do with a partial parse; nothing downstream
    may substitute a placeholder (that was the 2026-10-01
    job_posting_title bug)."""

    name: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        """Name + title + company. Location is enrichment, not identity."""
        return bool(self.name and self.title and self.company)


def _strip_site_suffix(segments: list[str]) -> list[str]:
    out = list(segments)
    while out and out[-1].strip().lower().rstrip(".") in _SITE_SUFFIXES:
        out.pop()
    return out


def _looks_like_person_name(text: str) -> bool:
    """Two-to-four capitalised words, no role vocabulary, no company
    suffix. Deliberately conservative: a false negative costs one lead, a
    false positive writes a company name into the `name` field."""
    t = text.strip()
    if not t or len(t) > 60:
        return False
    words = t.split()
    if not 1 < len(words) <= 4:
        return False
    if _COMPANY_SUFFIX_RE.search(t):
        return False
    if classify_seniority(t) != "unknown" or _title_vocabulary_hit(t):
        return False
    # Allow "Jane Doe", "Jane A. Doe", "Jane van Doe"; reject "VP OF SALES"
    if t.isupper():
        return False
    return all(w[0].isupper() or w.lower() in {"van", "von", "de", "der", "da", "del", "la"} for w in words if w)


_COMPANY_SUFFIX_RE = re.compile(
    r"\b(?:inc|inc\.|llc|l\.l\.c\.|ltd|ltd\.|corp|corp\.|corporation|co|co\.|gmbh|b\.v\.|bv|plc|ag|sa|s\.a\.|pty|limited|holdings|group|labs|technologies|systems|software|solutions)\b",
    re.IGNORECASE,
)


def parse_person_result(item: dict) -> ParsedPerson:
    """
    Structured name / title / company / location out of ONE search result.

    Only the result's own `title` field is parsed for name/title/company;
    the snippet is used solely for location. Mixing the two is how a
    snippet phrase like "... hiring a sales intern ..." ends up
    classifying the PERSON as a negative title.
    """
    raw_title = (item.get("title") or "").strip()
    snippet = (item.get("snippet") or "").strip()

    person = ParsedPerson(location=parse_location(snippet))
    if not raw_title:
        return person

    # "Jane Doe on LinkedIn: ..." -- name only, no title/company claim.
    on_linkedin = re.match(r"^(.{2,60}?)\s+on\s+LinkedIn\s*[:|]", raw_title, re.IGNORECASE)
    if on_linkedin:
        candidate = on_linkedin.group(1).strip()
        if _looks_like_person_name(candidate):
            person.name = candidate
        return person

    segments = _strip_site_suffix([s.strip() for s in _SEGMENT_SEP_RE.split(raw_title) if s.strip()])
    if not segments:
        return person

    if _looks_like_person_name(segments[0]):
        person.name = segments[0]
        rest = segments[1:]
    else:
        rest = segments

    if not rest:
        return person

    if len(rest) == 1:
        # Ambiguous single segment: title if it reads like a role,
        # otherwise company. Never both.
        if _title_vocabulary_hit(rest[0]) or classify_seniority(rest[0]) != "unknown":
            person.title = rest[0]
        else:
            person.company = rest[0]
        return person

    # 2+ remaining segments: the first that reads like a role is the
    # title, and the next non-role segment is the company. This survives
    # "Jane Doe - Acme Corp - VP Engineering" as well as the usual order.
    title_idx = next(
        (i for i, s in enumerate(rest) if _title_vocabulary_hit(s) or classify_seniority(s) != "unknown"),
        None,
    )
    if title_idx is None:
        person.company = rest[0]
        return person

    person.title = rest[title_idx]
    company_candidates = [s for i, s in enumerate(rest) if i != title_idx]
    if company_candidates:
        person.company = company_candidates[0]
    return person


def parse_location(text: str) -> Optional[str]:
    """A location from free text, or None. Never a guess."""
    if not text:
        return None
    # Specific shapes first: "Greater Boston Area" and "Austin, TX" are
    # unambiguous, so they win over the looser label match.
    for pattern in (_LOCATION_AREA_RE, _LOCATION_CITY_REGION_RE, _LOCATION_LABEL_RE):
        match = pattern.search(text)
        if match:
            value = match.group(1).strip().rstrip(".,")
            if value:
                return value
    return None


# ── 2. Seniority classification + negative titles ────────────────────────
#
# Ordered most- to least-senior; the FIRST tier whose pattern matches wins,
# so "VP, Engineering (Director level)" classifies as vp rather than
# director. Every pattern is \b-anchored -- see the module docstring for
# why that is not optional.

SENIORITY_TIERS = ("c_level", "vp", "head", "director", "manager", "ic")

_SENIORITY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("c_level", re.compile(
        r"\b(?:c[te]o|cio|ciso|coo|cfo|cpo|cmo|chief\s+\w+(?:\s+\w+)?\s+officer|chief\s+of\s+\w+"
        r"|founder|co-?founder|owner|president|partner|principal\s+(?:engineer|architect)\s*,?\s*(?:cto|ceo)?)\b",
        re.IGNORECASE)),
    ("vp", re.compile(r"\b(?:vp|v\.p\.|vice\s+president|svp|evp|avp)\b", re.IGNORECASE)),
    ("head", re.compile(r"\bhead\s+of\b|\bglobal\s+head\b|\bgroup\s+head\b", re.IGNORECASE)),
    ("director", re.compile(r"\b(?:director|dir\.|sr\.?\s+director|senior\s+director|managing\s+director)\b", re.IGNORECASE)),
    ("manager", re.compile(r"\b(?:manager|mgr\.?|team\s+lead|tech\s+lead|engineering\s+lead|lead\s+engineer|supervisor)\b", re.IGNORECASE)),
    ("ic", re.compile(
        r"\b(?:engineer|developer|architect|administrator|admin|analyst|scientist|specialist"
        r"|consultant|sre|devops|programmer|designer)\b", re.IGNORECASE)),
]

# Vocabulary that marks a segment as A TITLE at all (used by the parser to
# disambiguate title-vs-company), independent of seniority tier.
_TITLE_VOCAB_RE = re.compile(
    r"\b(?:of|at|engineering|platform|infrastructure|technology|product|operations|ops|devops|security"
    r"|data|cloud|software|technical|it|information)\b",
    re.IGNORECASE,
)


def _title_vocabulary_hit(text: str) -> bool:
    """True when a segment contains role vocabulary. 'Head of Platform'
    and 'Engineering Operations' hit; 'Acme Corp' does not."""
    if not text or _COMPANY_SUFFIX_RE.search(text):
        return False
    return bool(_TITLE_VOCAB_RE.search(text)) and not _looks_like_bare_company(text)


def _looks_like_bare_company(text: str) -> bool:
    """A single capitalised token with no role vocabulary reads as a
    company, not a title ('Datadog', 'Stripe')."""
    words = text.split()
    return len(words) == 1 and bool(words) and words[0][:1].isupper()


def classify_seniority(title: Optional[str]) -> str:
    """
    One of SENIORITY_TIERS, or "unknown" when nothing matches.

    Takes the TITLE STRING ONLY. Callers must never pass a name, a
    snippet, or a title+company concatenation -- see module docstring.
    """
    if not title:
        return "unknown"
    for tier, pattern in _SENIORITY_PATTERNS:
        if pattern.search(title):
            return tier
    return "unknown"


# Titles that disqualify a lead outright regardless of seniority: people
# who are not the buyer, and profile states that aren't a job at all.
#
# \b-anchored for a reason, one case per line:
#   intern    -> must not fire on "Internal", "International", "Internet"
#   talent    -> must not fire on a person NAMED Talent (name is a
#                different field; this only ever sees the title)
#   ae / sdr  -> must not fire on "AEgis", "SDRAM"
#   sales     -> must not fire on "Salesforce Platform Director", who IS
#                a buyer for infra work; handled by the explicit
#                _NEGATIVE_EXEMPT_RE carve-out below.
_NEGATIVE_TITLE_PATTERNS: dict[str, re.Pattern] = {
    "recruiting": re.compile(
        r"\b(?:recruit(?:er|ing|ment)|talent\s+(?:acquisition|partner|advisor|sourcer|scout)|talent\b"
        r"|sourcer|headhunter|staffing|hiring\s+manager)\b", re.IGNORECASE),
    "student": re.compile(r"\b(?:intern|internship|student|trainee|apprentice|graduate\s+(?:student|trainee)|co-?op)\b", re.IGNORECASE),
    "open_to_work": re.compile(r"\b(?:open\s+to\s+work|seeking\s+(?:new\s+)?(?:opportunities|work|role)|looking\s+for\s+(?:work|a\s+role)|unemployed|ex-)\b", re.IGNORECASE),
    "sales": re.compile(r"\b(?:account\s+executive|ae|sdr|bdr|business\s+development\s+(?:rep|representative|manager)|sales(?:person|\s+rep|\s+representative|\s+manager|\s+director|\s+lead)?)\b", re.IGNORECASE),
    "agency": re.compile(r"\b(?:freelance|freelancer|contractor\s+at|consultant\s+at\s+self|self-?employed)\b", re.IGNORECASE),
}

# Titles that CONTAIN a negative token but are real buyers. Checked first.
# "Salesforce" is the headline case -- a Salesforce platform owner is
# exactly who buys infra consulting.
_NEGATIVE_EXEMPT_RE = re.compile(
    r"\b(?:salesforce|sales\s*force|sales\s+engineering\s+(?:director|vp|head)|pre-?sales\s+architect)\b",
    re.IGNORECASE,
)


def is_negative_title(title: Optional[str]) -> tuple[bool, Optional[str]]:
    """
    (rejected, reason). Reason is the _NEGATIVE_TITLE_PATTERNS key so
    funnel_stats can attribute every drop to a named cause.

    TITLE FIELD ONLY. Passing a name here is the bug this signature
    exists to prevent.
    """
    if not title:
        return False, None
    if _NEGATIVE_EXEMPT_RE.search(title):
        return False, None
    for reason, pattern in _NEGATIVE_TITLE_PATTERNS.items():
        if pattern.search(title):
            return True, reason
    return False, None


# ── 3. Firmographics ─────────────────────────────────────────────────────

HEADCOUNT_BANDS = ("1-19", "20-49", "50-99", "100-300", "301-1000", "1000+")

_HEADCOUNT_RANGE_RE = re.compile(r"\b(\d{1,5})\s*(?:[-–]|to)\s*(\d{1,5})\s*(?:\+)?\s*employees\b", re.IGNORECASE)
_HEADCOUNT_SINGLE_RE = re.compile(r"\b(\d{1,5})(\+)?\s*employees\b", re.IGNORECASE)
_HEADCOUNT_TEAM_RE = re.compile(r"\bteam\s+of\s+(\d{1,5})\b", re.IGNORECASE)

_FUNDING_RE = re.compile(
    r"\b(?:raised|raises|closed|announced|secured)\b[^.]{0,60}?\b(?:series\s+[a-f]|seed|pre-?seed|round)\b"
    r"|\b(?:series\s+[a-f]|seed|pre-?seed)\s+(?:round|funding|financing)\b",
    re.IGNORECASE,
)
_FUNDING_DATE_RE = re.compile(r"\b(20\d{2})[-/](\d{1,2})\b|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b", re.IGNORECASE)
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def headcount_band(text: Optional[str]) -> Optional[str]:
    """A HEADCOUNT_BANDS value, or None when no headcount is stated.
    None is NOT a disqualifier anywhere -- it means unknown, and the
    scorer treats it as neutral rather than as out-of-band."""
    if not text:
        return None
    match = _HEADCOUNT_RANGE_RE.search(text)
    if match:
        return _band_for(int(match.group(2)))
    match = _HEADCOUNT_SINGLE_RE.search(text) or _HEADCOUNT_TEAM_RE.search(text)
    if match:
        return _band_for(int(match.group(1)))
    return None


def _band_for(n: int) -> str:
    for band in HEADCOUNT_BANDS[:-1]:
        upper = int(band.split("-")[1])
        if n <= upper:
            return band
    return HEADCOUNT_BANDS[-1]


def headcount_in_band(band: Optional[str], low: int, high: int) -> Optional[bool]:
    """True/False when the band is known, None when it isn't. Three-valued
    on purpose -- "unknown" must not read as "out of band"."""
    if not band:
        return None
    # "1000+" is a real band and must evaluate as OUT of a 20-300 band --
    # not as unknown. partition("-") leaves lo_s="1000+" there, so the
    # "+" is stripped before parsing rather than raising into None.
    lo_s, _, hi_s = band.partition("-")
    open_ended = lo_s.endswith("+") or hi_s.endswith("+")
    try:
        lo = int(lo_s.rstrip("+"))
        hi = int(hi_s.rstrip("+")) if hi_s.rstrip("+").isdigit() else 10 ** 6
    except ValueError:
        return None
    if open_ended and not hi_s.rstrip("+").isdigit():
        hi = 10 ** 6
    return not (hi < low or lo > high)


def funding_recency_months(text: Optional[str], today: Optional[date] = None) -> Optional[int]:
    """Months since a funding event mentioned in `text`, or None when no
    funding event OR no date for it is found. Never guesses a date."""
    if not text or not _FUNDING_RE.search(text):
        return None
    today = today or date.today()
    match = _FUNDING_DATE_RE.search(text)
    if not match:
        return None
    if match.group(1):
        year, month = int(match.group(1)), int(match.group(2))
    else:
        month_name = match.group(0).split()[0].lower()
        year, month = int(match.group(3)), _MONTHS.get(month_name, 1)
    if not 1 <= month <= 12:
        return None
    months = (today.year - year) * 12 + (today.month - month)
    return months if months >= 0 else None


# ── 4. Technographics ────────────────────────────────────────────────────

# Canonical name -> \b-anchored pattern. Canonical names are what get
# stored, so "k8s" and "kubernetes" collapse to one tag.
_STACK_PATTERNS: dict[str, re.Pattern] = {
    "AWS": re.compile(r"\b(?:aws|amazon\s+web\s+services)\b", re.IGNORECASE),
    "Azure": re.compile(r"\b(?:azure|microsoft\s+azure)\b", re.IGNORECASE),
    "GCP": re.compile(r"\b(?:gcp|google\s+cloud)\b", re.IGNORECASE),
    "Kubernetes": re.compile(r"\b(?:kubernetes|k8s|eks|aks|gke)\b", re.IGNORECASE),
    "Terraform": re.compile(r"\bterraform\b", re.IGNORECASE),
    "Bicep": re.compile(r"\bbicep\b", re.IGNORECASE),
    "Pulumi": re.compile(r"\bpulumi\b", re.IGNORECASE),
    "Docker": re.compile(r"\bdocker\b", re.IGNORECASE),
    "GitHub Actions": re.compile(r"\bgithub\s+actions\b", re.IGNORECASE),
    "GitLab CI": re.compile(r"\bgitlab\s+ci\b", re.IGNORECASE),
    "Jenkins": re.compile(r"\bjenkins\b", re.IGNORECASE),
    "Azure DevOps": re.compile(r"\bazure\s+devops\b|\bADO\b", re.IGNORECASE),
    "ArgoCD": re.compile(r"\bargo\s*cd\b|\bargocd\b", re.IGNORECASE),
    "Datadog": re.compile(r"\bdatadog\b", re.IGNORECASE),
    "Prometheus": re.compile(r"\bprometheus\b", re.IGNORECASE),
    "Grafana": re.compile(r"\bgrafana\b", re.IGNORECASE),
    "Snowflake": re.compile(r"\bsnowflake\b", re.IGNORECASE),
    "Databricks": re.compile(r"\bdatabricks\b", re.IGNORECASE),
    "Python": re.compile(r"\bpython\b", re.IGNORECASE),
    "Go": re.compile(r"\bgolang\b|\bGo\b(?!\w)", re.IGNORECASE),
}


def extract_stack(text: Optional[str]) -> list[str]:
    """Canonical technology tags found in JD / page text. Sorted for a
    stable stored value; empty list when nothing matches."""
    if not text:
        return []
    return sorted(name for name, pattern in _STACK_PATTERNS.items() if pattern.search(text))


# ── 5. Email confidence grades ───────────────────────────────────────────
#
# core/email_finder.verify_email returns verified|catch_all|unverified|
# invalid. Grades collapse that into the four buckets the send gate cares
# about, and ONLY "valid" is allowed into MKT-O5 (Kelvin, 2026-10-01).

EMAIL_GRADES = ("valid", "risky", "invalid", "unknown")

_VERIFICATION_TO_GRADE = {
    "verified": "valid",
    "catch_all": "risky",
    "invalid": "invalid",
    "unverified": "unknown",
}


def grade_email(
    verification_status: Optional[str],
    *,
    domain_is_catch_all: Optional[bool] = None,
    role_address: Optional[bool] = None,
) -> str:
    """
    One of EMAIL_GRADES.

    A verified address on a KNOWN catch-all domain is downgraded to
    "risky": a catch-all accepts every RCPT TO, so a 250 proves only that
    the domain answers, not that the mailbox exists. That is the whole
    point of catch-all detection, and shipping those as "valid" is how a
    bounce rate climbs quietly.

    Role addresses (info@, sales@, careers@) are capped at "risky" too --
    deliverable, but not a person, and CAN-SPAM/reputation-wise not who
    cold outreach should land on.
    """
    grade = _VERIFICATION_TO_GRADE.get((verification_status or "").lower(), "unknown")
    if grade == "valid" and (domain_is_catch_all or role_address):
        return "risky"
    return grade


_ROLE_LOCALPARTS = frozenset({
    "info", "sales", "support", "contact", "hello", "admin", "careers", "jobs",
    "hr", "recruiting", "team", "office", "help", "billing", "press", "legal",
    "marketing", "noreply", "no-reply", "webmaster", "postmaster", "abuse",
})


def is_role_address(email: Optional[str]) -> bool:
    """True for shared/role mailboxes rather than a named person."""
    if not email or "@" not in email:
        return False
    return email.split("@", 1)[0].strip().lower() in _ROLE_LOCALPARTS


def email_may_send(grade: str) -> bool:
    """The MKT-O5 gate in one place. Only "valid" sends -- risky,
    invalid and unknown never do."""
    return grade == "valid"


# ── 6. Scoring ───────────────────────────────────────────────────────────

@dataclass
class ScoringConfig:
    """Every threshold in one place so tuning never means editing scoring
    logic. Instantiated per run; defaults match Kelvin's 2026-10-01 ICP
    (headcount 20-300, funding within 24 months)."""

    headcount_low: int = 20
    headcount_high: int = 300
    funding_recent_months: int = 24
    fit_threshold: float = 0.50
    intent_threshold: float = 0.40
    target_stack: tuple[str, ...] = ("Azure", "AWS", "Terraform", "Kubernetes", "Bicep", "Azure DevOps", "GitHub Actions")
    seniority_weights: dict[str, float] = field(default_factory=lambda: {
        "c_level": 1.00, "vp": 0.90, "head": 0.85, "director": 0.75,
        "manager": 0.45, "ic": 0.15, "unknown": 0.0,
    })
    posting_fresh_days: int = 14


@dataclass
class Score:
    """A score plus the reasons that produced it. Reasons are stored so a
    rejected lead can always be explained -- 'should work' is not an
    acceptable audit trail in this codebase."""

    value: float
    reasons: list[str] = field(default_factory=list)

    def passes(self, threshold: float) -> bool:
        return self.value >= threshold


def fit_score(
    *,
    seniority: str,
    headcount_band_value: Optional[str] = None,
    stack: Optional[list[str]] = None,
    has_domain: bool = False,
    config: Optional[ScoringConfig] = None,
) -> Score:
    """
    How well this PERSON/COMPANY matches the ICP, in [0, 1].

    Weights: seniority 0.45, headcount-in-band 0.20, stack overlap 0.25,
    resolvable domain 0.10. An unknown headcount contributes a neutral
    half-credit rather than zero -- most legitimate SMB prospects never
    state headcount anywhere a search result can see.
    """
    cfg = config or ScoringConfig()
    reasons: list[str] = []
    total = 0.0

    sen_weight = cfg.seniority_weights.get(seniority, 0.0)
    total += 0.45 * sen_weight
    reasons.append(f"seniority={seniority}({sen_weight:.2f})")

    in_band = headcount_in_band(headcount_band_value, cfg.headcount_low, cfg.headcount_high)
    if in_band is True:
        total += 0.20
        reasons.append(f"headcount={headcount_band_value} in band")
    elif in_band is False:
        reasons.append(f"headcount={headcount_band_value} out of band")
    else:
        total += 0.10
        reasons.append("headcount unknown (neutral)")

    stack = stack or []
    overlap = [s for s in stack if s in cfg.target_stack]
    if overlap:
        ratio = min(1.0, len(overlap) / 3.0)
        total += 0.25 * ratio
        reasons.append(f"stack overlap={','.join(overlap)}")
    else:
        reasons.append("no target stack detected")

    if has_domain:
        total += 0.10
        reasons.append("domain resolved")
    else:
        reasons.append("no domain (manual LinkedIn track)")

    return Score(round(min(1.0, total), 3), reasons)


def company_fit_score(
    *,
    stack: Optional[list[str]] = None,
    size_proxy_ok: bool = False,
    domain_resolved: bool = False,
    intent_boosts: Optional[list[str]] = None,
    exclusion_multiplier: float = 1.0,
    config: Optional[ScoringConfig] = None,
) -> Score:
    """
    How well this COMPANY matches the ICP, with no contact involved.

    Added 2026-10-01 for the two-stage model (Kelvin's decision 1:
    "restructure, do not lower the threshold"). The original fit_score
    weights contact seniority at 0.45, which made sense when a lead WAS a
    person. Under company-first sourcing the company is found first and the
    contact lookup is budgeted and optional, so that weighting silently
    turned "we have not looked for a contact yet" into "this company is a
    bad fit" -- it discarded 23 of 32 companies with matching open roles in
    the first live runs, including ones with six open platform roles.

    Here the company stands on its own evidence: stack overlap 0.35, size
    proxy in band 0.30, resolved domain 0.20, hiring-intent title markers
    0.15. `exclusion_multiplier` comes from the per-product exclusion
    policy, so Cloud Decoded can rank an infra/devtools vendor lower
    without excluding it (decision 3).

    Contact seniority is scored separately by fit_score and used for
    RANKING, never as a gate.
    """
    cfg = config or ScoringConfig()
    reasons: list[str] = []
    total = 0.0

    stack = stack or []
    overlap = [s for s in stack if s in cfg.target_stack]
    if overlap:
        ratio = min(1.0, len(overlap) / 3.0)
        total += 0.35 * ratio
        reasons.append(f"stack overlap={','.join(overlap)}")
    else:
        reasons.append("no target stack detected")

    if size_proxy_ok:
        total += 0.30
        reasons.append("size proxy in band")
    else:
        reasons.append("size proxy out of band")

    if domain_resolved:
        total += 0.20
        reasons.append("domain resolved")
    else:
        reasons.append("no domain resolved")

    intent_boosts = intent_boosts or []
    if intent_boosts:
        total += 0.15 * min(1.0, len(intent_boosts) / 2.0)
        reasons.append(f"hiring-intent markers={','.join(sorted(set(intent_boosts)))}")

    if exclusion_multiplier != 1.0:
        total *= exclusion_multiplier
        reasons.append(f"exclusion multiplier={exclusion_multiplier}")

    return Score(round(min(1.0, total), 3), reasons)


def intent_score(
    *,
    posting_age_days: Optional[int] = None,
    open_role_count: int = 0,
    stack: Optional[list[str]] = None,
    funding_months: Optional[int] = None,
    config: Optional[ScoringConfig] = None,
) -> Score:
    """
    How likely this company is buying NOW, in [0, 1].

    Weights: posting freshness 0.40, number of relevant open roles 0.25,
    target-stack density 0.20, recent funding 0.15. An unknown posting
    age scores zero here -- unlike firmographics, "we can't tell when
    they posted" genuinely is absence of an intent signal.
    """
    cfg = config or ScoringConfig()
    reasons: list[str] = []
    total = 0.0

    if posting_age_days is not None:
        if posting_age_days <= cfg.posting_fresh_days:
            total += 0.40
            reasons.append(f"posting {posting_age_days}d old (fresh)")
        elif posting_age_days <= 30:
            total += 0.20
            reasons.append(f"posting {posting_age_days}d old")
        else:
            reasons.append(f"posting {posting_age_days}d old (stale)")
    else:
        reasons.append("posting age unknown")

    if open_role_count >= 3:
        total += 0.25
        reasons.append(f"{open_role_count} open relevant roles")
    elif open_role_count == 2:
        total += 0.15
        reasons.append("2 open relevant roles")
    elif open_role_count == 1:
        total += 0.08
        reasons.append("1 open relevant role")

    stack = stack or []
    overlap = [s for s in stack if s in cfg.target_stack]
    if overlap:
        total += 0.20 * min(1.0, len(overlap) / 2.0)
        reasons.append(f"JD names {len(overlap)} target technologies")

    if funding_months is not None and funding_months <= cfg.funding_recent_months:
        total += 0.15
        reasons.append(f"funded {funding_months}mo ago")
    elif funding_months is not None:
        reasons.append(f"funded {funding_months}mo ago (stale)")

    return Score(round(min(1.0, total), 3), reasons)


# ── Routing ──────────────────────────────────────────────────────────────

ROUTE_OUTBOUND_EMAIL = "outbound_email"
ROUTE_MANUAL_LINKEDIN = "manual_linkedin"
ROUTE_REJECT = "reject"


def route_lead(
    *,
    email_grade: str,
    has_domain: bool,
    fit: Score,
    intent: Score,
    config: Optional[ScoringConfig] = None,
) -> tuple[str, str]:
    """
    (route, reason). Encodes Kelvin's 2026-10-01 rule: "Domain-less leads
    route to the manual LinkedIn track only" -- they are never dropped
    silently, and they never enter the email path.
    """
    cfg = config or ScoringConfig()
    if not fit.passes(cfg.fit_threshold):
        return ROUTE_REJECT, f"fit {fit.value} < {cfg.fit_threshold}"
    if not intent.passes(cfg.intent_threshold):
        return ROUTE_REJECT, f"intent {intent.value} < {cfg.intent_threshold}"
    if not has_domain:
        return ROUTE_MANUAL_LINKEDIN, "no domain"
    if not email_may_send(email_grade):
        return ROUTE_MANUAL_LINKEDIN, f"email grade={email_grade}"
    return ROUTE_OUTBOUND_EMAIL, "qualified"


def qualify(
    item: dict,
    *,
    jd_text: Optional[str] = None,
    posting_age_days: Optional[int] = None,
    open_role_count: int = 0,
    domain: Optional[str] = None,
    email_verification_status: Optional[str] = None,
    email: Optional[str] = None,
    domain_is_catch_all: Optional[bool] = None,
    config: Optional[ScoringConfig] = None,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """
    The whole qualification pipeline for one search result, as one dict
    ready to merge into an mse_leads row. Pure: no network, no DB.

    Returns keys: name, title, company, location, seniority,
    negative_reason, stack_tags, headcount_band, funding_months,
    email_grade, fit_score, intent_score, score_reasons, route,
    route_reason.
    """
    cfg = config or ScoringConfig()
    person = parse_person_result(item)

    rejected, negative_reason = is_negative_title(person.title)

    text_for_firmographics = jd_text or f"{item.get('title', '')} {item.get('snippet', '')}"
    stack = extract_stack(jd_text) if jd_text else extract_stack(item.get("snippet"))
    band = headcount_band(text_for_firmographics)
    funding = funding_recency_months(text_for_firmographics, today=today)

    grade = grade_email(
        email_verification_status,
        domain_is_catch_all=domain_is_catch_all,
        role_address=is_role_address(email),
    )

    fit = fit_score(
        seniority=classify_seniority(person.title),
        headcount_band_value=band,
        stack=stack,
        has_domain=bool(domain),
        config=cfg,
    )
    intent = intent_score(
        posting_age_days=posting_age_days,
        open_role_count=open_role_count,
        stack=stack,
        funding_months=funding,
        config=cfg,
    )

    if rejected:
        route, route_reason = ROUTE_REJECT, f"negative_title:{negative_reason}"
    else:
        route, route_reason = route_lead(
            email_grade=grade, has_domain=bool(domain), fit=fit, intent=intent, config=cfg
        )

    return {
        "name": person.name,
        "title": person.title,
        "company": person.company,
        "location": person.location,
        "seniority": classify_seniority(person.title),
        "negative_reason": negative_reason,
        "stack_tags": stack,
        "headcount_band": band,
        "funding_months": funding,
        "email_grade": grade,
        "fit_score": fit.value,
        "intent_score": intent.value,
        "score_reasons": fit.reasons + intent.reasons,
        "route": route,
        "route_reason": route_reason,
    }
