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

import html
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Optional

from agents.marketing.lead_qualification import (
    ROUTE_MANUAL_LINKEDIN,
    company_fit_score,
    email_may_send,
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
from agents.marketing.company_classification import (
    CONSULTING_EXCLUSION_POLICY,
    CompanyClassification,
    ExclusionPolicy,
    classify_company,
    normalise_company_name,
    text_mentions_company,
)
from agents.marketing.contact_discovery import (
    ContactDiscoveryStats,
    discover_contact_free,
)
from agents.marketing.domain_resolution import (
    MAX_BRAVE_DOMAIN_QUERIES,
    DomainResolution,
    resolve_domain,
)
from agents.marketing.role_taxonomy import (
    RoleTaxonomy,
    intent_multiplier_for,
    size_proxy_in_band,
)
from agents.marketing.role_taxonomy import matching_postings as taxonomy_matching_postings
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

# Board expansion is bounded on WALL CLOCK, not on a board count (Kelvin's
# decision 4, 2026-10-01: "raise the per-run cap from 60 to all cached
# boards, with a wall-clock bound"). It costs no Brave quota, so a count cap
# was throwing away free signal -- the first live runs discovered 225 and
# polled 60, leaving 165 unread. Time is the real constraint: one HTTP
# request (20s timeout) plus a 1-2.5s pause per board. A bound is still
# required so a run cannot outlive its scheduler and strand the
# mse_lead_finder_runs row at "running" with no funnel_stats.
#
# MAX_BOARDS_PER_RUN remains as a safety ceiling, raised well above the
# cache size, so a runaway cache cannot produce an unbounded sweep even if
# the clock budget is misconfigured.
BOARD_SWEEP_BUDGET_SECONDS = 900
MAX_BOARDS_PER_RUN = 1000

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
    boards_not_reached: int = 0
    postings_returned: int = 0
    postings_title_matched: int = 0
    companies_considered: int = 0
    role_drop_reasons: dict[str, int] = field(default_factory=dict)
    dropped_no_matching_role: int = 0
    # Stage 1 (company qualification -- no contact required)
    stage1_passed: int = 0
    dropped_excluded: dict[str, int] = field(default_factory=dict)
    dropped_size_proxy: int = 0
    dropped_no_domain: int = 0
    domain_sources: dict[str, int] = field(default_factory=dict)
    brave_domain_queries: int = 0
    downweighted: int = 0
    # Stage 2 (contact lookup -- only for Stage 1 passers)
    contact_found: int = 0
    contacts_from_free_sources: dict[str, int] = field(default_factory=dict)
    contact_discovery: dict = field(default_factory=dict)
    contact_pending: int = 0
    email_grades: dict[str, int] = field(default_factory=dict)
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
        d["dropped_excluded_total"] = sum(self.dropped_excluded.values())
        d["queries_total"] = (self.queries_discovery + self.queries_decision_maker
                              + self.brave_domain_queries)
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
        # Deliberately NOT filtered by product_id, despite taking one.
        #
        # mse_ats_board_tokens is UNIQUE on (provider, board_token) -- one
        # row per company board, globally. Filtering reads by product_id
        # would therefore be wrong twice over: a board first discovered for
        # consulting would be invisible to Cloud Decoded and get
        # re-discovered at full Brave cost, and the upsert that followed
        # would flip the row's product_id, making it invisible to
        # consulting instead. The two branches would take turns paying for
        # the same tokens forever.
        #
        # A company's ATS board is not product-specific anyway -- a company
        # hiring platform engineers is a candidate for both branches, and
        # which product a lead belongs to is decided per posting by the
        # routing step, not by who happened to find the board first.
        # product_id is kept on the row as provenance ("who discovered
        # this"), and the caller still scopes leads per product.
        rows = (db.table(BOARD_TOKEN_TABLE)
                .select("provider,board_token,consecutive_failures")
                .limit(limit).execute().data or [])
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

_TITLE_LEAD = (
    r"(?:Chief|VP|Vice[ -]President|SVP|EVP|Head|Director|Senior Director"
    r"|Managing Director|CTO|CIO|CISO|CEO|COO)"
)
# A segment separator is a dash/bullet/pipe with WHITESPACE around it, not a
# bare hyphen. Live 2026-10-01: Tailscale's CTO came through as
# "CTO &amp; co" because the old pattern excluded bare "-", which truncated
# "co-founder" at its own hyphen. Hyphenated titles are normal
# ("co-founder", "Vice-President"), so only a spaced separator may end a
# title.
_NOT_SEPARATOR = r"(?:(?!\s[-–—·|]\s)[^.·|])"

_SNIPPET_TITLE_PATTERNS = [
    # "Jane Doe. VP of Engineering at Acme Corp. Austin, TX"
    re.compile(rf"\b({_TITLE_LEAD}\b{_NOT_SEPARATOR}{{0,60}}?)\s+(?:at|@)\s+", re.IGNORECASE),
    # "... — Head of Platform — Acme"
    re.compile(rf"\s[-–—·|]\s*({_TITLE_LEAD}\b{_NOT_SEPARATOR}{{0,60}})", re.IGNORECASE),
    # "Experience: Acme Corp · VP, Infrastructure"
    re.compile(rf"·\s*({_TITLE_LEAD}\b[^·]{{0,60}})", re.IGNORECASE),
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
    # Brave returns HTML-escaped text. Without this, Tailscale's CTO was
    # stored as "CTO &amp; co" -- and that field is handed to MKT-O2 as
    # "real signal context", so the escape artefact would appear verbatim
    # in outbound copy.
    snippet = html.unescape(snippet)
    for pattern in _SNIPPET_TITLE_PATTERNS:
        match = pattern.search(snippet)
        if match:
            title = match.group(1).strip(" .,-–—·|")
            # It must classify as a real seniority tier, or it isn't a
            # title we can assert.
            if title and classify_seniority(title) != "unknown":
                return title
    return None


# Company-name comparison lives in company_classification -- the lowest
# module in this import graph that needs it. Previously a private copy
# here; two normalisers that disagree about whether "Acme Corp" and
# "acme-corp" are the same company is exactly the near-duplicate logic the
# DRY rule exists to prevent. Aliased rather than renamed at every call
# site so the existing tests keep addressing the same names.
_normalise_company = normalise_company_name
_mentions_company = text_mentions_company


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
        if not _mentions_company(html.unescape(f"{item.get('title', '')} {snippet}"), company):
            continue
        # Name comes from the result title's leading segment, which for a
        # profile page is the person. Nothing else is claimed.
        raw = html.unescape((item.get("title") or "").strip())
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
    # Title markers from the matching roles ("founding", "head of", ...).
    intent_boosts: list[str] = field(default_factory=list)

    @property
    def total_open_roles(self) -> int:
        """ALL open roles on the board, not just the matching ones. This is
        the company-SIZE proxy (decision 4); the matching count is a
        separate intent signal."""
        return len(self.all_postings)

    @property
    def jd_text_all(self) -> str:
        """Description text across the whole board, for company
        classification -- a single posting rarely contains the "we are a
        consultancy" paragraph, but the board as a whole reliably does."""
        return " ".join(p.description_text or "" for p in self.all_postings[:8]).strip()

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
    taxonomy: RoleTaxonomy,
    *,
    max_age_days: Optional[int],
    stats: FunnelStats,
) -> list[CompanyCandidate]:
    """Boards -> companies that actually have a relevant open role.

    Takes a RoleTaxonomy rather than a keyword list (decision 2). The
    per-reason drop counts it returns are what distinguish "this posting is
    not an infrastructure role" from "this posting is the wrong FUNCTION"
    (a sales or solutions engineer), which is what tells you whether the
    include list or the negative list needs tuning.
    """
    out: list[CompanyCandidate] = []
    for key, postings in boards.items():
        ref = refs.get(key)
        if ref is None:
            continue
        stats.companies_considered += 1
        stats.postings_returned += len(postings)
        matching, drops = taxonomy_matching_postings(postings, taxonomy, max_age_days=max_age_days)
        for reason, count in drops.items():
            stats.role_drop_reasons[reason] = stats.role_drop_reasons.get(reason, 0) + count
        stats.postings_title_matched += len(matching)
        if not matching:
            stats.dropped_no_matching_role += 1
            continue
        boosts: list[str] = []
        for posting in matching:
            boosts.extend(taxonomy.match(posting.title).boosts)
        company = next((p.company for p in postings if p.company), None) or company_name_from_token(ref.token)
        out.append(CompanyCandidate(
            ref=ref, company=company,
            domain=company_domain_from_postings(postings),
            matching=matching, all_postings=postings,
            intent_boosts=sorted(set(boosts)),
        ))
    return out


# ── Stage 1: company qualification (NO contact required) ─────────────────

@dataclass
class Stage1Result:
    """Why a company passed or failed Stage 1. Kelvin's decision 1: the
    gate is matching role + resolved domain + exclusions pass + size proxy
    in band -- and a contact is explicitly NOT part of it."""

    passed: bool = False
    reasons: list[str] = field(default_factory=list)
    classification: Optional[CompanyClassification] = None
    domain: Optional[DomainResolution] = None
    size_ok: bool = False
    fit_multiplier: float = 1.0
    failure_stage: Optional[str] = None


def stage1_qualify(
    candidate: CompanyCandidate,
    policy: ExclusionPolicy,
    *,
    scraper=None,
    brave_domain_budget: int = 0,
    http_get: Optional[Callable[..., Any]] = None,
    dns_resolves: Optional[Callable[[str], bool]] = None,
    sleep: Optional[Callable[[float], None]] = None,
    stats_sink: Optional[dict] = None,
) -> Stage1Result:
    """
    Company qualification, in the order that spends the least to fail
    fastest: exclusions (free, prose already in hand), then the size proxy
    (free, already counted), then domain resolution (may cost a Brave
    query). A company that fails on exclusions never consumes domain
    budget.
    """
    result = Stage1Result()
    jd = candidate.jd_text_all

    # (1) Exclusions -- free.
    classification = classify_company(candidate.company, jd)
    result.classification = classification
    excluded, multiplier, policy_reasons = policy.decide(classification)
    result.reasons.extend(policy_reasons)
    result.fit_multiplier = multiplier
    if excluded:
        result.failure_stage = "excluded"
        return result

    # (2) Size proxy -- free, from the board we already fetched.
    size_ok, size_reason = size_proxy_in_band(candidate.total_open_roles)
    result.size_ok = size_ok
    result.reasons.append(size_reason)
    if not size_ok:
        result.failure_stage = "size_proxy"
        return result

    # (3) Domain -- mandatory (decision 5). May cost one Brave query.
    resolution = resolve_domain(
        candidate.company,
        token=candidate.ref.token,
        jd_text=jd,
        postings=candidate.all_postings,
        known_domain=candidate.domain,
        scraper=scraper,
        brave_budget_remaining=brave_domain_budget,
        http_get=http_get,
        dns_resolves=dns_resolves,
        sleep=sleep,
        stats=stats_sink,
    )
    result.domain = resolution
    if not resolution.resolved:
        result.reasons.append(f"no domain (tried {','.join(resolution.attempts)})")
        result.failure_stage = "no_domain"
        return result
    result.reasons.append(f"domain={resolution.domain} via {resolution.method}")

    result.passed = True
    return result


def qualify_candidate(
    candidate: CompanyCandidate,
    *,
    stage1: Optional[Stage1Result] = None,
    contact: Optional[dict] = None,
    email_grade: Optional[str] = None,
    email: Optional[str] = None,
    config: Optional[ScoringConfig] = None,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """
    Scores and routes ONE company candidate that has already PASSED Stage 1.

    Two-stage model (decision 1): Stage 1 is the gate, so nothing here
    rejects on score. company_fit_score ranks the company on its own
    evidence; contact seniority is a separate ranking signal, never a gate.
    A candidate with no contact gets status 'company_qualified' /
    contact_status 'pending' and is retried next run from the cache -- it is
    NOT dropped, which was the whole defect this restructure fixes.

    Still never invents an email: `email_grade` defaults to "unknown" and
    only "valid" routes to outbound email.
    """
    cfg = config or ScoringConfig()
    contact = contact or {}
    contact_title = contact.get("title")

    jd = candidate.jd_text
    stack = extract_stack(jd) or extract_stack(candidate.jd_text_all) or extract_stack(candidate.primary_role)
    band = headcount_band(jd)
    funding = funding_recency_months(jd, today=today)

    domain = stage1.domain.domain if (stage1 and stage1.domain) else candidate.domain
    domain_source = stage1.domain.method if (stage1 and stage1.domain) else None
    multiplier = stage1.fit_multiplier if stage1 else 1.0
    size_ok = stage1.size_ok if stage1 else False

    fit = company_fit_score(
        stack=stack,
        size_proxy_ok=size_ok,
        domain_resolved=bool(domain),
        intent_boosts=candidate.intent_boosts,
        exclusion_multiplier=multiplier,
        config=cfg,
    )
    undated_mult, undated_reason = intent_multiplier_for(candidate.matching)
    intent = intent_score(
        posting_age_days=candidate.freshest_age_days,
        open_role_count=len(candidate.matching),
        stack=stack,
        funding_months=funding,
        undated_multiplier=undated_mult,
        config=cfg,
    )

    grade = email_grade or grade_email(None)

    rejected, negative_reason = is_negative_title(contact_title)
    if rejected:
        # The COMPANY still qualified; only this contact is wrong. Keep the
        # company and drop the contact rather than discarding a real signal.
        contact = {}
        contact_title = None

    has_contact = bool(contact.get("name") or contact_title)
    if has_contact and domain and email_may_send(grade):
        route = ROUTE_OUTBOUND_EMAIL
        route_reason = "contact + valid email"
    elif has_contact:
        route = ROUTE_MANUAL_LINKEDIN
        route_reason = f"contact, email_grade={grade}"
    else:
        route = ROUTE_MANUAL_LINKEDIN
        route_reason = "awaiting contact"

    status = "pending_dm" if has_contact else "company_qualified"
    contact_status = "found" if has_contact else "pending"

    reasons = list(fit.reasons) + list(intent.reasons)
    if stage1:
        reasons = list(stage1.reasons) + reasons

    return {
        "company": candidate.company,
        "domain": domain,
        "domain_source": domain_source,
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
        "size_proxy_open_roles": candidate.total_open_roles,
        "stack_tags": stack,
        "headcount_band": band,
        "funding_months": funding,
        "email": email,
        "email_grade": grade,
        "fit_score": fit.value,
        "intent_score": intent.value,
        "score_reasons": reasons,
        "company_tags": sorted(stage1.classification.tags) if (stage1 and stage1.classification) else [],
        "stage1_reasons": list(stage1.reasons) if stage1 else [],
        "lead_route": route,
        "route_reason": route_reason,
        "status": status,
        "contact_status": contact_status,
        "negative_reason": negative_reason,
        "ats_provider": candidate.ref.provider,
        "ats_board_token": candidate.ref.token,
    }


# ── Orchestrator ─────────────────────────────────────────────────────────

def find_company_first_signals(
    product_id: str,
    keywords: Iterable[str],
    *,
    policy: Optional[ExclusionPolicy] = None,
    taxonomy: Optional[RoleTaxonomy] = None,
    role_taxonomy_config: Optional[dict] = None,
    email_resolver: Optional[Callable[[str, str], tuple[Optional[str], str]]] = None,
    max_brave_domain_queries: int = MAX_BRAVE_DOMAIN_QUERIES,
    http_get: Optional[Callable[..., Any]] = None,
    dns_resolves: Optional[Callable[[str], bool]] = None,
    db=None,
    scraper=None,
    ats_client: Optional[AtsBoardClient] = None,
    max_queries: int = 40,
    max_boards: int = MAX_BOARDS_PER_RUN,
    board_sweep_budget_seconds: float = BOARD_SWEEP_BUDGET_SECONDS,
    clock: Optional[Callable[[], float]] = None,
    max_age_days: Optional[int] = 30,
    existing_domains: Optional[set] = None,
    existing_companies: Optional[set] = None,
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
    # Role matching is taxonomy-driven now (decision 2). `keywords` is still
    # accepted and still drives Brave DISCOVERY queries -- those must stay
    # short literal phrases to be useful as search terms -- but which
    # POSTINGS count as relevant is the taxonomy's job.
    taxonomy = taxonomy or RoleTaxonomy.from_config(role_taxonomy_config)
    policy = policy or CONSULTING_EXCLUSION_POLICY
    # The caller may own the stats object so that a mid-run exception
    # still leaves it with whatever was established -- notably the Brave
    # queries already spent, which are billed when issued.
    stats = stats if stats is not None else FunnelStats()
    cfg = config or ScoringConfig()
    existing_domains = existing_domains or set()
    # "One company, one pipeline, ever" cannot rest on domain alone here:
    # ATS feeds carry only the ATS's own URL, so v2 leads have no domain and
    # domain dedup matches nothing. The 2026-10-01 live runs wrote Clutch
    # twice, once per product, for exactly this reason.
    existing_companies = set(existing_companies or set())
    # mse_leads.linkedin_url carries a UNIQUE partial index, so a profile
    # already on another lead must not be written again -- a batch insert
    # is all-or-nothing and one collision discards every good lead with it.
    existing_linkedin_urls = set(existing_linkedin_urls or set())

    if scraper is None or not getattr(scraper, "api_key", None):
        # Same graceful-skip contract as every other quota/credential-
        # gated path in this codebase.
        log.warning("[CompanyFirst] no Brave scraper/API key -- returning no leads")
        return [], stats

    # Three consumers of the Brave budget now: discovery, the per-company
    # domain fallback (decision 5, capped separately at 10) and the contact
    # lookup. The domain allowance is carved out FIRST because a company
    # with no domain fails Stage 1 outright -- spending the whole budget on
    # discovering boards we then cannot qualify is the worst split.
    domain_allowance = min(max_brave_domain_queries, max(0, max_queries // 4))
    max_brave_domain_queries = domain_allowance
    remaining = max(0, max_queries - domain_allowance)
    dm_budget = int(remaining * DECISION_MAKER_BUDGET_SHARE) if lookup_decision_makers else 0
    discovery_budget = remaining - dm_budget

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
        log.warning("[CompanyFirst] %d boards exceeds the %d safety ceiling -- truncating",
                    len(ordered), max_boards)
        ordered = ordered[:max_boards]
    log.info("[CompanyFirst] sweeping up to %d boards within %.0fs",
             len(ordered), board_sweep_budget_seconds)
    boards = client.fetch_boards(
        ordered, deadline_seconds=board_sweep_budget_seconds, clock=clock,
    )
    stats.boards_polled = len(boards)
    # Not reached within the time budget. NOT a failure -- nothing was
    # learned about these either way, and they stay cached for the next run.
    stats.boards_not_reached = max(0, len(ordered) - len(boards))
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

    # Step 3: candidates (taxonomy-matched roles).
    candidates = build_candidates(boards, refs, taxonomy, max_age_days=max_age_days, stats=stats)

    # Ordering decides who gets the scarce contact/domain budget, so it is
    # explicit rather than incidental (Kelvin's decision 3: "Dated postings
    # within 30 days rank first"):
    #   1. a dated posting inside the freshness window beats an undated one
    #   2. then the number of matching roles
    #   3. then recency, with undated treated as oldest
    def _rank(c: CompanyCandidate) -> tuple:
        ages = [p.age_days for p in c.matching if p.age_days is not None]
        has_fresh_dated = any(a <= (max_age_days or 30) for a in ages)
        return (1 if has_fresh_dated else 0, len(c.matching), -(min(ages) if ages else 10_000))

    candidates.sort(key=_rank, reverse=True)

    rows: list[dict] = []
    seen_domains: set = set()
    domain_stats_sink: dict = {}
    contact_stats = ContactDiscoveryStats()
    domain_budget = max_brave_domain_queries

    for candidate in candidates:
        # Company-level dedup BEFORE spending any budget on it.
        company_key = _normalise_company(candidate.company)
        if company_key and company_key in existing_companies:
            stats.dropped_duplicate_company += 1
            continue
        if candidate.domain and (candidate.domain in existing_domains or candidate.domain in seen_domains):
            stats.dropped_duplicate_company += 1
            continue

        # ── STAGE 1: is this company worth pursuing? No contact needed. ──
        stage1 = stage1_qualify(
            candidate, policy,
            scraper=scraper,
            brave_domain_budget=max(0, domain_budget - domain_stats_sink.get("brave_domain_queries", 0)),
            http_get=http_get,
            dns_resolves=dns_resolves,
            sleep=sleep,
            stats_sink=domain_stats_sink,
        )
        stats.brave_domain_queries = domain_stats_sink.get("brave_domain_queries", 0)

        if not stage1.passed:
            if stage1.failure_stage == "excluded":
                for reason in stage1.reasons:
                    if reason.startswith("excluded:"):
                        tag = reason.split(":", 1)[1].split("=", 1)[0]
                        stats.dropped_excluded[tag] = stats.dropped_excluded.get(tag, 0) + 1
            elif stage1.failure_stage == "size_proxy":
                stats.dropped_size_proxy += 1
            elif stage1.failure_stage == "no_domain":
                stats.dropped_no_domain += 1
            continue

        stats.stage1_passed += 1
        if stage1.fit_multiplier != 1.0:
            stats.downweighted += 1
        resolved_domain = stage1.domain.domain if stage1.domain else None
        if stage1.domain and stage1.domain.method:
            stats.domain_sources[stage1.domain.method] = stats.domain_sources.get(stage1.domain.method, 0) + 1

        if resolved_domain and (resolved_domain in existing_domains or resolved_domain in seen_domains):
            # Only discoverable after resolution -- two tokens can be the
            # same company.
            stats.dropped_duplicate_company += 1
            continue

        # ── STAGE 2: who do we talk to? Only for Stage 1 passers. ──
        #
        # FREE SOURCES FIRST (decision 5a): the JD's own named hiring
        # manager costs nothing at all, and the company's /team, /about,
        # /leadership pages cost one GET each against a domain Stage 1 has
        # already verified. Brave is the last resort, not the first.
        contact = None
        free = discover_contact_free(
            domain=resolved_domain,
            jd_text=candidate.jd_text_all,
            http_get=http_get,
            stats=contact_stats,
        )
        if free and free.is_usable:
            contact = {"name": free.name, "title": free.title, "profile_url": None}
            stats.contacts_from_free_sources[free.source or "unknown"] = (
                stats.contacts_from_free_sources.get(free.source or "unknown", 0) + 1
            )
        elif lookup_decision_makers and stats.queries_decision_maker < dm_budget:
            contact = find_decision_maker(scraper, candidate.company, stats=stats, sleep=sleep)

        # A profile already used elsewhere: drop the ATTRIBUTION, keep the
        # company -- mse_leads.linkedin_url is globally UNIQUE.
        if contact and contact.get("profile_url") in existing_linkedin_urls:
            stats.dropped_duplicate_contact += 1
            contact = None
        elif contact and contact.get("profile_url"):
            existing_linkedin_urls.add(contact["profile_url"])

        # Email discovery + grading, only once a domain exists.
        email_value, grade = None, grade_email(None)
        if resolved_domain and contact and contact.get("name") and email_resolver is not None:
            try:
                email_value, grade = email_resolver(contact["name"], resolved_domain)
            except Exception as exc:
                log.warning("[CompanyFirst] email resolution failed for %s@%s: %s",
                            contact.get("name"), resolved_domain, exc)
        stats.email_grades[grade] = stats.email_grades.get(grade, 0) + 1

        qualified = qualify_candidate(
            candidate, stage1=stage1, contact=contact,
            email_grade=grade, email=email_value, config=cfg, today=today,
        )

        if qualified["contact_status"] == "found":
            stats.contact_found += 1
        else:
            stats.contact_pending += 1

        if qualified["lead_route"] == ROUTE_OUTBOUND_EMAIL:
            stats.routed_outbound_email += 1
        else:
            stats.routed_manual_linkedin += 1

        if resolved_domain:
            seen_domains.add(resolved_domain)
        if company_key:
            existing_companies.add(company_key)
        qualified["product_id"] = product_id
        rows.append(qualified)

    stats.contact_discovery = contact_stats.as_dict()
    return rows, stats


# Every mse_leads column this module is allowed to write, verified against
# microsaas-prod's information_schema (2026-10-01) -- NOT assumed, plus the
# two-stage columns from migration 20261001000059.
#
# This exists because the first live v2 run died on the INSERT with PGRST204
# "Could not find the 'name' column of 'mse_leads'": the real schema has
# first_name/last_name, and a unit test that asserted against a
# hand-written allowlist happily agreed with the wrong guess. Filtering
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
    # migration 059
    "contact_status", "contact_attempts", "company_tags", "stage1_reasons",
    "domain_source", "size_proxy_open_roles",
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
    Project a qualified candidate onto mse_leads' real columns.

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
        "domain_source": qualified.get("domain_source"),
        "title": qualified.get("title"),
        "first_name": first_name,
        "last_name": last_name,
        # The decision-maker step's profile URL. Previously discarded,
        # which left the manual-LinkedIn track with no way to reach the
        # person it had deliberately routed there.
        "linkedin_url": qualified.get("contact_profile_url"),
        "location": qualified.get("location"),
        "status": qualified.get("status"),
        "contact_status": qualified.get("contact_status"),
        "email": qualified.get("email"),
        "job_posting_url": qualified.get("job_posting_url"),
        "job_posting_title": qualified.get("job_posting_title"),
        "job_posting_date": qualified.get("job_posting_date"),
        "open_role_count": qualified.get("open_role_count"),
        "size_proxy_open_roles": qualified.get("size_proxy_open_roles"),
        "stack_tags": qualified.get("stack_tags") or None,
        "company_tags": qualified.get("company_tags") or None,
        "stage1_reasons": qualified.get("stage1_reasons") or None,
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
