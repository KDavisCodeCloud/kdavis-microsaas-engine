"""
agents/marketing/slug_discovery.py

Weekly ATS slug discovery: the only place Brave is spent on finding boards
(lead-source expansion, Phase 1, 2026-11-15).

WHY A SEPARATE JOB. Until now each weekly scout spent 20 Brave queries on
discovery, CD and consulting separately, with nearly the same queries (both
search "platform engineer" and "devops engineer"), at 10 results a query and
always page 1. So the same ~190 ATS URLs came back every week, of which ~57
were new on 2026-11-08 (docs/lead-sources/00-baseline.md). Known slugs cost
nothing to re-read -- they are direct ATS API calls -- so Brave should buy
NEW slugs only. This job:

  * runs once a week (Saturday, before the Sunday 13:00 UTC scouts), shared
    by both products, under its own Brave allocation
    (brave_budget.SOURCE_ALLOCATIONS["slug_discovery"]);
  * asks every supported ATS host x the brief's role terms, 20 results a
    query, and reads a different result page each week (ISO week mod
    MAX_PAGE+1), so a repeated query keeps surfacing boards it has not
    returned before;
  * writes only slugs the cache has never seen, as "known, never fetched"
    rows (discovered_from='brave_slug_discovery'), which the scouts read
    first on Sunday because the cache is ordered least-recently-fetched with
    never-fetched first.

The scouts then skip their own discovery (discovery_queries=0) whenever this
job has completed within the last 8 days.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from scrapers.ats_boards import BoardRef, board_ref_from_url

log = logging.getLogger(__name__)

SOURCE = "slug_discovery"
BOARD_TOKEN_TABLE = "mse_ats_board_tokens"
DISCOVERED_FROM = "brave_slug_discovery"

# Every ATS host the scouts can read (scrapers/ats_boards.py). Recruitee is
# subdomain-per-company, so its site: filter is the bare domain.
SLUG_DISCOVERY_HOSTS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "recruitee.com",
)
# The brief's role terms, verbatim. Broad on purpose ("cloud",
# "infrastructure"): discovery only finds boards; which ROLES count is the
# scout's role taxonomy, which reads every posting's title.
SLUG_DISCOVERY_TERMS = (
    "devops",
    "sre",
    "site reliability",
    "platform engineer",
    "infrastructure",
    "cloud",
)
RESULTS_PER_QUERY = 20   # Brave's maximum `count`
MAX_PAGE = 4             # pages 0-4 rotate weekly
DEFAULT_MAX_QUERIES = len(SLUG_DISCOVERY_HOSTS) * len(SLUG_DISCOVERY_TERMS)  # 36
# The scouts treat discovery as done for the week if this job completed
# within this window.
FRESH_FOR = timedelta(days=8)


def discovery_queries(hosts: Iterable[str] = SLUG_DISCOVERY_HOSTS,
                      terms: Iterable[str] = SLUG_DISCOVERY_TERMS) -> list[str]:
    """Term-major, so a capped run still covers every host for the first terms."""
    hosts = list(hosts)
    return [f'site:{h} "{t}"' for t in terms for h in hosts]


def page_for(today: date) -> int:
    return today.isocalendar()[1] % (MAX_PAGE + 1)


@dataclass
class SlugStats:
    queries: int = 0
    page: int = 0
    results_seen: int = 0
    ats_urls_seen: int = 0
    slugs_seen: int = 0
    slugs_new: int = 0
    slugs_written: int = 0
    by_provider_seen: dict[str, int] = field(default_factory=dict)
    by_provider_new: dict[str, int] = field(default_factory=dict)
    query_errors: int = 0
    cache_before: int = 0
    cache_after: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def known_slugs(db) -> set[str]:
    """Every cache_key in the token cache, paged past PostgREST's 1,000 rows."""
    keys: set[str] = set()
    start = 0
    while True:
        page = (db.table(BOARD_TOKEN_TABLE).select("provider,board_token")
                .range(start, start + 999).execute().data or [])
        keys.update(f"{r['provider']}:{r['board_token']}" for r in page
                    if r.get("provider") and r.get("board_token"))
        if len(page) < 1000:
            return keys
        start += 1000


