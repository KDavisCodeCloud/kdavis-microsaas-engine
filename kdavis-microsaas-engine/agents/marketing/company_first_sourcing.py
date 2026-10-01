"""
agents/marketing/company_first_sourcing.py

Scraper v2 primary sourcing path (2026-10-01, Kelvin's spec).

THE INVERSION. v1 was keyword-first: every lead cost at least one Brave
query (capped at 900/month), and keyword search surfaces aggregator
category pages -- which is literally what produced the 2026-09-29 batch
of HITL drafts addressed to ZipRecruiter.

v2 is company-first:

  1. DISCOVER (costs Brave quota) -- ATS-scoped queries surface ATS
     posting URLs. Each URL's path already names the company, so a
     board token is extracted with no page fetch and no guessing.
     Cached tokens from mse_ats_board_tokens are loaded FIRST and cost
     nothing at all.

  2. EXPAND (free) -- each board token is handed to that ATS's own
     public JSON API, which returns the company's ENTIRE open-role
     list. One Brave query that found one posting yields the whole
     hiring picture, which is also exactly what intent scoring's
     open_role_count needs.

  3. QUALIFY (free) -- agents.marketing.lead_qualification scores fit
     and intent, grades email confidence, and routes.

  4. DECISION-MAKER (costs Brave quota, budgeted last) -- and ONLY
     here does `site:linkedin.com/in` run, one query per already-
     qualified company, with the contact title parsed from the SNIPPET.
     Kelvin, 2026-10-01: "site:linkedin.com/in is NOT a top-level
     source. It runs only in the per-company decision-maker step."

Standing rules honoured here:
  - No LinkedIn SCRAPING. Step 4 reads Brave's index of public profile
    pages through the paid Search API; it never fetches linkedin.com.
  - Domain-less leads route to the manual LinkedIn track only -- never
    dropped, never emailed.
  - A hard max_queries cap, enforced across steps 1 and 4 together, so a
    test run can be held to ≤40 queries as instructed.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Optional

from agents.marketing.lead_qualification import (
    ROUTE_MANUAL_LINKEDIN,
    ROUTE_OUTBOUND_EMAIL,
    ROUTE_REJECT,
    ScoringConfig,
    classify_seniority,
    extract_stack,
    funding_recency_months,
    grade_email,
    headcount_band,
    intent_score,
    is_negative_title,
    is_role_address,
    fit_score,
    parse_location,
    route_lead,
)
from scrapers.ats_boards import (
    AtsBoardClient,
    AtsPosting,
    BoardRef,
    board_ref_from_url,
    company_domain_from_postings,
    company_name_from_token,
    matching_postings,
)

log = logging.getLogger(__name__)

BOARD_TOKEN_TABLE = "mse_ats_board_tokens"

# Discovery queries. One per (ATS host, keyword). These are the ONLY
# top-level search templates in v2 -- deliberately no open-web
# `"{title}" hiring` template, which is what returned category pages.
ATS_DISCOVERY_TEMPLATES = [
    'site:boards.greenhouse.io "{kw}"',
    'site:job-boards.greenhouse.io "{kw}"',
    'site:jobs.lever.co "{kw}"',
    'site:jobs.ashbyhq.com "{kw}"',
    'site:apply.workable.com "{kw}"',
]

# Buyer titles for the per-company decision-maker lookup (step 4).
DECISION_MAKER_TITLES = ("CTO", "VP Engineering", "Head of Platform", "Director of Engineering", "VP Infrastructure")

# A board that has failed this many times in a row stops being polled.
MAX_CONSECUTIVE_BOARD_FAILURES = 3

# Boards polled per run. ATS expansion costs no Brave quota, but it is not
# free in WALL CLOCK: each board is one HTTP request (20s timeout) plus a
# 1-2.5s politeness pause, so an uncapped run that discovered 200 boards
# could sit there for over an hour and get killed mid-flight -- leaving
# the mse_lead_finder_runs row stuck at "running" with no funnel_stats,
# which is exactly the un-attributable failure this build removes.
# Candidates are prioritised before the cap bites (see
# find_company_first_signals), so the cap drops the least promising.
MAX_BOARDS_PER_RUN = 60

# Brave request pacing. BraveSearchScraper.scrape() paces itself, but
# _search() -- which this module calls directly, because v2 does not use
# scrape()'s RawLead pipeline -- does not. Without this, a 40-query run
# fires 40 requests back-to-back and earns 429s on a metered plan. Same
# constants as scrapers/brave_search.py so both paths behave alike.
MIN_QUERY_DELAY_SECONDS = 1.2
MAX_QUERY_DELAY_SECONDS = 2.5


def _pace(sleep: Optional[Callable[[float], None]]) -> None:
    (sleep if sleep is not None else time.sleep)(
        random.uniform(MIN_QUERY_DELAY_SECONDS, MAX_QUERY_DELAY_SECONDS)
    )

# Share of the query budget reserved for step 4. Discovery is useless
# without contacts, and contacts are useless without companies, so
# neither step may consume the whole budget.
DECISION_MAKER_BUDGET_SHARE = 0.35


@dataclass
class FunnelStats:
    """Extended funnel accounting (item 3.10). Every stage records a
    count, and every drop is attributable to a named reason -- a lead
    that vanishes with no stage to explain it is the bug this whole
    build exists to prevent."""

    queries_discovery: int = 0
    queries_decision_maker: int = 0
    ats_urls_seen: int = 0
    board_tokens_discovered: int = 0
    board_tokens_from_cache: int = 0
    boards_polled: int = 0
    boards_skipped_over_cap: int = 0
    postings_returned: int = 0
    postings_title_matched: int = 0
    companies_considered: int = 0
    dropped_no_matching_role: int = 0
    dropped_negative_title: dict[str, int] = field(default_factory=dict)
    dropped_low_fit: int = 0
    dropped_low_intent: int = 0
    dropped_duplicate_company: int = 0
    dropped_duplicate_contact: int = 0
    routed_outbound_email: int = 0
    routed_manual_linkedin: int = 0
    decision_makers_found: int = 0
    ats_client_stats: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["leads_qualified"] = self.routed_outbound_email + self.routed_manual_linkedin
        d["dropped_negative_title_total"] = sum(self.dropped_negative_title.values())
        return d


# ── Step 1: board token discovery ────────────────────────────────────────

def load_cached_board_refs(
    db,
    product_id: Optional[str] = None,
    limit: int = 200,
    prior_failures: Optional[dict[str, int]] = None,
) -> list[BoardRef]:
    """Board tokens already known for this product. Costs ZERO Brave
    quota -- this is the whole point of the cache table. Boards that have
    failed MAX_CONSECUTIVE_BOARD_FAILURES times in a row are excluded.

    `prior_failures`, if given, is filled with {cache_key: count} for
    every cached row -- upsert_board_tokens needs those counts to
    INCREMENT on a repeat failure rather than reset to 1."""
    if prior_failures is None:
        prior_failures = {}
    try:
        query = db.table(BOARD_TOKEN_TABLE).select("provider,board_token,consecutive_failures")
        if product_id:
            query = query.eq("product_id", product_id)
        rows = query.limit(limit).execute().data or []
    except Exception as exc:
        # The cache is an optimisation. A missing table or a transient
        # read error must degrade to "discover from scratch", never abort
        # the run -- but it is logged, not swallowed.
        log.warning("[CompanyFirst] board-token cache unavailable (%s) -- discovering from scratch", exc)
        return []
    out = []
    for r in rows:
        ref = BoardRef(provider=r.get("provider") or "", token=r.get("board_token") or "")
        if not (ref.provider and ref.token):
            continue
        failures = r.get("consecutive_failures") or 0
        # Remember the count even for a board we are about to skip, so a
        # re-discovery of that same dead board this run keeps accumulating
        # rather than resetting to 1.
        prior_failures[ref.cache_key] = failures
        if failures >= MAX_CONSECUTIVE_BOARD_FAILURES:
            continue
        out.append(ref)
    return out


def discover_board_refs(
    scraper,
    keywords: Iterable[str],
    *,
    max_queries: int,
    stats: FunnelStats,
    sleep: Optional[Callable[[float], None]] = None,
) -> list[BoardRef]:
    """ATS-scoped Brave queries -> unique BoardRefs. Stops at
    max_queries. A result that is not an ATS URL is simply not a board
    token -- there is no fallback that could invent one."""
    queries = [t.format(kw=kw) for kw in keywords for t in ATS_DISCOVERY_TEMPLATES]
    found: dict[str, BoardRef] = {}
    for query in queries:
        if stats.queries_discovery >= max_queries:
            log.info("[CompanyFirst] discovery query cap %d reached", max_queries)
            break
        if stats.queries_discovery:
            _pace(sleep)
        items = scraper._search(query)
        stats.queries_discovery += 1
        for item in items:
            link = item.get("link") or ""
            ref = board_ref_from_url(link)
            if ref is None:
                continue
            stats.ats_urls_seen += 1
            found.setdefault(ref.cache_key, ref)
    stats.board_tokens_discovered = len(found)
    return list(found.values())


def upsert_board_tokens(
    db,
    refs_with_results: dict[str, tuple[BoardRef, list[AtsPosting], Optional[str]]],
    product_id: Optional[str] = None,
    *,
    board_status: Optional[dict[str, str]] = None,
    prior_failures: Optional[dict[str, int]] = None,
) -> int:
    """Write/refresh the cache. One row per board, so next week's run
    skips discovery for every company found this week.

    Records all THREE outcomes distinctly, which the first live run proved
    necessary. Of the boards discovered on 2026-10-01, several (angi,
    dbtlabsinc, o1labs, worldlabs, skylotechnologies, zerofox,
    talentwerx.io) returned 404 -- Brave's index still carries ATS URLs for
    companies that have since moved or closed their boards. Verified
    independently that the endpoints are right: stripe and discord return
    200 on the same Greenhouse API.

    Two bugs this function had until that run made them visible:
      - a 404 and a company with no current openings both recorded as
        'empty', which are completely different facts; and
      - consecutive_failures was SET to 1 rather than incremented, so the
        `>= MAX_CONSECUTIVE_BOARD_FAILURES` eviction in
        load_cached_board_refs could never fire and a dead board would be
        re-polled (one HTTP request + a politeness pause) every run,
        forever.
    """
    # A real ISO timestamp, NOT the string "now()". PostgREST sends values
    # as JSON, so "now()" arrives as the literal 6-character string and
    # Postgres rejects it ("invalid input syntax for type timestamp with
    # time zone"). The except below would have swallowed that into a
    # warning and the cache would have silently never populated --
    # defeating the entire reason this table exists.
    now_iso = datetime.now(timezone.utc).isoformat()
    board_status = board_status or {}
    prior_failures = prior_failures or {}
    written = 0
    for key, (ref, postings, domain) in refs_with_results.items():
        company = next((p.company for p in postings if p.company), None) or company_name_from_token(ref.token)
        # Fall back to inferring from postings only when the client gave no
        # explicit status, so a caller that does pass board_status can
        # distinguish 'failed' from 'empty'.
        status = board_status.get(key) or ("ok" if postings else "empty")
        if status == "failed":
            failures = prior_failures.get(key, 0) + 1
        else:
            failures = 0
        row = {
            "provider": ref.provider,
            "board_token": ref.token,
            "company": company,
            "company_domain": domain,
            "last_fetched_at": now_iso,
            "last_status": status,
            # A failed fetch learned nothing about the role count, so it
            # must not overwrite a previously-known number with 0.
            "open_role_count": len(postings) if status != "failed" else None,
            "consecutive_failures": failures,
            "updated_at": now_iso,
        }
        if row["open_role_count"] is None:
            del row["open_role_count"]
        if product_id:
            row["product_id"] = product_id
        try:
            db.table(BOARD_TOKEN_TABLE).upsert(row, on_conflict="provider,board_token").execute()
            written += 1
        except Exception as exc:
            log.warning("[CompanyFirst] board-token upsert failed for %s: %s", ref.cache_key, exc)
    return written


def queue_board_tokens(db, refs: dict[str, BoardRef], product_id: Optional[str] = None) -> int:
    """Cache board tokens that were discovered but not polled this run.

    Written with last_status NULL and last_fetched_at NULL -- "known, never
    fetched" is a third state distinct from ok/empty/failed, and
    load_cached_board_refs orders by last_fetched_at so these come first
    next run. The company name is the token-derived fallback only; the real
    display name arrives when the board is actually fetched.

    Deliberately an upsert that does NOT clobber an existing row's status:
    a board already fetched in a previous run keeps its own record, because
    re-discovering it is not new information about it.
    """
    written = 0
    for key, ref in refs.items():
        row = {
            "provider": ref.provider,
            "board_token": ref.token,
            "company": company_name_from_token(ref.token),
            "discovered_from": "brave_ats_discovery",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if product_id:
            row["product_id"] = product_id
        try:
            db.table(BOARD_TOKEN_TABLE).upsert(row, on_conflict="provider,board_token").execute()
            written += 1
        except Exception as exc:
            log.warning("[CompanyFirst] board-token queue failed for %s: %s", key, exc)
    return written


# ── Step 4: per-company decision-maker lookup ────────────────────────────
#
# The ONLY place site:linkedin.com/in appears in v2.

_SNIPPET_TITLE_PATTERNS = [
    # "Jane Doe. VP of Engineering at Acme Corp. Austin, TX"
    re.compile(r"\b((?:Chief|VP|Vice President|SVP|EVP|Head|Director|Senior Director|Managing Director|CTO|CIO|CISO|CEO|COO)\b[^.·|]{0,60}?)\s+(?:at|@)\s+", re.IGNORECASE),
    # "... — Head of Platform — Acme"
    re.compile(r"[-–—·|]\s*((?:Chief|VP|Vice President|SVP|EVP|Head of|Director of|CTO|CIO|CISO)\b[^-–—·|]{0,60})", re.IGNORECASE),
    # "Experience: Acme Corp · VP, Infrastructure"
    re.compile(r"·\s*((?:VP|Head|Director|Chief)\b[^·]{0,60})", re.IGNORECASE),
]


def parse_decision_maker_title(snippet: str) -> Optional[str]:
    """
    The contact's title out of a LinkedIn-profile search SNIPPET.

    Kelvin's rule: "the title parsed from the snippet". Returns None --
    never a placeholder -- when no title is parseable, because MKT-O2
    hands this field to the LLM as "real signal context (never invent
    anything beyond this)". A guess here becomes a false claim in
    outbound copy; that exact bug shipped once already.
    """
    if not snippet:
        return None
    for pattern in _SNIPPET_TITLE_PATTERNS:
        match = pattern.search(snippet)
        if match:
            title = match.group(1).strip(" .,-–—·|")
            # It must classify as a real seniority tier, or it isn't a
            # title we can assert.
            if title and classify_seniority(title) != "unknown":
                return title
    return None


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Words that carry no identity -- "Labs Inc" must not match every company
# with "Labs" in the name.
_COMPANY_STOPWORDS = frozenset({
    "inc", "llc", "ltd", "corp", "corporation", "co", "gmbh", "bv", "plc",
    "limited", "holdings", "group", "the", "and",
})


def _normalise_company(value: str) -> str:
    return _NON_ALNUM_RE.sub("", (value or "").lower())


def _mentions_company(text: str, company: str) -> bool:
    """True when `text` plausibly names `company`.

    Compared with punctuation and casing removed, because an ATS token
    yields "Vector Labs" while the profile says "VectorLabs". A company
    name that reduces to nothing but stopwords is treated as unverifiable
    (False) rather than matching everything.
    """
    normalised = _normalise_company(company)
    if not normalised:
        return False
    haystack = _normalise_company(text)
    if normalised in haystack:
        return True
    # Fall back to the distinctive words only, so "Acme Technologies Inc"
    # still matches a profile that says just "Acme Technologies".
    words = [w for w in _NON_ALNUM_RE.sub(" ", (company or "").lower()).split()
             if w and w not in _COMPANY_STOPWORDS]
    if not words:
        return False
    return _normalise_company("".join(words)) in haystack


def find_decision_maker(
    scraper,
    company: str,
    *,
    stats: FunnelStats,
    titles: Iterable[str] = DECISION_MAKER_TITLES,
    sleep: Optional[Callable[[float], None]] = None,
) -> dict[str, Optional[str]]:
    """
    One Brave query for one company's likely buyer. Returns
    {"name", "title", "profile_url"}, any of which may be None.

    Reads Brave's index of public profile pages; does NOT fetch
    linkedin.com (no LinkedIn scraping, per standing rule).
    """
    title_clause = " OR ".join(f'"{t}"' for t in titles)
    query = f'site:linkedin.com/in "{company}" ({title_clause})'
    _pace(sleep)
    items = scraper._search(query)
    stats.queries_decision_maker += 1

    for item in items:
        snippet = item.get("snippet") or ""
        title = parse_decision_maker_title(snippet)
        if not title:
            continue
        rejected, _reason = is_negative_title(title)
        if rejected:
            continue
        # The company must actually appear on the profile. Found live
        # 2026-10-01: the same profile (linkedin.com/in/jerrykrikheli) came
        # back for two different companies, because a `site:linkedin.com/in
        # "Company"` query also matches a profile that merely MENTIONS the
        # company. Writing that lead asserts "<person> is <title> at
        # <company>" as fact -- the same class of fabrication as the
        # job_posting_title bug. No association, no contact.
        if not _mentions_company(f"{item.get('title', '')} {snippet}", company):
            continue
        # Name comes from the result title's leading segment, which for a
        # profile page is the person. Nothing else is claimed.
        raw = (item.get("title") or "").strip()
        name = re.split(r"\s+[-–—|]\s+", raw)[0].strip() or None
        stats.decision_makers_found += 1
        return {"name": name, "title": title, "profile_url": item.get("link") or None}

    return {"name": None, "title": None, "profile_url": None}


# ── Steps 2+3: expand and qualify ────────────────────────────────────────

@dataclass
class CompanyCandidate:
    """One company, its relevant open roles, and everything derived from
    them. Built before any qualification decision so the funnel can
    attribute a drop to a stage rather than losing the candidate."""

    ref: BoardRef
    company: str
    domain: Optional[str]
    matching: list[AtsPosting]
    all_postings: list[AtsPosting]

    @property
    def jd_text(self) -> str:
        return " ".join(p.description_text or "" for p in self.matching).strip()

    @property
    def freshest_age_days(self) -> Optional[int]:
        ages = [p.age_days for p in self.matching if p.age_days is not None]
        return min(ages) if ages else None

    @property
    def primary_role(self) -> Optional[str]:
        return self.matching[0].title if self.matching else None

    @property
    def location(self) -> Optional[str]:
        for p in self.matching:
            if p.location:
                return p.location
        return None


def build_candidates(
    boards: dict[str, list[AtsPosting]],
    refs: dict[str, BoardRef],
    keywords: Iterable[str],
    *,
    max_age_days: Optional[int],
    stats: FunnelStats,
) -> list[CompanyCandidate]:
    """Boards -> companies that actually have a relevant open role."""
    keywords = list(keywords)
    out: list[CompanyCandidate] = []
    for key, postings in boards.items():
        ref = refs.get(key)
        if ref is None:
            continue
        stats.companies_considered += 1
        stats.postings_returned += len(postings)
        matching = matching_postings(postings, keywords, max_age_days=max_age_days)
        stats.postings_title_matched += len(matching)
        if not matching:
            stats.dropped_no_matching_role += 1
            continue
        company = next((p.company for p in postings if p.company), None) or company_name_from_token(ref.token)
        out.append(CompanyCandidate(
            ref=ref, company=company,
            domain=company_domain_from_postings(postings),
            matching=matching, all_postings=postings,
        ))
    return out


def qualify_candidate(
    candidate: CompanyCandidate,
    *,
    contact: Optional[dict] = None,
    config: Optional[ScoringConfig] = None,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """
    Scores + routes ONE company candidate. Pure.

    Note what is NOT done here: no email is invented. `email_grade` is
    "unknown" until core/email_finder actually verifies something, and
    "unknown" never sends (lead_qualification.email_may_send). The 19
    rows with fabricated addresses in the 2026-10-01 cleanup came from
    guessing; v2 structurally cannot.
    """
    cfg = config or ScoringConfig()
    contact = contact or {}
    contact_title = contact.get("title")

    jd = candidate.jd_text
    stack = extract_stack(jd) or extract_stack(candidate.primary_role)
    band = headcount_band(jd)
    funding = funding_recency_months(jd, today=today)

    rejected, negative_reason = is_negative_title(contact_title)

    fit = fit_score(
        seniority=classify_seniority(contact_title),
        headcount_band_value=band,
        stack=stack,
        has_domain=bool(candidate.domain),
        config=cfg,
    )
    intent = intent_score(
        posting_age_days=candidate.freshest_age_days,
        open_role_count=len(candidate.matching),
        stack=stack,
        funding_months=funding,
        config=cfg,
    )

    grade = grade_email(None)  # nothing verified yet -> "unknown"

    if rejected:
        route, route_reason = ROUTE_REJECT, f"negative_title:{negative_reason}"
    else:
        route, route_reason = route_lead(
            email_grade=grade, has_domain=bool(candidate.domain), fit=fit, intent=intent, config=cfg,
        )

    return {
        "company": candidate.company,
        "domain": candidate.domain,
        "contact_name": contact.get("name"),
        "contact_profile_url": contact.get("profile_url"),
        "title": contact_title,
        "seniority": classify_seniority(contact_title),
        "location": candidate.location or parse_location(jd),
        "job_posting_url": candidate.matching[0].url if candidate.matching else None,
        "job_posting_title": candidate.primary_role,
        "job_posting_date": (candidate.matching[0].posted_at.isoformat()
                             if candidate.matching and candidate.matching[0].posted_at else None),
        "open_role_count": len(candidate.matching),
        "stack_tags": stack,
        "headcount_band": band,
        "funding_months": funding,
        "email_grade": grade,
        "fit_score": fit.value,
        "intent_score": intent.value,
        "score_reasons": fit.reasons + intent.reasons,
        "lead_route": route,
        "route_reason": route_reason,
        "negative_reason": negative_reason,
        "ats_provider": candidate.ref.provider,
        "ats_board_token": candidate.ref.token,
    }


# ── Orchestrator ─────────────────────────────────────────────────────────

def find_company_first_signals(
    product_id: str,
    keywords: Iterable[str],
    *,
    db=None,
    scraper=None,
    ats_client: Optional[AtsBoardClient] = None,
    max_queries: int = 40,
    max_boards: int = MAX_BOARDS_PER_RUN,
    max_age_days: Optional[int] = 30,
    existing_domains: Optional[set] = None,
    existing_linkedin_urls: Optional[set] = None,
    config: Optional[ScoringConfig] = None,
    lookup_decision_makers: bool = True,
    today: Optional[date] = None,
    sleep: Optional[Callable[[float], None]] = None,
    stats: Optional[FunnelStats] = None,
) -> tuple[list[dict], FunnelStats]:
    """
    The whole v2 pipeline for one product. Returns (qualified rows,
    FunnelStats). Does NOT write to mse_leads -- same contract as
    find_leads / find_job_posting_signals, so it stays independently
    testable and CLI-callable.

    `max_queries` is a HARD cap across discovery and decision-maker
    lookups together; a run can be held to ≤40 Brave queries as
    instructed, and the split is governed by
    DECISION_MAKER_BUDGET_SHARE so neither step starves the other.
    """
    keywords = [k for k in keywords if k and k.strip()]
    # The caller may own the stats object so that a mid-run exception
    # still leaves it with whatever was established -- notably the Brave
    # queries already spent, which are billed when issued.
    stats = stats if stats is not None else FunnelStats()
    cfg = config or ScoringConfig()
    existing_domains = existing_domains or set()
    # mse_leads.linkedin_url carries a UNIQUE partial index, so a profile
    # already on another lead must not be written again -- a batch insert
    # is all-or-nothing and one collision discards every good lead with it.
    existing_linkedin_urls = set(existing_linkedin_urls or set())

    if scraper is None or not getattr(scraper, "api_key", None):
        # Same graceful-skip contract as every other quota/credential-
        # gated path in this codebase.
        log.warning("[CompanyFirst] no Brave scraper/API key -- returning no leads")
        return [], stats

    dm_budget = int(max_queries * DECISION_MAKER_BUDGET_SHARE) if lookup_decision_makers else 0
    discovery_budget = max_queries - dm_budget

    # Step 1: cache first (free), then discovery (paid).
    refs: dict[str, BoardRef] = {}
    prior_failures: dict[str, int] = {}
    if db is not None:
        for ref in load_cached_board_refs(db, product_id, prior_failures=prior_failures):
            refs[ref.cache_key] = ref
        stats.board_tokens_from_cache = len(refs)
    cached_keys = set(refs)

    for ref in discover_board_refs(scraper, keywords, max_queries=discovery_budget, stats=stats, sleep=sleep):
        refs.setdefault(ref.cache_key, ref)

    if not refs:
        log.warning("[CompanyFirst] no ATS board tokens discovered or cached -- nothing to expand")
        return [], stats

    # Step 2: expand each board via its ATS JSON API -- zero Brave cost,
    # but capped on wall clock (see MAX_BOARDS_PER_RUN). Cached boards are
    # polled first: they are the ones a previous run already proved real.
    client = ats_client or AtsBoardClient()
    ordered = sorted(refs.values(), key=lambda r: r.cache_key not in cached_keys)
    if len(ordered) > max_boards:
        log.info("[CompanyFirst] %d boards discovered, polling the first %d this run",
                 len(ordered), max_boards)
        stats.boards_skipped_over_cap = len(ordered) - max_boards
        ordered = ordered[:max_boards]
    boards = client.fetch_boards(ordered)
    stats.boards_polled = len(boards)
    stats.ats_client_stats = client.stats.as_dict()

    if db is not None:
        upsert_board_tokens(
            db,
            {k: (refs[k], v, company_domain_from_postings(v)) for k, v in boards.items() if k in refs},
            product_id,
            board_status=client.board_status,
            prior_failures=prior_failures,
        )
        # Cache the tokens we discovered but did NOT poll this run, as
        # unfetched rows. The first live run discovered 186 boards from 26
        # Brave queries against a 60-board cap -- without this, the other
        # 126 are thrown away and re-discovered next week at full Brave
        # cost, which defeats the point of having a cache at all. These
        # rows carry last_status NULL (never fetched), so
        # load_cached_board_refs returns them first next run, for free.
        unpolled = {k: ref for k, ref in refs.items() if k not in boards}
        if unpolled:
            queue_board_tokens(db, unpolled, product_id)

    # Step 3: candidates -> qualification.
    candidates = build_candidates(boards, refs, keywords, max_age_days=max_age_days, stats=stats)

    # Highest intent first, so a tight decision-maker budget is spent on
    # the companies most worth a contact lookup rather than alphabetically.
    candidates.sort(key=lambda c: (len(c.matching), -(c.freshest_age_days or 10_000)), reverse=True)

    rows: list[dict] = []
    seen_domains: set = set()
    for candidate in candidates:
        domain = candidate.domain
        if domain and (domain in existing_domains or domain in seen_domains):
            stats.dropped_duplicate_company += 1
            continue

        # Step 4: decision-maker lookup, budgeted. The ONLY
        # site:linkedin.com/in query in v2.
        contact = None
        if lookup_decision_makers and stats.queries_decision_maker < dm_budget:
            contact = find_decision_maker(scraper, candidate.company, stats=stats, sleep=sleep)

        qualified = qualify_candidate(candidate, contact=contact, config=cfg, today=today)

        route = qualified["lead_route"]
        if route == ROUTE_REJECT:
            reason = qualified["route_reason"]
            if qualified["negative_reason"]:
                key = qualified["negative_reason"]
                stats.dropped_negative_title[key] = stats.dropped_negative_title.get(key, 0) + 1
            elif "fit" in reason:
                stats.dropped_low_fit += 1
            else:
                stats.dropped_low_intent += 1
            continue

        if route == ROUTE_OUTBOUND_EMAIL:
            stats.routed_outbound_email += 1
        else:
            stats.routed_manual_linkedin += 1

        # linkedin_url is globally UNIQUE in mse_leads. The same profile
        # legitimately surfaces for more than one company (and 41 rows
        # already carried one before v2 ran at all), so drop the duplicate
        # ATTRIBUTION rather than the whole lead -- the company signal is
        # still real, it just has no usable contact.
        profile = qualified.get("contact_profile_url")
        if profile and profile in existing_linkedin_urls:
            stats.dropped_duplicate_contact += 1
            qualified["contact_profile_url"] = None
            qualified["contact_name"] = None
            qualified["title"] = None
        elif profile:
            existing_linkedin_urls.add(profile)

        if domain:
            seen_domains.add(domain)
        qualified["product_id"] = product_id
        rows.append(qualified)

    return rows, stats


# Every mse_leads column this module is allowed to write, verified against
# microsaas-prod's information_schema on 2026-10-01 -- NOT assumed.
#
# This exists because the first live v2 run died on the INSERT with
# PGRST204 "Could not find the 'name' column of 'mse_leads'": the real
# schema has first_name/last_name, and a unit test that asserted against
# a hand-written allowlist happily agreed with the wrong guess. Filtering
# through a verified set means an unknown key is dropped (and logged) here
# instead of 400-ing the whole batch insert in production.
MSE_LEADS_WRITABLE_COLUMNS = frozenset({
    "product_id", "first_name", "last_name", "title", "company", "domain",
    "email", "email_status", "linkedin_url", "source", "location", "status",
    "confidence_score", "notes", "job_posting_url", "job_posting_title",
    "job_posting_date", "job_posting_description", "job_posting_stack_keywords",
    "seniority", "stack_tags", "headcount_band", "funding_months",
    "email_grade", "fit_score", "intent_score", "score_reasons",
    "lead_route", "open_role_count",
})


def split_contact_name(name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(first_name, last_name) for mse_leads' two real columns. A
    single-token name becomes first_name with last_name left NULL rather
    than duplicated -- an invented surname is still an invented fact."""
    if not name or not name.strip():
        return None, None
    parts = name.strip().split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def to_mse_lead_row(qualified: dict, *, source: str) -> dict:
    """
    Project a qualified candidate onto mse_leads' real columns
    (migration 20261001000057 for the v2 fields).

    Internal-only keys (route_reason, negative_reason, ats_*) are dropped
    rather than written, any None is omitted so a nullable column stays
    NULL instead of being set to a literal "None", and anything not in
    MSE_LEADS_WRITABLE_COLUMNS is refused with a warning.
    """
    first_name, last_name = split_contact_name(qualified.get("contact_name"))
    row = {
        "product_id": qualified["product_id"],
        "source": source,
        "company": qualified["company"],
        "domain": qualified.get("domain"),
        "title": qualified.get("title"),
        "first_name": first_name,
        "last_name": last_name,
        # The decision-maker step's profile URL. Previously discarded,
        # which left the manual-LinkedIn track with no way to reach the
        # person it had deliberately routed there.
        "linkedin_url": qualified.get("contact_profile_url"),
        "location": qualified.get("location"),
        "job_posting_url": qualified.get("job_posting_url"),
        "job_posting_title": qualified.get("job_posting_title"),
        "job_posting_date": qualified.get("job_posting_date"),
        "open_role_count": qualified.get("open_role_count"),
        "stack_tags": qualified.get("stack_tags") or None,
        "headcount_band": qualified.get("headcount_band"),
        "funding_months": qualified.get("funding_months"),
        "email_grade": qualified.get("email_grade"),
        "fit_score": qualified.get("fit_score"),
        "intent_score": qualified.get("intent_score"),
        "score_reasons": qualified.get("score_reasons") or None,
        "lead_route": qualified.get("lead_route"),
        "seniority": qualified.get("seniority"),
        "confidence_score": qualified.get("fit_score"),
    }
    unknown = set(row) - MSE_LEADS_WRITABLE_COLUMNS
    if unknown:
        log.warning("[CompanyFirst] refusing to write unknown mse_leads column(s): %s", sorted(unknown))
    return {k: v for k, v in row.items() if v is not None and k in MSE_LEADS_WRITABLE_COLUMNS}
