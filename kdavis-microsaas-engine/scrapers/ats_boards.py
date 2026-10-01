"""
scrapers/ats_boards.py

Company-first ATS sourcing (Scraper v2, 2026-10-01).

WHY THIS EXISTS -- the economics. The v1 scraper was keyword-first: every
lead cost at least one Brave query, and Brave is capped at 900/month
(scrapers/brave_search.FREE_TIER_MONTHLY_CAP). Worse, keyword-first search
returns aggregator category pages, which is exactly what produced the
2026-09-29 batch of leads addressed to ZipRecruiter.

Company-first inverts it. One Brave query surfaces ONE ATS posting URL,
whose path already names the company (boards.greenhouse.io/<token>/...).
That token is then fed to the ATS's own documented public JSON API, which
returns EVERY open role at that company for zero additional Brave spend.
A single query that found one posting yields the company's whole hiring
picture -- which is also what `open_role_count` needs for intent scoring.

Tokens are cached in mse_ats_board_tokens so a company discovered this
week costs nothing to re-poll next week.

Endpoints used (all official, public, documented, no auth, no scraping):
  Greenhouse  GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
  Lever       GET api.lever.co/v0/postings/{token}?mode=json
  Ashby       GET api.ashbyhq.com/posting-api/job-board/{token}
  Workable    GET apply.workable.com/api/v1/widget/accounts/{token}?details=true

These are the same JSON feeds each vendor publishes for embedding a board
on a customer's own careers page. No HTML parsing, no robots.txt question
(documented APIs, not crawled pages), and no LinkedIn -- per Kelvin's
standing rule, there is no LinkedIn scraping anywhere in this module.
"""

from __future__ import annotations

import html
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

# Politeness: these are free public endpoints and we are a guest on them.
MIN_ATS_DELAY_SECONDS = 1.0
MAX_ATS_DELAY_SECONDS = 2.5
ATS_TIMEOUT_SECONDS = 20
# A board with more postings than this is an enterprise/staffing operation,
# not a 20-300 headcount prospect -- stop reading rather than ingest 800
# roles from a company that is out of ICP anyway.
MAX_POSTINGS_PER_BOARD = 120


# ── Board token discovery ────────────────────────────────────────────────

# host -> (provider, path index of the company token)
#   boards.greenhouse.io/<token>/jobs/123
#   job-boards.greenhouse.io/<token>/jobs/123
#   jobs.lever.co/<token>/<uuid>
#   jobs.ashbyhq.com/<token>/<uuid>
#   apply.workable.com/<token>/j/<id>
ATS_HOST_MAP: dict[str, tuple[str, int]] = {
    "boards.greenhouse.io": ("greenhouse", 0),
    "job-boards.greenhouse.io": ("greenhouse", 0),
    "boards.eu.greenhouse.io": ("greenhouse", 0),
    "jobs.lever.co": ("lever", 0),
    "jobs.eu.lever.co": ("lever", 0),
    "jobs.ashbyhq.com": ("ashby", 0),
    "apply.workable.com": ("workable", 0),
}

PROVIDERS = ("greenhouse", "lever", "ashby", "workable")

# Path segments that are never a company token.
_RESERVED_TOKENS = frozenset({
    "jobs", "j", "search", "companies", "embed", "api", "board", "boards",
    "careers", "apply", "postings", "p", "o", "about", "login",
})

_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}$", re.IGNORECASE)


@dataclass(frozen=True)
class BoardRef:
    """A company's ATS board, independent of which posting revealed it."""

    provider: str
    token: str

    @property
    def cache_key(self) -> str:
        return f"{self.provider}:{self.token}"


def board_ref_from_url(url: str) -> Optional[BoardRef]:
    """The (provider, token) pair an ATS posting URL names, or None.

    Returns None -- never a guess -- for a non-ATS host, a reserved path
    segment, or a token that doesn't look like a token. A wrong token
    silently fetches another company's jobs, so this is deliberately
    strict."""
    if not url:
        return None
    parsed = urlparse(url if "//" in url else f"https://{url}")
    host = (parsed.netloc or "").lower().replace("www.", "")
    mapping = ATS_HOST_MAP.get(host)
    if not mapping:
        return None
    provider, idx = mapping
    parts = [p for p in (parsed.path or "").split("/") if p]
    if len(parts) <= idx:
        return None
    token = parts[idx].strip()
    if token.lower() in _RESERVED_TOKENS or not _TOKEN_RE.match(token):
        return None
    return BoardRef(provider=provider, token=token)


