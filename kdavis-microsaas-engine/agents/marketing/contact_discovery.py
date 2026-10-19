"""
agents/marketing/contact_discovery.py

Free-first contact discovery (Kelvin's decision 5a, 2026-10-01).

WHY. The 2026-10-01 re-runs found ZERO contacts across both products while
spending Brave queries trying: domains now resolve 100% of the time, so
contacts are the remaining bottleneck. Before spending metered quota on a
`site:linkedin.com/in` search, try the sources that cost nothing but one
HTTP GET against a domain we have already verified:

  1. A hiring manager named in the JD text itself ("report to Jane Doe,
     VP Engineering") -- free, no request at all, and the most reliable
     signal there is because the company wrote it.
  2. The company's own pages: /about, /team, /leadership, /company, and
     the handful of variants below.

Only if both come back empty does the caller fall through to Brave.

WHAT THIS DELIBERATELY DOES NOT DO
  - No LinkedIn fetching. Standing rule. These are the company's own pages.
  - robots.txt is honoured before any fetch (see _allowed), because unlike
    the ATS JSON APIs these ARE crawled pages.
  - No guessing. A name without a title, or a title without a name, is not
    a contact -- returning half a contact invites MKT-O2 to assert a role
    the page never stated.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

from agents.marketing.lead_qualification import (
    _COMPANY_SUFFIX_RE,
    classify_seniority,
    is_negative_title,
)

log = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 12
USER_AGENT = "THD-Agentic-Systems/1.0 (+https://thdstack.com)"

# Checked in this order; the first page yielding a usable contact wins.
# Ordered by how likely a small company is to have it AND to put leadership
# on it, not alphabetically.
TEAM_PAGE_PATHS = (
    "/team", "/about", "/about-us", "/leadership", "/company",
    "/our-team", "/people", "/who-we-are",
)

# Titles worth reaching out to, most senior first. Mirrors
# company_first_sourcing.DECISION_MAKER_TITLES but as patterns, because here
# we are reading prose rather than querying a search index.
_BUYER_TITLE_RE = re.compile(
    r"\b(?:Chief\s+Technology\s+Officer|Chief\s+Information\s+Officer|CTO|CIO|CISO"
    r"|VP\s+(?:of\s+)?(?:Engineering|Infrastructure|Platform|Technology)"
    r"|Vice\s+President\s+(?:of\s+)?(?:Engineering|Infrastructure|Platform|Technology)"
    r"|Head\s+of\s+(?:Engineering|Infrastructure|Platform|DevOps|Technology)"
    r"|Director\s+of\s+(?:Engineering|Infrastructure|Platform|Technology)"
    r"|(?:Co-?)?Founder|CEO)\b",
    re.IGNORECASE,
)

# A person's name: exactly First + Last, with an optional lowercase particle
# between ("Maria del Carmen"). Deliberately NOT "2-3 capitalised words":
# that allowed a preceding heading to be absorbed into the name, and a
# stripped team page reads as one run of text -- "Leadership Jane Doe - CTO"
# parsed as name="Leadership Jane Doe". Capping the capitalised words at two
# makes the regex engine reject "Leadership Jane" (no separator follows) and
# backtrack to "Jane Doe", which is the actual person.
_NAME_RE = r"[A-Z][a-z]+(?:\s+(?:van|von|de|der|del|da|di|la))?\s+[A-Z][a-z'\u2019\-]+"

# CRITICAL: the NAME portion must stay CASE-SENSITIVE while the title stays
# case-insensitive, so the title group is wrapped in a scoped inline flag
# `(?i:...)` instead of compiling the whole pattern with re.IGNORECASE.
#
# The first version used a global re.IGNORECASE, which made `[A-Z][a-z]+`
# match any case and turned prose into contact names. The 2026-10-01
# consulting run duly stored contacts called "you will", "and leadership",
# "or senior", "of MeatEater" and "with co" -- fragments that would have gone
# into outbound copy as the recipient's name.
_TITLE_I = f"(?i:{_BUYER_TITLE_RE.pattern})"

# "Jane Doe, VP of Engineering" / "Jane Doe - CTO" / "Jane Doe – Head of Platform"
_NAME_THEN_TITLE_RE = re.compile(rf"({_NAME_RE})\s*(?:,|-|–|—|\||·)\s*({_TITLE_I})")
# "CTO Jane Doe" / "VP of Engineering, Jane Doe"
_TITLE_THEN_NAME_RE = re.compile(rf"({_TITLE_I})\s*(?:,|-|–|—|:)?\s+({_NAME_RE})")

# JD phrasing that names a hiring manager. The surrounding prose is matched
# case-insensitively; the captured NAME is not.
_JD_MANAGER_RES = (
    re.compile(rf"(?i:report(?:s|ing)?\s+(?:directly\s+)?to\s+(?:our\s+)?)({_NAME_RE})\s*,?\s*(?i:(?:our\s+)?)({_TITLE_I})"),
    re.compile(rf"(?i:\byou(?:'ll| will)\s+report\s+to\s+)({_NAME_RE})\s*,?\s*({_TITLE_I})"),
    re.compile(rf"(?i:\b(?:hiring\s+manager|this\s+role\s+reports\s+to)\s*:?\s*)({_NAME_RE})\s*,?\s*({_TITLE_I})?"),
)

# Prose words that can begin a capitalised two-word run at a sentence start
# ("Our Team", "The Platform") but never begin a person's name.
_NOT_NAME_FIRST_WORD = frozenset({
    "the", "our", "your", "their", "this", "that", "these", "those", "and",
    "or", "with", "of", "for", "from", "you", "we", "they", "it", "as", "at",
    "in", "on", "by", "to", "all", "any", "each", "both", "join", "meet",
    "about", "contact", "apply", "report", "reporting", "lead", "leads",
    "tech", "senior", "staff", "principal", "head", "chief", "vice",
    "more", "less", "view", "see", "read",
})

_TAG_RE = re.compile(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", re.I | re.S)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Page furniture that matches the name shape but is not a person.
# Words that make a capitalised pair page furniture rather than a person.
# Found live 2026-10-18: "Case Studies" was stored as a partner's founder.
_FURNITURE_WORDS = frozenset({
    # Deliberately no word that is also a common surname (Case, Read, Book,
    # Call, Free): this check is shared with prospect contact discovery.
    "studies", "study", "services", "solutions", "products", "pricing", "blog",
    "news", "careers", "resources", "insights", "contact", "privacy", "terms",
    "portfolio", "testimonials", "reviews", "industries", "partners", "company", "menu",
    "demo", "started", "featured", "clients", "stories", "veteran",
    "certified", "awards", "members", "leadership",
    "practice", "department", "division", "technology", "compliance", "advisory",
})

_NOT_A_NAME = frozenset({
    "our team", "the team", "about us", "contact us", "who we are",
    "leadership team", "meet the", "join us", "our people", "our story",
    "privacy policy", "terms of", "all rights", "cookie policy",
})


@dataclass
class DiscoveredContact:
    name: Optional[str] = None
    title: Optional[str] = None
    source: Optional[str] = None
    evidence: Optional[str] = None

    @property
    def is_usable(self) -> bool:
        """Both halves required. A name with no title would let MKT-O2
        assert a role the page never stated; a title with no name is not a
        person to address."""
        return bool(self.name and self.title)


@dataclass
class ContactDiscoveryStats:
    jd_hits: int = 0
    pages_fetched: int = 0
    pages_blocked_by_robots: int = 0
    page_hits: int = 0
    failures: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "jd_hits": self.jd_hits,
            "pages_fetched": self.pages_fetched,
            "pages_blocked_by_robots": self.pages_blocked_by_robots,
            "page_hits": self.page_hits,
            "failures": dict(self.failures),
        }


def _clean_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html or "")
    text = _ANY_TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _plausible_name(value: str) -> bool:
    v = (value or "").strip()
    if not v or len(v) > 60:
        return False
    if v.lower() in _NOT_A_NAME or any(v.lower().startswith(p) for p in _NOT_A_NAME):
        return False
    words = v.split()
    if len(words) < 2:
        return False
    if any(w.lower().strip(".,") in _FURNITURE_WORDS for w in words):
        return False
    # Belt and braces alongside the case-sensitive regex: a prose word can
    # legitimately be capitalised at a sentence start, so "Tech Leads" and
    # "Senior Engineers" must still be refused even though both words are
    # capitalised.
    if words[0].lower() in _NOT_NAME_FIRST_WORD or words[-1].lower() in _NOT_NAME_FIRST_WORD:
        return False
    # Every word must START uppercase, checked case-SENSITIVELY.
    if not all(w[:1].isupper() for w in words if w and w.lower() not in
               {"van", "von", "de", "der", "del", "da", "di", "la"}):
        return False
    # A company name is not a person. Reuses lead_qualification's existing
    # suffix detector rather than growing a second list that could disagree
    # with it -- "Strutt Co" came through as a contact on the 2026-10-01 run.
    if _COMPANY_SUFFIX_RE.search(v):
        return False
    # A role word inside the "name" means the split went wrong.
    return classify_seniority(v) == "unknown"


def _patterns_for(title_re: re.Pattern) -> tuple[re.Pattern, re.Pattern]:
    """The name/title pair patterns for a given title vocabulary. The buyer
    vocabulary is the default; the partner lane (2026-10-18) passes its own
    -- founders and managing partners of service firms -- without a second
    copy of the name logic."""
    if title_re is _BUYER_TITLE_RE:
        return _NAME_THEN_TITLE_RE, _TITLE_THEN_NAME_RE
    title_i = f"(?i:{title_re.pattern})"
    # Title-then-name WITHOUT a comma: "CEO, Shadow Breeze" is how a client
    # testimonial is attributed (title, company), and on 2026-10-18 it put a
    # client's company name in as an agency's CEO.
    return (re.compile(rf"({_NAME_RE})\s*(?:,|-|–|—|\||·)\s*({title_i})"),
            re.compile(rf"({title_i})\s*(?:-|–|—|:)?\s+({_NAME_RE})"))


def _contact_from_text(text: str, source: str,
                       title_re: re.Pattern = _BUYER_TITLE_RE) -> Optional[DiscoveredContact]:
    """First (name, title) pair in `text`, or None."""
    name_then_title, title_then_name = _patterns_for(title_re)
    for pattern, name_first in ((name_then_title, True), (title_then_name, False)):
        for match in pattern.finditer(text):
            name = match.group(1) if name_first else match.group(match.lastindex)
            title_raw = match.group(match.lastindex) if name_first else match.group(1)
            # The title group can be the whole alternation; normalise it.
            title_match = title_re.search(title_raw or "")
            title = title_match.group(0).strip() if title_match else None
            if not (name and title) or not _plausible_name(name):
                continue
            rejected, _reason = is_negative_title(title)
            if rejected:
                continue
            return DiscoveredContact(
                name=name.strip(), title=title, source=source,
                evidence=match.group(0).strip()[:120],
            )
    return None


# ── Adjacent-element pairs (2026-10-18) ──────────────────────────────────
#
# Team pages usually put the name and the title in SEPARATE elements
# ("<h3>Dana Whitfield</h3><p>Managing Partner</p>"). Flattened to text, that
# is "Dana Whitfield Managing Partner" with no separator, which the
# name/title patterns above correctly refuse to split. Reading the page as a
# list of element texts keeps the boundary the page itself drew.

_BLOCK_RE = re.compile(r"<(?:script|style|noscript)[^>]*>.*?</(?:script|style|noscript)>", re.I | re.S)


_HEADING_LEAD = re.compile(r"^\W*(?:from|by|about|meet|message|letter|note|our|the|a|an|ask|with)\b", re.I)


def page_segments(page_html: str, max_len: int = 160) -> list[str]:
    """Visible text, one entry per tag-delimited run, in page order."""
    import html as _html
    body = _BLOCK_RE.sub(" ", page_html or "")
    out = []
    for part in re.split(r"<[^>]+>", body):
        t = " ".join(_html.unescape(part).split())
        if t and len(t) <= max_len:
            out.append(t)
    return out


def adjacent_name_title_pairs(segments: list[str], title_re: re.Pattern,
                              *, max_title_len: int = 80) -> list[tuple[str, str]]:
    """(name, title) where a title element directly follows (within two
    elements) a plausible person name. A title with no name beside it, or a
    sentence that merely contains a title ("X is a partner and Head of
    Talent"), yields nothing -- never paired with a distant name."""
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for i, seg in enumerate(segments):
        if len(seg) > max_title_len or " is " in seg or not title_re.search(seg):
            continue
        # A title element reads as a title: "From the President" or "Meet
        # our CEO" is a heading about one, not the person's own title.
        if _HEADING_LEAD.match(seg):
            continue
        for back in (1, 2):
            if i - back < 0:
                break
            cand = segments[i - back]
            if _plausible_name(cand) and not title_re.search(cand):
                if cand not in seen:
                    seen.add(cand)
                    pairs.append((cand, seg))
                break
    return pairs


# ── 1. From the JD text (free, no request) ───────────────────────────────

def from_jd_text(jd_text: Optional[str]) -> Optional[DiscoveredContact]:
    """A hiring manager the company named in its own job description.

    The most reliable source available: the company wrote it, so there is
    no inference involved.
    """
    if not jd_text:
        return None
    for pattern in _JD_MANAGER_RES:
        for match in pattern.finditer(jd_text):
            name = match.group(1)
            title_raw = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            if not _plausible_name(name or ""):
                continue
            title_match = _BUYER_TITLE_RE.search(title_raw or "")
            if not title_match:
                # A name with no stated title is not a usable contact.
                continue
            rejected, _ = is_negative_title(title_match.group(0))
            if rejected:
                continue
            return DiscoveredContact(
                name=name.strip(), title=title_match.group(0).strip(),
                source="jd_hiring_manager", evidence=match.group(0).strip()[:120],
            )
    # Fall back to the generic name+title shapes anywhere in the JD.
    return _contact_from_text(jd_text, "jd_text")


# ── 2. From the company's own pages (free, one GET each) ─────────────────

def _allowed(domain: str, path: str, http_get: Callable[..., Any]) -> bool:
    """robots.txt check. These are crawled pages, not documented APIs, so
    unlike scrapers/ats_boards.py this must ask permission. A robots.txt we
    cannot read is treated as permissive (the standard reading), but a
    robots.txt that disallows is honoured."""
    try:
        response = http_get(f"https://{domain}/robots.txt",
                           timeout=HTTP_TIMEOUT_SECONDS,
                           headers={"User-Agent": USER_AGENT})
        if getattr(response, "status_code", 0) != 200:
            return True
        parser = RobotFileParser()
        parser.parse((getattr(response, "text", "") or "").splitlines())
        return parser.can_fetch(USER_AGENT, f"https://{domain}{path}")
    except Exception:
        return True


def _contact_from_segments(page_html: str, source: str, title_re: re.Pattern) -> Optional[DiscoveredContact]:
    for name, title_text in adjacent_name_title_pairs(page_segments(page_html), title_re):
        m = title_re.search(title_text)
        title = title_text.strip() if len(title_text) <= 60 else (m.group(0).strip() if m else None)
        if not title:
            continue
        rejected, _ = is_negative_title(title)
        if rejected:
            continue
        return DiscoveredContact(name=name, title=title, source=source, evidence=f"{name} | {title_text}"[:120])
    return None


def from_company_pages(
    domain: Optional[str],
    *,
    http_get: Optional[Callable[..., Any]] = None,
    paths: Iterable[str] = TEAM_PAGE_PATHS,
    stats: Optional[ContactDiscoveryStats] = None,
    max_pages: int = 4,
    title_re: Optional[re.Pattern] = None,
) -> Optional[DiscoveredContact]:
    """Walk a few well-known team/about paths on a domain we have already
    verified, stopping at the first usable contact.

    `max_pages` bounds it: a company with none of these pages must not cost
    eight requests per run.
    """
    if not domain:
        return None
    http_get = http_get or httpx.get
    stats = stats if stats is not None else ContactDiscoveryStats()

    checked = 0
    for path in paths:
        if checked >= max_pages:
            break
        if not _allowed(domain, path, http_get):
            stats.pages_blocked_by_robots += 1
            log.info("[ContactDiscovery] robots.txt disallows %s%s -- skipping", domain, path)
            continue
        url = urljoin(f"https://{domain}", path)
        try:
            response = http_get(url, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True,
                               headers={"User-Agent": USER_AGENT})
        except Exception as exc:
            stats.failures["request_error"] = stats.failures.get("request_error", 0) + 1
            log.debug("[ContactDiscovery] %s failed: %s", url, exc)
            continue
        checked += 1
        status = getattr(response, "status_code", 0)
        if status != 200:
            stats.failures[f"http_{status}"] = stats.failures.get(f"http_{status}", 0) + 1
            continue
        stats.pages_fetched += 1
        page = getattr(response, "text", "") or ""
        contact = _contact_from_text(_clean_text(page), f"company_page:{path}", title_re or _BUYER_TITLE_RE)
        if not (contact and contact.is_usable):
            contact = _contact_from_segments(page, f"company_page:{path}", title_re or _BUYER_TITLE_RE)
        if contact and contact.is_usable:
            stats.page_hits += 1
            return contact
    return None


# ── Orchestrator ─────────────────────────────────────────────────────────

def discover_contact_free(
    *,
    domain: Optional[str],
    jd_text: Optional[str] = None,
    http_get: Optional[Callable[..., Any]] = None,
    stats: Optional[ContactDiscoveryStats] = None,
) -> Optional[DiscoveredContact]:
    """
    Try every FREE source, cheapest first. Returns None when nothing usable
    is found, and the caller then decides whether to spend a Brave query.

    Order matters: the JD costs nothing at all (the text is already in
    memory), so it is always tried before any HTTP request.
    """
    stats = stats if stats is not None else ContactDiscoveryStats()

    contact = from_jd_text(jd_text)
    if contact and contact.is_usable:
        stats.jd_hits += 1
        return contact

    return from_company_pages(domain, http_get=http_get, stats=stats)
