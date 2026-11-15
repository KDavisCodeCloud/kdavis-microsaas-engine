"""
agents/marketing/wellfound_signal.py

Wellfound as a SIGNAL ONLY (lead-source expansion, Phase 4, 2026-11-15).

The brief: "Run Brave queries like `site:wellfound.com/company devops` ...
extract company names only from the result titles. Resolve each to its own
domain and ATS, then send it through the normal pipeline. Never fetch
wellfound.com pages."

So this module:
  * spends a few Brave queries (allocation brave_budget["wellfound"]);
  * reads ONLY each result's title and URL path -- both part of Brave's
    response; no request is ever made to wellfound.com (enforced in code:
    nothing here takes an http client for it, and a test asserts it);
  * turns each name into an ATS slug with form_d.resolve_company_slug -- direct
    ATS API calls, accepted only when the board names the same company;
  * queues resolved slugs in mse_ats_board_tokens (discovered_from=
    'wellfound_signal'). The Sunday scout then qualifies the company through
    every normal rule (role match, exclusions, size, domain, buyer gate).
    A name with no resolvable ATS board is dropped and counted.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

SOURCE = "wellfound"
DISCOVERED_FROM = "wellfound_signal"
WELLFOUND_QUERIES = (
    'site:wellfound.com/company devops',
    'site:wellfound.com/company "site reliability"',
    'site:wellfound.com/company "platform engineering"',
    'site:wellfound.com/jobs "platform engineer"',
    'site:wellfound.com/jobs "devops engineer"',
    'site:wellfound.com/jobs "site reliability engineer"',
    'site:wellfound.com/jobs "infrastructure engineer"',
    'site:wellfound.com/jobs "cloud engineer"',
)
RESULTS_PER_QUERY = 20

# "Senior DevOps Engineer at Acme Robotics • Remote | Wellfound"
_JOB_TITLE = re.compile(r"\bat\s+(.+?)(?:\s+[•·|–—-]\s+|\s*\(|$)", re.IGNORECASE)
# "Acme Robotics Careers & Jobs | Wellfound", "Acme Robotics - Company Profile"
_COMPANY_TITLE = re.compile(
    r"^(.+?)\s+(?:careers?\b|jobs\b|company profile|[-–—|•·]\s|hiring\b|is hiring)", re.IGNORECASE)
_NOT_A_NAME = re.compile(r"^(?:wellfound|angellist|startup|jobs|remote|the best|top)\b", re.IGNORECASE)


def company_from_result(title: str, url: str) -> Optional[str]:
    """The company a Wellfound search result is about, from its title, else
    from the /company/<slug> segment of its URL. None when neither names one."""
    t = html.unescape(title or "").strip()
    path = [p for p in (urlparse(url or "").path or "").split("/") if p]
    kind = path[0] if path else ""
    name = None
    if kind == "jobs" and len(path) >= 2:
        m = _JOB_TITLE.search(t)
        name = _clean(m.group(1)) if m else None
    elif kind == "company" and len(path) >= 2:
        m = _COMPANY_TITLE.search(t)
        name = _clean(m.group(1)) if m else None
        if not name:  # the URL slug names the company even when the title does not
            name = _clean(re.sub(r"[-_]+", " ", path[1]).strip().title())
    return name


def _clean(name: str) -> Optional[str]:
    name = re.sub(r"\s+", " ", name or "").strip(" -|•·:")
    if not name or len(name) > 60 or _NOT_A_NAME.match(name):
        return None
    return name


@dataclass
class WellfoundStats:
    queries: int = 0
    page: int = 0
    results_seen: int = 0
    names_found: int = 0
    skipped_existing: int = 0
    skipped_known_slug: int = 0
    resolved_new_slug: int = 0
    unresolved: int = 0
    queued: int = 0
    names: list[str] = field(default_factory=list)
    unresolved_names: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def run_wellfound_signal(db, *, scraper=None, ats_client=None, max_queries: int = len(WELLFOUND_QUERIES),
                         max_companies: int = 80, today: Optional[date] = None, sleep=None) -> dict:
    from agents.marketing.brave_budget import check_source_budget, record_source_usage
    from agents.marketing.company_classification import normalise_company_name
    from agents.marketing.company_first_sourcing import _pace
    from agents.marketing.form_d import norm, resolve_company_slug
    from agents.marketing.mkt_lead_finder import _existing_job_signal_companies
    from agents.marketing.slug_discovery import known_slugs
    from scrapers.ats_boards import AtsBoardClient

    decision = check_source_budget(db, SOURCE, max_queries)
    if not decision.allowed:
        raise RuntimeError(f"Brave budget refused the Wellfound signal run: {decision.reason}")
    if scraper is None:
        from agents.marketing.mkt_lead_finder import _this_months_brave_query_count
        from scrapers.brave_search import BraveSearchScraper
        scraper = BraveSearchScraper(query_count=_this_months_brave_query_count(db))
    if not getattr(scraper, "api_key", None):
        raise RuntimeError("BRAVE_API_KEY is not set; the Wellfound signal cannot search")

    stats = WellfoundStats(page=(today or date.today()).month % 5)
    names: dict[str, str] = {}
    try:
        for q in WELLFOUND_QUERIES[:max_queries]:
            if stats.queries:
                _pace(sleep)
            try:
                items = scraper._search(q, offset=stats.page, count=RESULTS_PER_QUERY)
            finally:
                stats.queries += 1
            stats.results_seen += len(items)
            for it in items:
                if "wellfound.com" not in (urlparse(it.get("link") or "").hostname or ""):
                    continue
                name = company_from_result(it.get("title") or "", it.get("link") or "")
                if name:
                    names.setdefault(norm(name), name)
    finally:
        record_source_usage(db, SOURCE, stats.queries)

    stats.names_found = len(names)
    stats.names = sorted(names.values())[:200]
    existing = _existing_job_signal_companies(db)
    known = known_slugs(db)
    client = ats_client or AtsBoardClient(sleep=sleep)
    for key, name in list(names.items())[:max_companies]:
        if key in existing or normalise_company_name(name) in existing:
            stats.skipped_existing += 1
            continue
        found = resolve_company_slug(name, client, known)
        if not found:
            stats.unresolved += 1
            stats.unresolved_names.append(name)
            continue
        if found[0] == "known":
            stats.skipped_known_slug += 1
            continue
        stats.resolved_new_slug += 1
        ref = found[1]
        db.table("mse_ats_board_tokens").upsert({
            "provider": ref.provider, "board_token": ref.token, "company": name,
            "discovered_from": DISCOVERED_FROM,
        }, on_conflict="provider,board_token").execute()
        known.add(ref.cache_key)
        stats.queued += 1
    log.warning("[Wellfound] %s", {k: v for k, v in stats.as_dict().items() if k not in ("names",)})
    return {"status": "complete", "leads_written": 0, "slugs_new": stats.queued, "brave_queries": stats.queries,
            "funnel_stats": stats.as_dict(), "budget": decision.as_dict()}
