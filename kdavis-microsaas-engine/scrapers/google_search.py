"""
Google Custom Search scraper (Source 1).

Uses Google's OFFICIAL Programmable Search Engine / Custom Search JSON API
(https://developers.google.com/custom-search/v1/overview), never
`googlesearch-python`-style scraping of Google's own search-results HTML —
that violates Google's Terms of Service even though it never touches
LinkedIn directly, in direct tension with the "no rules broken" constraint
this whole system is built under. Kelvin's explicit call (2026-08-14):
stay inside Google's ToS and hard-cap usage at the always-free 100
queries/day quota (FREE_TIER_DAILY_CAP below), so this never bills and
never risks the Railway IP getting CAPTCHA-challenged or rate-limited by
Google for automated querying.

Requires GOOGLE_CSE_API_KEY + GOOGLE_CSE_ENGINE_ID (a free Google Cloud
project + Programmable Search Engine — one-time owner setup, same
"skip gracefully without a paid/unavailable credential" shape as MKT-O1
without APOLLO_API_KEY) — both unset means this scraper returns no leads
rather than raising.

For `site:linkedin.com/in` results, only the search result's own
title/snippet metadata (already returned by the Custom Search API) is
used — this scraper never fetches a linkedin.com URL directly, per the
explicit "no LinkedIn scraping" constraint. For every other public page
(brokerage sites, directory listings), it checks robots.txt before
following the link, and only extracts what's plainly present in the
page's own visible text (an email address) — never anything behind a
login or a paywall.
"""

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

CSE_URL = "https://www.googleapis.com/customsearch/v1"

# Free tier is exactly 100 queries/day -- this is the hard stop that keeps
# ongoing cost at genuinely $0, never a soft warning.
FREE_TIER_DAILY_CAP = 100
# "10-15 Google queries per hour max" from the task spec, enforced here as
# a per-scrape()-call ceiling (mkt_lead_finder.py calls scrape() once per
# location per weekly run, not continuously, so a per-call cap is the
# meaningful unit -- true rolling-hourly enforcement across an entire run
# would need shared, wall-clock-aware state this class doesn't own).
MAX_QUERIES_PER_CALL = 15
MIN_DELAY_SECONDS = 2
MAX_DELAY_SECONDS = 5

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _robots_allowed(url: str, user_agent: str, get_fn) -> bool:
    """Fail-closed: any error fetching/parsing robots.txt means treat the
    page as disallowed rather than crawl it anyway."""
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
    # Google result titles are typically "Name - Title | Site" or similar
    # separator-delimited shapes -- the leading segment is the closest
    # thing to a plain name without guessing further.
    cleaned = title.split(" - ")[0].split(" | ")[0].strip()
    return cleaned or None


class GoogleSearchScraper(BaseScraper):
    source_name = "google_search"

    def __init__(
        self,
        api_key: Optional[str] = None,
        engine_id: Optional[str] = None,
        http_get=None,
        daily_query_count: int = 0,
    ):
        self.api_key = api_key or os.environ.get("GOOGLE_CSE_API_KEY")
        self.engine_id = engine_id or os.environ.get("GOOGLE_CSE_ENGINE_ID")
        self._get = http_get or (lambda url, **kw: httpx.get(url, **kw))
        # Caller (mkt_lead_finder.py) persists and passes this back in
        # across calls within one run so the free-tier cap is respected
        # across every location/vertical a single run touches, not just
        # within one scrape() call.
        self.daily_query_count = daily_query_count

    def _search(self, query: str) -> list[dict]:
        if self.daily_query_count >= FREE_TIER_DAILY_CAP:
            return []
        response = self._get(
            CSE_URL,
            params={"key": self.api_key, "cx": self.engine_id, "q": query, "num": 10},
            timeout=15,
        )
        self.daily_query_count += 1
        response.raise_for_status()
        return response.json().get("items", []) or []

    def scrape(self, location: str, filters: dict) -> list[RawLead]:
        if not self.api_key or not self.engine_id:
            return []  # skip gracefully -- same shape as MKT-O1 without APOLLO_API_KEY

        templates = filters.get("search_templates") or []
        titles = filters.get("job_titles") or [""]
        exclude_domains = set(filters.get("exclude_domains") or [])

        leads: list[RawLead] = []
        queries_run = 0
        for template in templates:
            for title in titles:
                if self.daily_query_count >= FREE_TIER_DAILY_CAP or queries_run >= MAX_QUERIES_PER_CALL:
                    return leads

                query = template.format(title=title, location=location)
                items = self._search(query)
                queries_run += 1
                time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))

                for item in items:
                    lead = self._lead_from_result(item, location, exclude_domains)
                    if lead:
                        leads.append(lead)

        return leads

    def _lead_from_result(self, item: dict, location: str, exclude_domains: set) -> Optional[RawLead]:
        link = item.get("link", "")
        if not link:
            return None
        domain = urlparse(link).netloc.replace("www.", "")
        if domain in exclude_domains:
            return None

        if "linkedin.com/in/" in link:
            # Never fetch li.com directly -- only the metadata Google's
            # own API already returned for this result.
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
            return None
        try:
            page = self._get(link, timeout=10, headers={"User-Agent": user_agent})
            time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
            if page.status_code >= 400:
                return None
            email = _extract_email_from_page(page.text)
        except Exception:
            return None

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
    return GoogleSearchScraper().scrape(location, filters)
