"""
Registry mapping vertical name (ICP config's "vertical" field) to its
scraper class. agents/marketing/mkt_lead_finder.py imports the right
scraper for a product's ICP config from here rather than hardcoding an
if/elif chain — adding a new vertical (contractor licenses, medical
licenses, business registries — all public record in most states) means
adding one file here plus one new entry in this dict, nothing else in
mkt_lead_finder.py changes.
"""

from scrapers.verticals.real_estate import RealEstateScraper
from scrapers.verticals.trades import TradesScraper
from scrapers.verticals.care import CareScraper
from scrapers.verticals.service import ServiceScraper
from scrapers.verticals.field import FieldScraper

VERTICAL_SCRAPERS: dict[str, type] = {
    "real_estate": RealEstateScraper,
    "trades": TradesScraper,
    "care": CareScraper,
    "service": ServiceScraper,
    "field": FieldScraper,
}


def get_vertical_scraper(vertical: str):
    """Returns the scraper class for a vertical, or None if this vertical
    has no public-database scraper yet — never raises, since Source 2 is
    optional per-ICP (an ICP config with an unsupported vertical still
    gets leads from Source 1 + Source 3, just not Source 2)."""
    return VERTICAL_SCRAPERS.get(vertical)
