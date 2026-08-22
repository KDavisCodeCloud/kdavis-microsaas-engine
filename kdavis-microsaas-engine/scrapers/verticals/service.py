"""
Cosmetology board license database scraper (Source 2 for the Service
vertical, agents/service_intel/agent.py). Targets California's BreEZe
system (breeze.ca.gov), which the Board of Barbering and Cosmetology
uses for public license lookups -- picked as the first state to
implement following scrapers/verticals/real_estate.py's own precedent of
shipping one real, confirmed-public state source. Google Business
Profile scraping (the Service agent's second named source, for salon
emails) is a separate, generic capability already covered by
scrapers/google_search.py's Source 1 -- not duplicated here.

CONFIDENCE -- same convention as real_estate.py: the exact search-form
field names and result-page HTML structure are UNCONFIRMED against a
live session. BASE_SEARCH_URL and the query parameter names below are a
reasonable best-guess for this class of government license-lookup site,
not verified. _parse_results_table is deliberately generic (matches by
header text, not brittle hardcoded CSS selectors) so it degrades
gracefully -- and is easier to fix -- once someone exercises this against
the real live form.

Other states' cosmetology boards are follow-on work, one file (or one
state-keyed branch here) per state, same pattern as this one.
"""

import os
import random
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, RawLead

# UNCONFIRMED -- see module docstring. Override via env var once the real
# endpoint is confirmed, without needing a code change.
BASE_SEARCH_URL = os.environ.get(
    "CA_BBC_LICENSE_SEARCH_URL", "https://search.dca.ca.gov/"
)

MIN_DELAY_SECONDS = 2
MAX_DELAY_SECONDS = 5

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]

# Header text (lowercased, substring match) -> RawLead field. Generic on
# purpose -- see module docstring.
_COLUMN_MAP = {
    "establishment": "company",
    "salon": "company",
    "licensee": "name",
    "license type": "title",
    "license number": None,
    "city": "location",
    "address": "location",
}


def _parse_results_table(html: str, location: str) -> list[RawLead]:
    soup = BeautifulSoup(html, "html.parser")
    table = None
    for candidate in soup.find_all("table"):
        header_text = " ".join(th.get_text(strip=True).lower() for th in candidate.find_all(["th", "td"], limit=10))
        if "license" in header_text and ("establishment" in header_text or "salon" in header_text):
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
        if not record.get("company"):
            continue
        leads.append(RawLead(
            name=record.get("name"),
            title=record.get("title") or "Licensed Salon/Spa Establishment",
            company=record.get("company"),
            location=record.get("location") or location,
            source="service_license_db",
            raw_url=BASE_SEARCH_URL,
            confidence_score=0.4,  # license databases rarely list an email/LinkedIn directly
        ))
    return leads


class ServiceScraper(BaseScraper):
    source_name = "service_license_db"

    def __init__(self, http_get=None, base_url: Optional[str] = None):
        self._get = http_get or (lambda url, **kw: httpx.get(url, **kw))
        self.base_url = base_url or BASE_SEARCH_URL

    def scrape(self, location: str, filters: dict) -> list[RawLead]:
        # e.g. ["Establishment", "Salon"] -- filters carries whatever this
        # scraper understands and ignores the rest (BaseScraper's own
        # contract).
        license_types = filters.get("license_types") or ["Establishment"]
        leads: list[RawLead] = []

        for license_type in license_types:
            user_agent = random.choice(_USER_AGENTS)
            try:
                response = self._get(
                    self.base_url,
                    params={"City": location, "LicenseType": license_type, "Board": "Barbering and Cosmetology"},
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
    """Module-level convenience wrapper matching scrapers/base.py's
    documented `scrape(location, filters) -> list[RawLead]` signature."""
    return ServiceScraper().scrape(location, filters)
