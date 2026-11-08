"""
agents/marketing/buyer_gate.py

The buyer-only gate (Kelvin, 2026-11-07: "the outbound system targets
workers, not buyers").

One deterministic module answers the three questions every outbound lead
must clear, in order, before a draft is spent on it or an email is sent:

  1. COMPANY  -- is this a real hiring company we may sell to? Not a job
                 board, an ATS host, a staffing/recruiting firm, an MSP or
                 DevOps consultancy (competitors), or a named excluded
                 employer.
  2. CONTACT  -- is this person the budget owner? An allow-list of
                 leadership titles, a deny-list of individual-contributor
                 and non-buyer titles, and everything else AMBIGUOUS.
                 Ambiguous is never promoted: it goes to the Find the Buyer
                 lane for a human to resolve.
  3. EMAIL    -- may this address enter the MKT-O5 send queue? Only a
                 "valid" grade (MX found + SMTP RCPT accepted on a domain
                 that is NOT catch-all). Catch-all ("risky"), pattern
                 guesses and unknowns never reach the queue.

Why the old rule targeted workers: contact_fit.FUNCTIONAL_TITLE_RE accepted
"Platform Engineer", "DevOps Engineer", "Cloud Architect" and "SRE Lead" at
any company size, and Cloud Decoded's mse_icp_configs.job_titles literally
listed "SRE", "DevOps engineer", "platform engineer". Both are replaced by
this module (contact_fit.evaluate_contact_fit now delegates here).

Pure: no network, no DB. Matching is by TITLE (and company facts), never by
a person's name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
AMBIGUOUS = "ambiguous"

# CEO is a buyer only at a small company (<50 people). Headcount is almost
# never known for free, so total open roles is the proxy (<=10 open roles),
# the same proxy contact_fit has used since 2026-10-01.
CEO_MAX_HEADCOUNT = 50
CEO_MAX_OPEN_ROLES = 10


@dataclass
class TitleVerdict:
    verdict: str            # allow | deny | ambiguous
    reason: str
    category: Optional[str] = None   # exec_tech | eng_leader | founder | ceo_small

    @property
    def accepted(self) -> bool:
        return self.verdict == ALLOW


# ── 2. Contact title ─────────────────────────────────────────────────────

# Hard denies: never a buyer whatever else the title says. Checked first.
_HARD_DENY: list[tuple[str, re.Pattern]] = [
    ("job_seeker", re.compile(
        r"\bopen\s+to\s+work\b|\bseeking\s+(?:new\s+)?(?:opportunit|role|position|work)|"
        r"\blooking\s+for\s+(?:new\s+)?(?:work|a\s+(?:new\s+)?role|opportunit)|\bunemployed\b|"
        r"\bactively\s+looking\b|\bavailable\s+for\s+hire\b|^\s*ex-|\bformer\b", re.I)),
    ("intern", re.compile(r"\bintern(?:ship)?\b|\bstudent\b|\btrainee\b|\bapprentice\b", re.I)),
    ("recruiting", re.compile(r"\brecruit(?:er|ing|ment)\b|\btalent\b|\bsourcer\b|\bheadhunter\b", re.I)),
    ("people_hr", re.compile(r"\bpeople\b|\bHR\b|\bhuman\s+resources\b", re.I)),
    ("consultant", re.compile(r"\bconsultant\b|\bfreelanc(?:e|er)\b|\bcontractor\b|\bself[- ]employed\b", re.I)),
    ("analyst", re.compile(r"\banalyst\b", re.I)),
    ("specialist", re.compile(r"\bspecialist\b", re.I)),
    ("administrator", re.compile(r"\badministrator\b|\bsysadmin\b|\badmin\b", re.I)),
    # Senior, but not the infrastructure budget owner.
    ("wrong_function", re.compile(
        r"\b(?:sales|marketing|growth|revenue|finance|financial|legal|counsel|design|designer"
        r"|customer\s+success|support|business\s+development|partnerships?|product\s+(?:manager|management|marketing)"
        r"|data\s+science|analytics|operations\s+manager|chief\s+(?:operating|marketing|revenue|financial|people|product)\s+officer"
        r"|COO|CMO|CRO|CFO|CPO)\b", re.I)),
]

# Leadership titles that own infrastructure budget. Each match is REMOVED
# from the title before the IC-deny check, so "Director of SRE" is a buyer
# while "SRE Manager" is not, and "VP Engineering" never trips "engineer".
_ENG_FUNCTION = (r"(?:engineering|infrastructure|platform(?:\s+engineering)?|devops|dev\s*ops|SRE"
                 r"|site\s+reliability(?:\s+engineering)?|cloud(?:\s+infrastructure)?|technology"
                 r"|technical\s+operations)")
_ALLOW: list[tuple[str, re.Pattern]] = [
    ("exec_tech", re.compile(r"\bCTO\b|\bchief\s+technology\s+officer\b|\bchief\s+technical\s+officer\b"
                             r"|\bchief\s+architect\b", re.I)),
    ("eng_leader", re.compile(
        rf"\b(?:(?:senior|sr\.?|executive|group)\s+)?(?:S?VP|EVP|vice\s+president|head|director)\b"
        rf"(?:\s*,\s*|\s+of\s+|\s+|\s*-\s*|\s*/\s*){_ENG_FUNCTION}\b", re.I)),
    ("eng_leader", re.compile(
        rf"\b{_ENG_FUNCTION}\s+(?:S?VP|vice\s+president|director|head)\b", re.I)),
    ("founder", re.compile(r"\bco-?\s?founder\b|\bfounder\b|\bfounding\s+partner\b", re.I)),
]
_CEO_RE = re.compile(r"\bCEO\b|\bchief\s+executive(?:\s+officer)?\b", re.I)

_TAG_RE = re.compile(r"<[^>]*>")

# Individual-contributor tokens. Checked on what is LEFT after removing
# the allow-listed leadership phrase.
_IC_DENY = re.compile(
    r"\bengineer\b|\bdeveloper\b|\bdev\b|\bprogrammer\b|\bSRE\b|\barchitect\b|\btechnician\b"
    r"|\bstaff\b|\bprincipal\b|\bmember\s+of\s+technical\s+staff\b|\bMTS\b", re.I)


def classify_buyer_title(
    title: Optional[str],
    *,
    headcount: Optional[int] = None,
    open_role_count: Optional[int] = None,
) -> TitleVerdict:
    """allow | deny | ambiguous for one contact title. Title only, never a name."""
    if not title or not title.strip():
        return TitleVerdict(AMBIGUOUS, "no title")
    t = " ".join(_TAG_RE.sub(" ", title).split())
    if not t:
        return TitleVerdict(AMBIGUOUS, "no title")

    for reason, pattern in _HARD_DENY:
        m = pattern.search(t)
        if m and not (reason == "wrong_function" and _ALLOW[0][1].search(t)
                      and not re.search(r"\b(?:COO|CMO|CRO|CFO|CPO)\b", t)):
            return TitleVerdict(DENY, f"{reason} ({m.group(0).strip()}): {t!r}")

    # Leadership allow-list.
    remainder = t
    category = None
    for cat, pattern in _ALLOW:
        if pattern.search(remainder):
            category = category or cat
            remainder = pattern.sub(" ", remainder)
    ceo = bool(_CEO_RE.search(remainder))
    if ceo:
        remainder = _CEO_RE.sub(" ", remainder)

    ic = _IC_DENY.search(remainder)
    if category:
        if ic:
            # "Founder & Principal Engineer": leadership AND an IC token. Not
            # promoted automatically -- a human decides.
            return TitleVerdict(AMBIGUOUS, f"leadership title also names an IC role ({ic.group(0)}): {t!r}")
        return TitleVerdict(ALLOW, f"{category}: {t!r}", category)

    if ceo:
        if headcount is not None:
            if headcount < CEO_MAX_HEADCOUNT:
                return TitleVerdict(ALLOW, f"CEO at a {headcount}-person company", "ceo_small")
            return TitleVerdict(DENY, f"CEO at a {headcount}-person company (>= {CEO_MAX_HEADCOUNT})")
        if open_role_count is not None and open_role_count > CEO_MAX_OPEN_ROLES:
            return TitleVerdict(DENY, f"CEO at a company with {open_role_count} open roles "
                                      f"(> {CEO_MAX_OPEN_ROLES}, not a <50-person company)")
        # Few open roles does NOT prove <50 people (Learnosity, ~150 staff,
        # had 2). Without a stated headcount a CEO is not promoted.
        return TitleVerdict(AMBIGUOUS, f"CEO but headcount not confirmed < {CEO_MAX_HEADCOUNT}: {t!r}")

    if ic:
        return TitleVerdict(DENY, f"individual contributor ({ic.group(0)}): {t!r}")

    return TitleVerdict(AMBIGUOUS, f"not on the buyer allow-list: {t!r}")


# ── 1. Company ───────────────────────────────────────────────────────────

# Hosts that are never the hiring company. Registrable-domain match.
AGGREGATOR_DOMAINS = frozenset({
    "linkedin.com", "indeed.com", "ziprecruiter.com", "glassdoor.com", "monster.com",
    "simplyhired.com", "dice.com", "index.dev", "builtin.com", "builtinnyc.com",
    "builtinboston.com", "builtinchicago.org", "builtinla.com", "builtinsf.com",
    "builtinseattle.com", "builtinaustin.com", "builtincolorado.com", "wellfound.com",
    "angel.co", "otta.com", "welcometothejungle.com", "remoteok.com", "remoteok.io",
    "weworkremotely.com", "remote.co", "remote.com", "flexjobs.com", "himalayas.app",
    "workingnomads.com", "jobspresso.co", "remotive.com", "remotive.io", "nodesk.co",
    "4dayweek.io", "jobgether.com", "talent.com", "careerbuilder.com", "jooble.org",
    "adzuna.com", "snagajob.com", "hired.com", "crunchboard.com", "ycombinator.com",
    "workatastartup.com", "hackernews.com", "news.ycombinator.com", "upwork.com",
    "toptal.com", "turing.com", "arc.dev", "lemon.io", "andela.com", "gun.io",
    "crossover.com", "jobs.lever.co", "ashbyhq.com", "greenhouse.io", "lever.co",
    "workable.com", "apply.workable.com", "breezy.hr", "bamboohr.com", "smartrecruiters.com",
    "jobvite.com", "icims.com", "myworkdayjobs.com", "workday.com", "recruitee.com",
    "teamtailor.com", "notion.site", "notion.so", "github.io", "medium.com", "substack.com",
    "crunchbase.com", "google.com", "facebook.com", "twitter.com", "x.com",
})

# Named employers Kelvin excludes outright (2026-11-07).
EXCLUDED_EMPLOYERS = ("corvel", "honeywell", "boeing")

# Name-level competitor / non-buyer signals. Prose-level detection lives in
# company_classification (staffing, MSP, consultancy, cloud partner).
_COMPETITOR_NAME_RE = re.compile(
    r"\b(?:staffing|recruit(?:ing|ment|ers)?|talent\s+(?:group|solutions|partners)|headhunt\w*"
    r"|consult(?:ing|ancy|ancies|ants?)|advisors?|advisory|managed\s+(?:services?|IT)|MSP"
    r"|devops\s+(?:agency|services|consulting|company)|cloud\s+(?:services|consulting|partners?)"
    r"|IT\s+services|outsourc\w*|nearshore|offshore|software\s+(?:agency|house|development\s+company)"
    r"|dev\s+shop|systems?\s+integrator)\b",
    re.I,
)

# Known IT-staffing / dev-agency / DevOps-consultancy brands that do not
# announce themselves by name. Lowercased registrable-domain stems.
KNOWN_COMPETITOR_STEMS = frozenset({
    "coderio", "toptal", "turing", "andela", "epam", "globant", "endava", "thoughtworks",
    "accenture", "deloitte", "capgemini", "infosys", "wipro", "cognizant", "tcs", "hcl",
    "caylent", "missioncloud", "rackspace", "2ndwatch", "cloudreach", "slalom", "capco",
    "securityriskadvisors", "staffordgray", "roberthalf", "randstad", "kforce",
    "insightglobal", "teksystems", "apexsystems", "cybercoders", "motionrecruitment",
    "jobot", "harnham", "hays", "adecco", "manpower", "experis", "modis", "akkodis",
    "nisum", "bairesdev", "softserve", "luxoft", "n-ix", "itransition", "scnsoft",
    "sparksoftcorp", "sparksoftcorporation",
    # AWS/cloud consultancies seen in live runs (2026-11-08).
    "rearc", "onica", "trek10", "clearscale", "nclouds", "logicworks", "cloudticity",
})


def registrable_domain(value: Optional[str]) -> Optional[str]:
    """'https://jobs.lever.co/acme' -> 'lever.co'; 'mail.acme.co.uk' -> 'acme.co.uk'."""
    if not value:
        return None
    v = value.strip().lower()
    if "@" in v and "/" not in v:
        v = v.split("@", 1)[1]
    if "//" not in v:
        v = "//" + v
    host = (urlparse(v).hostname or "").strip(".")
    if not host:
        return None
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def is_aggregator_domain(domain: Optional[str]) -> bool:
    """True for job boards, ATS hosts, link-in-bio and doc hosts."""
    if not domain:
        return False
    d = domain.strip().lower().lstrip(".")
    if d.startswith("www."):
        d = d[4:]
    reg = registrable_domain(d) or d
    return d in AGGREGATOR_DOMAINS or reg in AGGREGATOR_DOMAINS or reg.startswith("builtin")


def company_block_reason(company: Optional[str], domain: Optional[str]) -> Optional[str]:
    """Why this company may never be a lead, or None. Name + domain only."""
    reg = registrable_domain(domain)
    if not reg:
        return "no resolved company domain"
    if is_aggregator_domain(domain):
        return f"aggregator_or_ats_domain:{reg}"
    stem = reg.split(".", 1)[0].replace("-", "")
    name = (company or "").strip().lower()
    name_compact = re.sub(r"[^a-z0-9]", "", name)
    for employer in EXCLUDED_EMPLOYERS:
        if employer in stem or employer in name_compact:
            return f"excluded_employer:{employer}"
    if stem in KNOWN_COMPETITOR_STEMS or name_compact in KNOWN_COMPETITOR_STEMS:
        return f"competitor:{stem}"
    m = _COMPETITOR_NAME_RE.search(company or "")
    if m:
        return f"competitor_name:{m.group(0).strip().lower()}"
    if is_aggregator_domain(name.replace(" ", "")):
        return f"aggregator_name:{name}"
    return None


def company_size_block_reason(
    *, headcount_band: Optional[str], open_roles: Optional[int],
    min_size: Optional[int], max_size: Optional[int],
) -> Optional[str]:
    """Rejects only on EVIDENCE of being out of band. Unknown size passes the
    company gate (it is the norm: ATS data almost never states headcount) and
    is recorded on the lead instead."""
    if headcount_band:
        m = re.match(r"\s*(\d+)\s*[-+]\s*(\d+)?", headcount_band)
        if m:
            low = int(m.group(1))
            high = int(m.group(2)) if m.group(2) else None
            if max_size and low > max_size:
                return f"headcount {headcount_band} > max {max_size}"
            if min_size and high is not None and high < min_size:
                return f"headcount {headcount_band} < min {min_size}"
    if max_size and open_roles is not None:
        # >=1 open engineering role per ~8 employees is already a hiring
        # spree; more open roles than max_size/8 means the company is
        # almost certainly above the band.
        if open_roles > max(25, max_size // 8):
            return f"{open_roles} open roles implies > {max_size} employees"
    return None


# Company size bands per product (Kelvin, 2026-11-07). Mirrored into
# mse_icp_configs.min/max_company_size by migration 069.
CLOUD_DECODED_PRODUCT_ID = "777a1852-f84c-49d8-890e-cd14670b7f6f"
CONSULTING_PRODUCT_ID = "9b6c8f36-985f-4d7a-a416-c40da89e23af"
PRODUCT_SIZE_BANDS = {
    CLOUD_DECODED_PRODUCT_ID: (20, 500),
    CONSULTING_PRODUCT_ID: (20, 200),
}


def size_band_for(product_id: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    return PRODUCT_SIZE_BANDS.get(product_id or "", (None, None))


# ── 3. Email ─────────────────────────────────────────────────────────────

SENDABLE_EMAIL_GRADES = frozenset({"valid"})


def email_may_enter_send_queue(grade: Optional[str]) -> bool:
    """Only an SMTP-verified, non-catch-all address. Everything else is
    'unverified' for routing purposes and goes to Find the Buyer."""
    return grade in SENDABLE_EMAIL_GRADES


# ── Whole-lead decision ──────────────────────────────────────────────────

LANE_SEND_QUEUE = "send_queue"          # may be drafted for email and sent after HITL
LANE_FIND_THE_BUYER = "find_the_buyer"  # company is good; a human names / verifies the buyer
LANE_REJECT = "reject"                  # company is not a lead at all


@dataclass
class LeadDecision:
    lane: str
    reasons: list[str] = field(default_factory=list)
    title_verdict: Optional[TitleVerdict] = None


def evaluate_lead(
    lead: dict,
    *,
    min_size: Optional[int] = None,
    max_size: Optional[int] = None,
) -> LeadDecision:
    """The full gate for one mse_leads-shaped dict."""
    reasons: list[str] = []
    block = company_block_reason(lead.get("company"), lead.get("domain"))
    if block:
        return LeadDecision(LANE_REJECT, [block])
    size_block = company_size_block_reason(
        headcount_band=lead.get("headcount_band"),
        open_roles=lead.get("size_proxy_open_roles"),
        min_size=min_size, max_size=max_size)
    if size_block:
        return LeadDecision(LANE_REJECT, [size_block])

    tv = classify_buyer_title(lead.get("title"), open_role_count=lead.get("size_proxy_open_roles")
                              if lead.get("size_proxy_open_roles") is not None else lead.get("open_role_count"))
    reasons.append(f"title:{tv.verdict} ({tv.reason})")
    if tv.verdict != ALLOW:
        return LeadDecision(LANE_FIND_THE_BUYER, reasons, tv)

    email = lead.get("email")
    if email and registrable_domain(email) != registrable_domain(lead.get("domain")):
        reasons.append("email domain does not match company domain")
        return LeadDecision(LANE_FIND_THE_BUYER, reasons, tv)
    if not email or not email_may_enter_send_queue(lead.get("email_grade")):
        reasons.append(f"email not verified (grade={lead.get('email_grade')})")
        return LeadDecision(LANE_FIND_THE_BUYER, reasons, tv)
    reasons.append("verified email")
    return LeadDecision(LANE_SEND_QUEUE, reasons, tv)
