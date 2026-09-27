"""
Brave Search API scraper (Source 1 replacement, 2026-09-18).

Replaces scrapers/google_search.py as the primary open-web lead source.
Google discontinued "Search the entire web" for newly-created Programmable
Search Engines as of 2026-01-20 (existing engines keep it only until
2027-01-01) — new engines are capped at up to 50 specific domains, which
cannot do what this scraper's queries need (arbitrary company/brokerage/
directory sites, not a fixed domain list). See
knowledge/sops/devops/2026-09-18-brave-search-replaces-google-cse.md
(kdavis-agentic-platform) for the full incident/decision record.

Uses Brave's official Web Search API (https://api.search.brave.com/res/v1/web/search),
same "official API only, never scrape search-results HTML" discipline as
the Google CSE scraper it replaces — this is a paid-API swap, not a ToS
workaround.

Real, stated cost tradeoff (different from Google CSE's genuinely-free
100/day): as of Feb 2026 Brave retired its free monthly tier for good —
every plan is metered pay-as-you-go, ~$5 of free credit per month
(roughly 1,000 queries at Brave's own ~$5/1,000 base rate), then billed
to the card on file. FREE_TIER_MONTHLY_CAP below is a hard stop set
below that credit (900, not 1,000) to leave margin against exact pricing
uncertainty — this scraper will NEVER silently run past it into paid
usage; it skips gracefully (returns no results) instead, same shape as
every other quota-gated path in this codebase. Real spend still requires
someone to raise this cap deliberately.

Requires BRAVE_API_KEY. Unset means this scraper returns no leads rather
than raising -- same graceful-skip contract Google CSE had.
"""

import logging
import os
import random
import re
import time
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, RawLead

log = logging.getLogger(__name__)

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

