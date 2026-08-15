"""
agents/marketing/mkt_lead_finder.py — find_leads / run_lead_finder_for_product
orchestration and dedup. Scrapers and core/email_finder.py are mocked;
this covers the orchestration logic, not real scraping/SMTP behavior.
"""
from unittest.mock import MagicMock, patch

import agents.marketing.mkt_lead_finder as mlf
from core.email_finder import EmailResult
from scrapers.base import RawLead
from tests.conftest import FakeSupabase

VALID_ICP_CONFIG = {
    "job_titles": ["buyer's agent"],
    "locations": ["Phoenix AZ"],
    "vertical": "real_estate",
    "search_templates": ['"{title}" "{location}"'],
    "exclude_domains": [],
    "target_count": 10,
}


def _fake_google_scraper(leads):
    scraper = MagicMock()
    scraper.scrape.return_value = leads
    scraper.daily_query_count = 1
    return scraper


def test_find_leads_returns_leads_for_a_valid_icp_config():
    fake_db = FakeSupabase(responses={"mse_leads": []})
    raw = [RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="google_search", location="Phoenix AZ")]

    with patch.object(mlf, "GoogleSearchScraper", return_value=_fake_google_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None), \
         patch.object(mlf, "verify_email", return_value=None), \
         patch.object(mlf, "find_email", return_value=EmailResult(email=None, pattern_used=None, verification_status="unverified", confidence_score=0.0)):
        leads = mlf.find_leads("prod-1", VALID_ICP_CONFIG, limit=10, supabase_client=fake_db)

    assert len(leads) == 1
    assert leads[0]["name"] == "Jane Doe"
    assert leads[0]["linkedin_url"] == "https://linkedin.com/in/janedoe"
    assert leads[0]["source"] == "google_search"


def test_find_leads_deduplicates_same_linkedin_url_against_existing_leads():
    fake_db = FakeSupabase(responses={
        "mse_leads": [{"linkedin_url": "https://linkedin.com/in/janedoe", "email": None}],
    })
    raw = [
        RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="google_search", location="Phoenix AZ"),
        RawLead(name="John Smith", linkedin_url="https://linkedin.com/in/johnsmith", source="google_search", location="Phoenix AZ"),
    ]

    with patch.object(mlf, "GoogleSearchScraper", return_value=_fake_google_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None):
        leads = mlf.find_leads("prod-1", VALID_ICP_CONFIG, limit=10, supabase_client=fake_db)

    # Jane Doe's linkedin_url already exists in mse_leads -- dropped.
    # John Smith is new -- kept.
    assert len(leads) == 1
    assert leads[0]["name"] == "John Smith"


def test_find_leads_deduplicates_within_the_same_batch():
    fake_db = FakeSupabase(responses={"mse_leads": []})
    raw = [
        RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="google_search", location="Phoenix AZ"),
        RawLead(name="Jane Doe Again", linkedin_url="https://linkedin.com/in/janedoe", source="real_estate_db", location="Phoenix AZ"),
    ]

    with patch.object(mlf, "GoogleSearchScraper", return_value=_fake_google_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None):
        leads = mlf.find_leads("prod-1", VALID_ICP_CONFIG, limit=10, supabase_client=fake_db)

    assert len(leads) == 1


def test_find_leads_uses_vertical_scraper_when_configured():
    fake_db = FakeSupabase(responses={"mse_leads": []})
    vertical_lead = RawLead(name="Alex Broker", title="Broker", source="real_estate_db", location="Phoenix AZ")

    vertical_scraper = MagicMock()
    vertical_scraper.scrape.return_value = [vertical_lead]
    vertical_scraper_cls = MagicMock(return_value=vertical_scraper)

    with patch.object(mlf, "GoogleSearchScraper", return_value=_fake_google_scraper([])), \
         patch.object(mlf, "get_vertical_scraper", return_value=vertical_scraper_cls):
        leads = mlf.find_leads("prod-1", VALID_ICP_CONFIG, limit=10, supabase_client=fake_db)

    assert len(leads) == 1
    assert leads[0]["source"] == "real_estate_db"


def test_run_lead_finder_for_product_raises_without_icp_config():
    fake_db = FakeSupabase(responses={"mse_icp_configs": []})
    try:
        mlf.run_lead_finder_for_product("prod-missing-icp", supabase_client=fake_db)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "ICP config" in str(exc)


def test_run_lead_finder_for_product_writes_leads_and_completes_run():
    fake_db = FakeSupabase(responses={
        "mse_icp_configs": [{"product_id": "prod-1", **VALID_ICP_CONFIG}],
        "mse_lead_finder_runs": [{"id": "run-1"}],
        "mse_leads": [{"id": "lead-row-1"}],
        "usage_events": [],
    })
    raw = [RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="google_search", location="Phoenix AZ")]

    with patch.object(mlf, "GoogleSearchScraper", return_value=_fake_google_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None):
        result = mlf.run_lead_finder_for_product("prod-1", supabase_client=fake_db)

    assert result["status"] == "complete"
    assert result["leads_found"] == 1

    inserts = [c for c in fake_db.executed if c.table_name == "mse_leads" and c.calls[0][0] == "insert"]
    assert inserts[0]._payload[0]["product_id"] == "prod-1"

    run_updates = [c for c in fake_db.executed if c.table_name == "mse_lead_finder_runs" and c.calls[0][0] == "update"]
    assert run_updates[-1]._payload["status"] == "complete"