def find_new_slugs(scraper, known: set[str], *, max_queries: int, page: int,
                   stats: Optional[SlugStats] = None,
                   sleep: Optional[Callable[[float], None]] = None) -> tuple[dict[str, BoardRef], SlugStats]:
    """Run the discovery queries; return {cache_key: BoardRef} for slugs not in `known`."""
    from agents.marketing.company_first_sourcing import _pace

    stats = stats or SlugStats()
    stats.page = page
    seen: dict[str, BoardRef] = {}
    for query in discovery_queries()[:max_queries]:
        if stats.queries:
            _pace(sleep)
        try:
            items = scraper._search(query, offset=page, count=RESULTS_PER_QUERY)
        except Exception as exc:
            # A failed query is still billed by Brave; count it, carry on.
            stats.queries += 1
            stats.query_errors += 1
            log.warning("[SlugDiscovery] query failed (%s): %s", query, exc)
            continue
        stats.queries += 1
        stats.results_seen += len(items)
        for item in items:
            ref = board_ref_from_url(item.get("link") or "")
            if ref is None:
                continue
            stats.ats_urls_seen += 1
            seen.setdefault(ref.cache_key, ref)
    stats.slugs_seen = len(seen)
    new = {k: r for k, r in seen.items() if k not in known}
    stats.slugs_new = len(new)
    for r in seen.values():
        stats.by_provider_seen[r.provider] = stats.by_provider_seen.get(r.provider, 0) + 1
    for r in new.values():
        stats.by_provider_new[r.provider] = stats.by_provider_new.get(r.provider, 0) + 1
    return new, stats


def write_new_slugs(db, new: dict[str, BoardRef]) -> int:
    """Insert as never-fetched rows. An insert, not an upsert: a slug that
    appeared between the read and the write keeps its own row (the unique
    violation is counted as already-known, not an error)."""
    from scrapers.ats_boards import company_name_from_token

    written = 0
    now = datetime.now(timezone.utc).isoformat()
    for ref in new.values():
        try:
            db.table(BOARD_TOKEN_TABLE).insert({
                "provider": ref.provider, "board_token": ref.token,
                "company": company_name_from_token(ref.token),
                "discovered_from": DISCOVERED_FROM, "updated_at": now,
            }).execute()
            written += 1
        except Exception as exc:
            if "23505" in str(exc) or "duplicate key" in str(exc).lower():
                continue
            log.warning("[SlugDiscovery] could not cache %s: %s", ref.cache_key, exc)
    return written


def run_slug_discovery(db, *, scraper=None, max_queries: int = DEFAULT_MAX_QUERIES,
                       today: Optional[date] = None, sleep=None) -> dict:
    """Budget check, queries, write, record usage. Raises on a refused budget
    so the scheduler marks the run failed with the reason."""
    from agents.marketing.brave_budget import check_source_budget, record_source_usage

    decision = check_source_budget(db, SOURCE, max_queries)
    if not decision.allowed:
        raise RuntimeError(f"Brave budget refused slug discovery: {decision.reason}")
    if scraper is None:
        from agents.marketing.mkt_lead_finder import _this_months_brave_query_count
        from scrapers.brave_search import BraveSearchScraper
        scraper = BraveSearchScraper(query_count=_this_months_brave_query_count(db))
    if not getattr(scraper, "api_key", None):
        raise RuntimeError("BRAVE_API_KEY is not set; slug discovery cannot search")

    known = known_slugs(db)
    stats = SlugStats(cache_before=len(known))
    try:
        new, stats = find_new_slugs(scraper, known, max_queries=max_queries,
                                    page=page_for(today or date.today()), stats=stats, sleep=sleep)
        stats.slugs_written = write_new_slugs(db, new)
    finally:
        record_source_usage(db, SOURCE, stats.queries)
    stats.cache_after = stats.cache_before + stats.slugs_written
    log.warning("[SlugDiscovery] %s", stats.as_dict())
    return {"status": "complete", "leads_written": 0, "slugs_new": stats.slugs_written,
            "brave_queries": stats.queries, "funnel_stats": stats.as_dict(),
            "budget": decision.as_dict()}


def discovery_ran_recently(db, now: Optional[datetime] = None) -> bool:
    """Has this week's slug discovery completed? The scouts skip their own
    Brave discovery when it has. Fails toward False (the old behaviour), so a
    read error never leaves a week with no discovery at all."""
    now = now or datetime.now(timezone.utc)
    try:
        rows = (db.table("mse_scheduled_job_runs").select("finished_at")
                .eq("job_key", "slug_discovery").eq("status", "complete")
                .gte("finished_at", (now - FRESH_FOR).isoformat()).limit(1).execute().data or [])
    except Exception as exc:
        log.warning("[SlugDiscovery] could not read the schedule (%s); scouts keep their own discovery", exc)
        return False
    return bool(rows)