def company_name_from_token(token: str) -> str:
    """Human-cased company name from a board token ('acme-corp' ->
    'Acme Corp'). A fallback only: every provider's API returns the real
    display name, which is preferred when present."""
    cleaned = re.sub(r"[-_.]+", " ", token).strip()
    return cleaned.title() if cleaned else token


# ── Normalised posting ───────────────────────────────────────────────────

@dataclass
class AtsPosting:
    """One open role, normalised across all four providers. Every field
    is Optional and None means "the API did not say" -- no field is ever
    inferred or defaulted into a factual claim."""

    provider: str
    board_token: str
    company: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    location: Optional[str] = None
    posted_at: Optional[date] = None
    description_text: Optional[str] = None

    @property
    def age_days(self) -> Optional[int]:
        if self.posted_at is None:
            return None
        return (date.today() - self.posted_at).days


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(value: Optional[str]) -> Optional[str]:
    """Plain text out of an ATS description field.

    MUST unescape BEFORE stripping tags. Greenhouse returns `content` as
    HTML-ESCAPED HTML -- the body arrives literally as
    `&lt;div class=&quot;content-intro&quot;&gt;&lt;p&gt;Caylent is an...`
    so there is not a single `<` for a tag-strip to match. Stripping first
    (as this did until 2026-10-01) therefore removed nothing and handed
    every downstream reader a wall of escaped markup: extract_stack matched
    technologies against tag soup, headcount_band never found a headcount
    because the digits it wanted were buried in `font-size: 12pt`, and the
    exclusion classifier would have scored CSS instead of prose.

    Unescaped twice, because some providers escape an already-escaped body
    (`&amp;nbsp;` -> `&nbsp;` -> a real space). The second pass is a no-op
    on a singly-escaped body, so it is safe either way.
    """
    if not value:
        return None
    text = html.unescape(html.unescape(value))
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", text))
    # Entity-decoded non-breaking spaces survive the whitespace collapse.
    text = text.replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip() or None


