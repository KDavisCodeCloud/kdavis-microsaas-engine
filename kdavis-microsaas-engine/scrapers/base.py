"""
Abstract base every lead-source scraper implements. agents/marketing/
mkt_lead_finder.py orchestrates concrete scrapers (scrapers/google_search.py,
each scrapers/verticals/*.py) purely through this interface — it never
needs to know which concrete scraper produced a given RawLead.

Every concrete scraper module also exports a module-level
`scrape(location: str, filters: dict) -> list[RawLead]` free function (a
thin wrapper around its class), so callers that don't care about the class
identity can just import and call it directly — matches the exact
signature named in the task spec.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class RawLead:
    """One candidate lead before dedup/email-verification. `source` must
    match one of mse_leads.source's real CHECK values
    (supabase/migrations/20260814000024_lead_finder.sql) — 'google_search'
    or 'real_estate_db' for anything this package produces."""

    name: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    domain: Optional[str] = None
    linkedin_url: Optional[str] = None
    email: Optional[str] = None
    source: str = ""
    raw_url: Optional[str] = None
    confidence_score: float = 0.0
    location: Optional[str] = None


class BaseScraper(ABC):
    """Every scraper (Google Custom Search, each vertical's public
    database) implements this. `source_name` must be a real mse_leads.source
    CHECK value."""

    source_name: str = "base"

    @abstractmethod
    def scrape(self, location: str, filters: dict) -> list[RawLead]:
        """Returns candidate leads for one location. `filters` carries
        whatever this scraper needs from the ICP config (job_titles,
        search_templates, exclude_domains, etc.) — each concrete scraper
        reads only the keys it understands and ignores the rest, so the
        same filters dict can be passed to every scraper in one ICP
        config without per-scraper branching in the caller."""
        raise NotImplementedError