# Brave retired the free tier in Feb 2026 -- see module docstring. Kept
# well under the ~1,000-query/~$5 monthly credit on purpose.
FREE_TIER_MONTHLY_CAP = 900
# Lead yield tuning (2026-09-27): was MAX_QUERIES_PER_CALL = 15, reset
# per LOCATION (scrape()'s own local `queries_run` counter) -- with 2
# locations that silently truncated every find_leads() run to ~30 Brave
# queries max regardless of how many title/template variants an ICP
# config carried, long before Brave's real 900/month free-credit cap
# was anywhere close. Renamed and now enforced across the WHOLE run
# (this BraveSearchScraper instance persists across every location in a
# single find_leads() call -- see self.queries_this_run below), per
# Kelvin's explicit budget: up to 250-300 Brave queries per run, well
# under the ~2,000/month headroom he confirmed.
MAX_QUERIES_PER_RUN = 300
# Brave's published free/base-tier rate limit is ~1 query/second (their
# production capacity is 50 QPS, but that's a paid-tier number, not what
# this key should assume) -- MIN/MAX_DELAY_SECONDS keep this comfortably
# under 1 QPS per call, same spirit as the Google CSE scraper's own
# self-imposed throttling.
MIN_DELAY_SECONDS = 1.2
MAX_DELAY_SECONDS = 2.5

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _robots_allowed(url: str, user_agent: str, get_fn) -> bool:
    """Fail-closed: any error fetching/parsing robots.txt means treat the
    page as disallowed rather than crawl it anyway. Identical logic to
    scrapers/google_search.py's own helper -- duplicated rather than
    imported since that module is now dormant (see this file's docstring)
    and not worth coupling an active scraper to."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = get_fn(robots_url, timeout=10, headers={"User-Agent": user_agent})
        if response.status_code >= 400:
            return True  # no robots.txt at all -- allowed by default
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser.can_fetch(user_agent, url)
    except Exception:
        return False


def _extract_email_from_page(html: str) -> Optional[str]:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    match = _EMAIL_RE.search(text)
    return match.group(0) if match else None


def _name_from_result_title(title: str) -> Optional[str]:
    cleaned = title.split(" - ")[0].split(" | ")[0].strip()
    return cleaned or None


def _brave_headers(api_key: str) -> dict:
    return {"Accept": "application/json", "X-Subscription-Token": api_key}


class BraveSearchScraper(BaseScraper):
    source_name = "brave_search"

    def __init__(
        self,
        api_key: Optional[str] = None,
        http_get=None,
        query_count: int = 0,
    ):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY")
        self._get = http_get or (lambda url, **kw: httpx.get(url, **kw))
        # Caller (mkt_lead_finder.py) persists and passes this back in
        # across calls within the same billing month so the free-credit
        # cap is enforced across an entire month's calls, not just one.
        self.query_count = query_count
        # Distinct from self.query_count (cross-call, monthly-cumulative,
        # passed in/read back out by the caller) -- this instance-local
        # counter tracks queries fired by THIS scrape() sequence only
        # (one BraveSearchScraper instance = one find_leads() run, reused
        # across every location), so MAX_QUERIES_PER_RUN caps the actual
        # per-run budget rather than resetting per location.
        self.queries_this_run = 0

        # Lead yield tuning (2026-09-27): find_leads' own "raw_found"
        # bookkeeping only ever counted len(raw_leads) -- i.e. survivors
        # of _lead_from_result's three silent drop points below. There
        # was no way to tell, after a run collapsed, whether the loss was
        # "Brave just didn't return many results" or "results came back
        # but got dropped by domain-exclude/robots.txt/a dead link" --
        # accumulated across every scrape() call on this instance (one
        # instance is reused across every location in a single
        # find_leads() run) so the caller can read it once at the end.
        self.stats = {
            "raw_results_returned": 0,
            "dropped_no_link": 0,
            "dropped_excluded_domain": 0,
            "dropped_robots_disallowed": 0,
            "dropped_fetch_failed": 0,
            "passed_scrape_filters": 0,
        }

    def _search(self, query: str) -> list[dict]:
        """Returns items normalized to the same {"link", "title", "snippet"}
        shape scrapers/google_search.py's CSE results had, so
        mkt_lead_finder.py's find_job_posting_signals (which reads those
        keys directly, not through RawLead) needs no changes beyond which
        scraper it constructs."""
        if self.query_count >= FREE_TIER_MONTHLY_CAP:
            return []
        response = self._get(
            BRAVE_SEARCH_URL,
            params={"q": query, "count": 10},
            headers=_brave_headers(self.api_key),
            timeout=15,
        )
        self.query_count += 1
        self.queries_this_run += 1
        response.raise_for_status()
        results = (response.json().get("web", {}) or {}).get("results", []) or []
        self.stats["raw_results_returned"] += len(results)
        return [
            {"link": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("description", "")}
            for r in results
        ]

    def scrape(self, location: str, filters: dict) -> list[RawLead]:
        if not self.api_key:
            return []  # skip gracefully -- same shape as MKT-O1 without APOLLO_API_KEY

        templates = filters.get("search_templates") or []
        titles = filters.get("job_titles") or [""]
        exclude_domains = set(filters.get("exclude_domains") or [])

        leads: list[RawLead] = []
        for template in templates:
            for title in titles:
                if self.query_count >= FREE_TIER_MONTHLY_CAP or self.queries_this_run >= MAX_QUERIES_PER_RUN:
                    return leads

                query = template.format(title=title, location=location)
                items = self._search(query)
                time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))

                for item in items:
                    lead = self._lead_from_result(item, location, exclude_domains)
                    if lead:
                        leads.append(lead)

        return leads

    def _lead_from_result(self, item: dict, location: str, exclude_domains: set) -> Optional[RawLead]:
        link = item.get("link", "")
        if not link:
            self.stats["dropped_no_link"] += 1
            return None
        domain = urlparse(link).netloc.replace("www.", "")
        if domain in exclude_domains:
            self.stats["dropped_excluded_domain"] += 1
            return None

        if "linkedin.com/in/" in link:
            # Never fetch li.com directly -- only the metadata Brave's own
            # API already returned for this result.
            self.stats["passed_scrape_filters"] += 1
            return RawLead(
                name=_name_from_result_title(item.get("title", "")),
                linkedin_url=link,
                source=self.source_name,
                raw_url=link,
                location=location,
                confidence_score=0.5,
            )

        user_agent = random.choice(_USER_AGENTS)
        if not _robots_allowed(link, user_agent, self._get):
            self.stats["dropped_robots_disallowed"] += 1
            return None
        try:
            page = self._get(link, timeout=10, headers={"User-Agent": user_agent})
            time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
            if page.status_code >= 400:
                self.stats["dropped_fetch_failed"] += 1
                return None
            email = _extract_email_from_page(page.text)
        except Exception:
            self.stats["dropped_fetch_failed"] += 1
            return None

        self.stats["passed_scrape_filters"] += 1
        return RawLead(
            name=_name_from_result_title(item.get("title", "")),
            company=domain,
            domain=domain,
            email=email,
            source=self.source_name,
            raw_url=link,
            location=location,
            confidence_score=0.6 if email else 0.3,
        )


def scrape(location: str, filters: dict) -> list[RawLead]:
    """Module-level convenience wrapper matching the task spec's exact
    `scrape(location, filters) -> list[RawLead]` signature — builds a
    scraper from environment config on each call."""
    return BraveSearchScraper().scrape(location, filters)