def _parse_epoch_or_iso(value: Any) -> Optional[date]:
    """ATS timestamps arrive as ms-epoch (Lever, Workable) or ISO-8601
    (Greenhouse, Ashby). Returns None rather than today() when unparseable
    -- an undated posting must not read as fresh."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip().replace("Z", "+00:00")
        for parse in (datetime.fromisoformat, lambda s: datetime.strptime(s, "%Y-%m-%d")):
            try:
                return parse(raw).date()
            except (ValueError, TypeError):
                continue
    return None


def _first_str(*candidates: Any) -> Optional[str]:
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def _location_value(raw: Any) -> Optional[str]:
    """A location string out of whatever shape the provider sent.

    The four vendors disagree, and individual endpoints within one vendor
    disagree too: Greenhouse's board API nests it ({"name": "Austin, TX"})
    while some feeds return a bare string, Workable returns either a
    string or {"city", "country"}, and any of them may send null. Reading
    one shape and assuming it crashed the whole scout run on the first
    variation, so every shape is handled here instead -- a location is
    enrichment, never worth failing a run over."""
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, dict):
        direct = _first_str(raw.get("name"), raw.get("location"), raw.get("text"))
        if direct:
            return direct
        parts = [p for p in (_first_str(raw.get("city")), _first_str(raw.get("region"), raw.get("state")),
                             _first_str(raw.get("country"))) if p]
        return ", ".join(parts) or None
    if isinstance(raw, list):
        for entry in raw:
            value = _location_value(entry)
            if value:
                return value
    return None


# ── Per-provider normalisers ─────────────────────────────────────────────

def _normalise_greenhouse(payload: dict, ref: BoardRef) -> tuple[Optional[str], list[AtsPosting]]:
    company = _first_str((payload.get("meta") or {}).get("company_name")) or None
    out = []
    for job in (payload.get("jobs") or [])[:MAX_POSTINGS_PER_BOARD]:
        out.append(AtsPosting(
            provider="greenhouse", board_token=ref.token, company=company,
            title=_first_str(job.get("title")),
            url=_first_str(job.get("absolute_url")),
            location=_location_value(job.get("location")),
            posted_at=_parse_epoch_or_iso(job.get("updated_at") or job.get("first_published")),
            description_text=_strip_html(job.get("content")),
        ))
    return company, out


def _normalise_lever(payload: Any, ref: BoardRef) -> tuple[Optional[str], list[AtsPosting]]:
    jobs = payload if isinstance(payload, list) else (payload.get("data") or [])
    out = []
    for job in jobs[:MAX_POSTINGS_PER_BOARD]:
        categories = job.get("categories") or {}
        out.append(AtsPosting(
            provider="lever", board_token=ref.token, company=None,
            title=_first_str(job.get("text"), job.get("title")),
            url=_first_str(job.get("hostedUrl"), job.get("applyUrl")),
            location=_location_value(categories.get("location")),
            posted_at=_parse_epoch_or_iso(job.get("createdAt")),
            description_text=_strip_html(job.get("descriptionPlain") or job.get("description")),
        ))
    return None, out


def _normalise_ashby(payload: dict, ref: BoardRef) -> tuple[Optional[str], list[AtsPosting]]:
    out = []
    for job in (payload.get("jobs") or [])[:MAX_POSTINGS_PER_BOARD]:
        out.append(AtsPosting(
            provider="ashby", board_token=ref.token, company=None,
            title=_first_str(job.get("title")),
            url=_first_str(job.get("jobUrl"), job.get("applyUrl")),
            location=_location_value(job.get("location")),
            posted_at=_parse_epoch_or_iso(job.get("publishedAt") or job.get("updatedAt")),
            description_text=_strip_html(job.get("descriptionPlain") or job.get("descriptionHtml")),
        ))
    return None, out


def _normalise_workable(payload: dict, ref: BoardRef) -> tuple[Optional[str], list[AtsPosting]]:
    company = _first_str(payload.get("name"), (payload.get("account") or {}).get("name"))
    jobs = payload.get("jobs") or (payload.get("account") or {}).get("jobs") or []
    out = []
    for job in jobs[:MAX_POSTINGS_PER_BOARD]:
        out.append(AtsPosting(
            provider="workable", board_token=ref.token, company=company,
            title=_first_str(job.get("title")),
            url=_first_str(job.get("url"), job.get("application_url"), job.get("shortlink")),
            location=_location_value(job.get("location")),
            posted_at=_parse_epoch_or_iso(job.get("published_on") or job.get("created_at")),
            description_text=_strip_html(job.get("description")),
        ))
    return company, out


_PROVIDER_ENDPOINTS: dict[str, Callable[[str], str]] = {
    "greenhouse": lambda t: f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true",
    "lever": lambda t: f"https://api.lever.co/v0/postings/{t}?mode=json",
    "ashby": lambda t: f"https://api.ashbyhq.com/posting-api/job-board/{t}",
    "workable": lambda t: f"https://apply.workable.com/api/v1/widget/accounts/{t}?details=true",
}

_PROVIDER_NORMALISERS: dict[str, Callable[[Any, BoardRef], tuple[Optional[str], list[AtsPosting]]]] = {
    "greenhouse": _normalise_greenhouse,
    "lever": _normalise_lever,
    "ashby": _normalise_ashby,
    "workable": _normalise_workable,
}


# ── Client ───────────────────────────────────────────────────────────────

@dataclass
class AtsStats:
    """Per-run accounting. Mirrors brave_search's self.stats convention so
    both sources report the same shape into funnel_stats."""

    boards_fetched: int = 0
    boards_failed: int = 0
    boards_empty: int = 0
    postings_returned: int = 0
    http_errors: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "boards_fetched": self.boards_fetched,
            "boards_failed": self.boards_failed,
            "boards_empty": self.boards_empty,
            "postings_returned": self.postings_returned,
            "http_errors": dict(self.http_errors),
        }


class AtsBoardClient:
    """
    Fetches every open role for a company from its ATS's public JSON API.

    Costs ZERO Brave queries -- that is the entire point. `http_get` is
    injectable so tests exercise real normalisation against recorded
    payloads with no network.

    Failures are non-fatal by design: one company's board being down,
    renamed, or 404 must not abort a scout run. Every failure is counted
    in `stats` and logged, never swallowed silently.
    """

    def __init__(
        self,
        http_get: Optional[Callable[..., Any]] = None,
        *,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._http_get = http_get or httpx.get
        self._sleep = sleep if sleep is not None else time.sleep
        self.stats = AtsStats()
        # Per-board outcome, keyed by BoardRef.cache_key: "ok" | "empty" |
        # "failed". The caller needs these THREE values distinguished to
        # maintain the token cache correctly -- a 404 board should stop
        # being polled, while a company with no openings this week is a
        # legitimate result that must keep its place.
        self.board_status: dict[str, str] = {}

    def _polite_pause(self) -> None:
        self._sleep(random.uniform(MIN_ATS_DELAY_SECONDS, MAX_ATS_DELAY_SECONDS))

    def fetch_board(self, ref: BoardRef) -> list[AtsPosting]:
        """Every open posting on one board. Empty list on any failure."""
        endpoint_builder = _PROVIDER_ENDPOINTS.get(ref.provider)
        if not endpoint_builder:
            log.warning("ATS: unknown provider %r -- skipping", ref.provider)
            self.stats.boards_failed += 1
            self.board_status[ref.cache_key] = "failed"
            return []

        url = endpoint_builder(ref.token)
        try:
            response = self._http_get(
                url,
                timeout=ATS_TIMEOUT_SECONDS,
                headers={"Accept": "application/json", "User-Agent": "THD-Agentic-Systems/1.0 (+https://thdstack.com)"},
            )
        except Exception as exc:  # network/timeout -- one board, not the run
            log.warning("ATS: %s board %r request failed: %s", ref.provider, ref.token, exc)
            self.stats.boards_failed += 1
            self.stats.http_errors["request_error"] = self.stats.http_errors.get("request_error", 0) + 1
            self.board_status[ref.cache_key] = "failed"
            return []

        status = getattr(response, "status_code", 0)
        if status != 200:
            log.info("ATS: %s board %r returned HTTP %s", ref.provider, ref.token, status)
            self.stats.boards_failed += 1
            key = f"http_{status}"
            self.stats.http_errors[key] = self.stats.http_errors.get(key, 0) + 1
            self.board_status[ref.cache_key] = "failed"
            return []

        try:
            payload = response.json()
        except Exception as exc:
            log.warning("ATS: %s board %r returned non-JSON: %s", ref.provider, ref.token, exc)
            self.stats.boards_failed += 1
            self.stats.http_errors["bad_json"] = self.stats.http_errors.get("bad_json", 0) + 1
            self.board_status[ref.cache_key] = "failed"
            return []

        company, postings = _PROVIDER_NORMALISERS[ref.provider](payload, ref)
        fallback_company = company or company_name_from_token(ref.token)
        for p in postings:
            if not p.company:
                p.company = fallback_company

        self.stats.boards_fetched += 1
        self.stats.postings_returned += len(postings)
        if not postings:
            self.stats.boards_empty += 1
        self.board_status[ref.cache_key] = "ok" if postings else "empty"
        return postings

    def fetch_boards(self, refs: Iterable[BoardRef]) -> dict[str, list[AtsPosting]]:
        """Several boards, deduped by cache_key, with a polite pause
        between distinct boards. Returns {cache_key: postings}."""
        seen: dict[str, list[AtsPosting]] = {}
        for i, ref in enumerate(refs):
            if ref.cache_key in seen:
                continue
            if i:
                self._polite_pause()
            seen[ref.cache_key] = self.fetch_board(ref)
        return seen


# ── Relevance filtering ──────────────────────────────────────────────────

def matching_postings(
    postings: Iterable[AtsPosting],
    keywords: Iterable[str],
    *,
    max_age_days: Optional[int] = None,
) -> list[AtsPosting]:
    """
    Postings whose TITLE matches one of `keywords`.

    Title field only -- matching against the description instead is how
    "we use Terraform to manage our sales CRM" turns a sales role into a
    platform-engineering signal. Word-boundary anchored for the same
    reason classify_seniority is (see lead_qualification's docstring).

    `max_age_days=None` keeps undated postings; a numeric value drops
    them, because an unknown date cannot honestly satisfy a freshness
    filter.
    """
    patterns = [re.compile(rf"\b{re.escape(k.strip())}\b", re.IGNORECASE) for k in keywords if k and k.strip()]
    if not patterns:
        return []
    out = []
    for p in postings:
        if not p.title or not any(pat.search(p.title) for pat in patterns):
            continue
        if max_age_days is not None:
            age = p.age_days
            if age is None or age > max_age_days:
                continue
        out.append(p)
    return out


def company_domain_from_postings(postings: Iterable[AtsPosting]) -> Optional[str]:
    """A company's own domain, if any posting links off the ATS host to
    it. ATS feeds often carry only the ATS-hosted URL, in which case this
    returns None and the lead routes to the manual LinkedIn track."""
    for p in postings:
        if not p.url:
            continue
        host = (urlparse(p.url).netloc or "").lower().replace("www.", "")
        if host and host not in ATS_HOST_MAP and not host.endswith(("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com")):
            return host
    return None
