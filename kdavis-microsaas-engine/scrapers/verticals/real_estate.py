"""
Real estate license database scraper (Source 2, Showing Signal's primary
vertical). Targets Arizona Department of Real Estate's public license
lookup (confirmed real and public 2026-08-14 at services.azre.gov/PdbWeb —
a genuine state-run license search, no login, explicit public-data
disclaimer on the landing page, no robots.txt or ToS blocking automated
access found).

CONFIDENCE — stated plainly, same convention as core/crm/kvcore.py's own
low-confidence flag elsewhere in this ecosystem: the exact search-form
field names, HTTP method, and result-page HTML structure are UNCONFIRMED
against a live session — the search sub-pages returned a generic ASP.NET
error when checked, not a real form. BASE_SEARCH_URL and the query
parameter names below are a reasonable best-guess for this class of
government license-lookup site, not verified. `_parse_results_table`
is deliberately written to detect ANY results table generically (by
matching header text, not brittle hardcoded CSS selectors) specifically
so it degrades gracefully — and is easier to fix — once someone exercises
this against the real live form and confirms the actual field/column
names.

Other verticals (contractor licenses, medical licenses, business
registries — all public record in most states) get their own file here
following the same BaseScraper interface; scrapers/verticals/__init__.py
registers each by vertical name.
"""

import os
import random
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, RawLead

# UNCONFIRMED — see module docstring. Override via env var once the real
# endpoint is confirmed, without needing a code change.
BASE_SEARCH_URL = os.environ.get(
    "AZ_RE_LICENSE_SEARCH_URL", "http://services.azre.gov/PdbWeb/SearchSalesperson.aspx"
)

MIN_DELAY_SECONDS = 2
MAX_DELAY_SECONDS = 5

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]

# Header text (lowercased, substring match) -> RawLead field. Generic on
# purpose -- real column headers on the live site are unconfirmed, so this
# matches by meaning rather than an exact guessed string.
_COLUMN_MAP = {
    "name": "name",
    "license type": "title",
    "license #": None,
    "brokerage": "company",
    "company": "company",
    "city": "location",
    "address": "location",
}


def _parse_results_table(html: str, location: str) -> list[RawLead]:
    soup = BeautifulSoup(html, "html.parser")
    table = None
    for candidate in soup.find_all("table"):
        header_text = " ".join(th.get_text(strip=True).lower() for th in candidate.find_all(["th", "td"], limit=10))
        if "name" in header_text and ("license" in header_text or "brokerage" in header_text):
            table = candidate
            break
    if table is None:
        return []

    rows = table.find_all("tr")
    if len(rows) < 2:
        return []

    headers = [cell.get_text(strip=True).lower() for cell in rows[0].find_all(["th", "td"])]
    field_positions: dict[str, int] = {}
    for i, header in enumerate(headers):
        for key, field in _COLUMN_MAP.items():
            if key in header and field:
                field_positions[field] = i
                break

    leads: list[RawLead] = []
    for row in rows[1:]:
        cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
        if not cells or not any(cells):
            continue
        record = {field: cells[pos] for field, pos in field_positions.items() if pos < len(cells)}
        if not record.get("name"):
            continue
        leads.append(RawLead(
            name=record.get("name"),
            title=record.get("title") or "Real Estate Agent",
            company=record.get("company"),
            location=record.get("location") or location,
            source="real_estate_db",
            raw_url=BASE_SEARCH_URL,
            confidence_score=0.4,  # license databases rarely list an email/LinkedIn directly
        ))
    return leads


class RealEstateScraper(BaseScraper):
    source_name = "real_estate_db"

    def __init__(self, http_get=None, base_url: Optional[str] = None):
        self._get = http_get or (lambda url, **kw: httpx.get(url, **kw))
        self.base_url = base_url or BASE_SEARCH_URL

    def scrape(self, location: str, filters: dict) -> list[RawLead]:
        license_types = filters.get("license_types") or ["Salesperson", "Broker"]
        leads: list[RawLead] = []

        for license_type in license_types:
            user_agent = random.choice(_USER_AGENTS)
            try:
                response = self._get(
                    self.base_url,
                    params={"City": location, "LicenseType": license_type},
                    timeout=15,
                    headers={"User-Agent": user_agent},
                )
                time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
                if response.status_code >= 400:
                    continue
                leads.extend(_parse_results_table(response.text, location))
            except Exception:
                continue

        return leads


def scrape(location: str, filters: dict) -> list[RawLead]:
    """Module-level convenience wrapper matching the task spec's exact
    `scrape(location, filters) -> list[RawLead]` signature."""
    return RealEstateScraper().scrape(location, filters)
