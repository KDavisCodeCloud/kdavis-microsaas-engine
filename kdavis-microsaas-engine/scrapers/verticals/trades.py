"""
Contractor license database scraper (Source 2 for the Trades vertical,
agents/trades_intel/agent.py). Targets California's Contractor State
License Board (CSLB) public license lookup (services.cslb.ca.gov) --
picked as the first state to implement following scrapers/verticals/
real_estate.py's own precedent of shipping one real, confirmed-public
state source rather than guessing at all seven states named in the
Trades agent's system prompt (TX/FL/CA/GA/NC/AZ/OH). CSLB's license
search is public record, no login, standard for contractor verification.

CONFIDENCE -- same convention as real_estate.py: the exact search-form
field names and result-page HTML structure are UNCONFIRMED against a
live session. BASE_SEARCH_URL and the query parameter names below are a
reasonable best-guess for this class of government license-lookup site,
not verified. _parse_results_table is deliberately generic (matches by
header text, not brittle hardcoded CSS selectors) so it degrades
gracefully -- and is easier to fix -- once someone exercises this against
the real live form.

Remaining six states (TX/FL/GA/NC/AZ/OH) are follow-on work, one file (or
one state-keyed branch here) per state, same pattern as this one.
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
    "CA_CSLB_LICENSE_SEARCH_URL", "https://www.cslb.ca.gov/onlineservices/checklicenseII/CheckLicense.aspx"
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
    "business name": "company",
    "contractor": "name",
    "classification": "title",
    "license #": None,
    "license number": None,
    "city": "location",
    "mailing address": "location",
}


def _parse_results_table(html: str, location: str) -> list[RawLead]:
    soup = BeautifulSoup(html, "html.parser")
    table = None
    for candidate in soup.find_all("table"):
        header_text = " ".join(th.get_text(strip=True).lower() for th in candidate.find_all(["th", "td"], limit=10))
        if "license" in header_text and ("contractor" in header_text or "business" in header_text):
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
        if not record.get("name") and not record.get("company"):
            continue
        leads.append(RawLead(
            name=record.get("name"),
            title=record.get("title") or "Licensed Contractor",
            company=record.get("company"),
            location=record.get("location") or location,
            source="trades_license_db",
            raw_url=BASE_SEARCH_URL,
            confidence_score=0.4,  # license databases rarely list an email/LinkedIn directly
        ))
    return leads


class TradesScraper(BaseScraper):
    source_name = "trades_license_db"

    def __init__(self, http_get=None, base_url: Optional[str] = None):
        self._get = http_get or (lambda url, **kw: httpx.get(url, **kw))
        self.base_url = base_url or BASE_SEARCH_URL

    def scrape(self, location: str, filters: dict) -> list[RawLead]:
        # e.g. ["C20", "C36", "C10"] for HVAC/plumbing/electrical CSLB
        # classification codes -- filters carries whatever this scraper
        # understands and ignores the rest (BaseScraper's own contract).
        license_types = filters.get("license_types") or ["C20", "C36", "C10"]
        leads: list[RawLead] = []

        for license_type in license_types:
            user_agent = random.choice(_USER_AGENTS)
            try:
                response = self._get(
                    self.base_url,
                    params={"City": location, "Classification": license_type},
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
    return TradesScraper().scrape(location, filters)
