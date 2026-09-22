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


def _fake_brave_scraper(leads):
    scraper = MagicMock()
    scraper.scrape.return_value = leads
    scraper.query_count = 1
    return scraper


def test_find_leads_returns_leads_for_a_valid_icp_config():
    fake_db = FakeSupabase(responses={"mse_leads": []})
    raw = [RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="brave_search", location="Phoenix AZ")]

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None), \
         patch.object(mlf, "verify_email", return_value=None), \
         patch.object(mlf, "find_email", return_value=EmailResult(email=None, pattern_used=None, verification_status="unverified", confidence_score=0.0)):
        leads = mlf.find_leads("prod-1", VALID_ICP_CONFIG, limit=10, supabase_client=fake_db)

    assert len(leads) == 1
    assert leads[0]["name"] == "Jane Doe"
    assert leads[0]["linkedin_url"] == "https://linkedin.com/in/janedoe"
    assert leads[0]["source"] == "brave_search"


def test_find_leads_deduplicates_same_linkedin_url_against_existing_leads():
    fake_db = FakeSupabase(responses={
        "mse_leads": [{"linkedin_url": "https://linkedin.com/in/janedoe", "email": None}],
    })
    raw = [
        RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="brave_search", location="Phoenix AZ"),
        RawLead(name="John Smith", linkedin_url="https://linkedin.com/in/johnsmith", source="brave_search", location="Phoenix AZ"),
    ]

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None):
        leads = mlf.find_leads("prod-1", VALID_ICP_CONFIG, limit=10, supabase_client=fake_db)

    # Jane Doe's linkedin_url already exists in mse_leads -- dropped.
    # John Smith is new -- kept.
    assert len(leads) == 1
    assert leads[0]["name"] == "John Smith"


def test_find_leads_deduplicates_within_the_same_batch():
    fake_db = FakeSupabase(responses={"mse_leads": []})
    raw = [
        RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="brave_search", location="Phoenix AZ"),
        RawLead(name="Jane Doe Again", linkedin_url="https://linkedin.com/in/janedoe", source="real_estate_db", location="Phoenix AZ"),
    ]

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None):
        leads = mlf.find_leads("prod-1", VALID_ICP_CONFIG, limit=10, supabase_client=fake_db)

    assert len(leads) == 1


def test_find_leads_drops_a_pattern_guessed_email_that_already_exists():
    """Real bug found live 2026-09-21 on the first real Brave Search run
    to ever surface actual leads: a raw lead with NO scraped email (only
    name+domain) gets one attached by find_email()'s pattern-guessing
    inside _verify_lead_email -- AFTER _dedupe_raw_leads already ran, so
    that guess was never checked against mse_leads' real emails.
    idx_mse_leads_email's UNIQUE constraint then failed the whole bulk
    insert (not just this one row), losing every lead the run found."""
    fake_db = FakeSupabase(responses={
        "mse_leads": [{"linkedin_url": None, "email": "director@freehire.me"}],
    })
    raw = [RawLead(name="Jane Doe", domain="freehire.me", source="brave_search", location="Phoenix AZ")]

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None), \
         patch.object(mlf, "find_email", return_value=EmailResult(
             email="director@freehire.me", pattern_used="role", verification_status="unverified", confidence_score=0.2,
         )):
        leads = mlf.find_leads("prod-1", VALID_ICP_CONFIG, limit=10, supabase_client=fake_db)

    assert leads == []


def test_find_leads_drops_the_second_of_two_leads_that_resolve_to_the_same_guessed_email():
    fake_db = FakeSupabase(responses={"mse_leads": []})
    raw = [
        RawLead(name="Jane Doe", domain="freehire.me", source="brave_search", location="Phoenix AZ"),
        RawLead(name="John Smith", domain="freehire.me", source="brave_search", location="Phoenix AZ"),
    ]

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None), \
         patch.object(mlf, "find_email", return_value=EmailResult(
             email="director@freehire.me", pattern_used="role", verification_status="unverified", confidence_score=0.2,
         )):
        leads = mlf.find_leads("prod-1", VALID_ICP_CONFIG, limit=10, supabase_client=fake_db)

    assert len(leads) == 1
    assert leads[0]["name"] == "Jane Doe"


def test_find_leads_uses_vertical_scraper_when_configured():
    fake_db = FakeSupabase(responses={"mse_leads": []})
    vertical_lead = RawLead(name="Alex Broker", title="Broker", source="real_estate_db", location="Phoenix AZ")

    vertical_scraper = MagicMock()
    vertical_scraper.scrape.return_value = [vertical_lead]
    vertical_scraper_cls = MagicMock(return_value=vertical_scraper)

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper([])), \
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
    raw = [RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="brave_search", location="Phoenix AZ")]

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None):
        result = mlf.run_lead_finder_for_product("prod-1", supabase_client=fake_db)

    assert result["status"] == "complete"
    assert result["leads_found"] == 1

    inserts = [c for c in fake_db.executed if c.table_name == "mse_leads" and c.calls[0][0] == "insert"]
    assert inserts[0]._payload[0]["product_id"] == "prod-1"

    run_updates = [c for c in fake_db.executed if c.table_name == "mse_lead_finder_runs" and c.calls[0][0] == "update"]
    assert run_updates[-1]._payload["status"] == "complete"

    activity_inserts = [c for c in fake_db.executed if c.table_name == "mse_activities" and c.calls[0][0] == "insert"]
    assert activity_inserts[0]._payload[0]["subject_id"] == "lead-row-1"
    assert activity_inserts[0]._payload[0]["kind"] == "found"
    assert activity_inserts[0]._payload[0]["product_id"] == "prod-1"


def test_run_lead_finder_for_product_limit_overrides_icp_target_count():
    """Real gap fixed 2026-09-22: POST /marketing/leads/find accepted a
    `limit` field but it never reached this function, so every run always
    used the ICP config's full target_count (10 here) regardless of what
    a caller asked for. VALID_ICP_CONFIG's target_count is 10; two raw
    leads are returned, so without the override both would survive --
    passing limit=1 must truncate to exactly one."""
    fake_db = FakeSupabase(responses={
        "mse_icp_configs": [{"product_id": "prod-1", **VALID_ICP_CONFIG}],
        "mse_lead_finder_runs": [{"id": "run-1"}],
        "mse_leads": [{"id": "lead-row-1"}],
        "usage_events": [],
    })
    raw = [
        RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="brave_search", location="Phoenix AZ"),
        RawLead(name="John Smith", linkedin_url="https://linkedin.com/in/johnsmith", source="brave_search", location="Phoenix AZ"),
    ]

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None):
        result = mlf.run_lead_finder_for_product("prod-1", supabase_client=fake_db, limit=1)

    assert result["leads_found"] == 1


def test_run_lead_finder_for_product_uses_target_count_when_no_limit_given():
    """Companion to the override test above -- confirms the default path
    (no limit passed, same as the weekly n8n cron's call shape) still
    uses the ICP config's target_count exactly as before this change."""
    fake_db = FakeSupabase(responses={
        "mse_icp_configs": [{"product_id": "prod-1", **VALID_ICP_CONFIG}],
        "mse_lead_finder_runs": [{"id": "run-1"}],
        "mse_leads": [{"id": "lead-row-1"}],
        "usage_events": [],
    })
    raw = [
        RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="brave_search", location="Phoenix AZ"),
        RawLead(name="John Smith", linkedin_url="https://linkedin.com/in/johnsmith", source="brave_search", location="Phoenix AZ"),
    ]

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None):
        result = mlf.run_lead_finder_for_product("prod-1", supabase_client=fake_db)

    assert result["leads_found"] == 2


def test_find_leads_reports_progress_for_search_and_verify_phases():
    """2026-09-17: Kelvin reported no status bar for a running lead-finder
    job -- on_progress is the mechanism that fixes it. One call per
    location scraped, one call per deduplicated lead verified, plus a
    final 'Finalizing results' call at 100%."""
    fake_db = FakeSupabase(responses={"mse_leads": []})
    icp_two_locations = {**VALID_ICP_CONFIG, "locations": ["Phoenix AZ", "Tucson AZ"]}
    raw = [
        RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="brave_search", location="Phoenix AZ"),
        RawLead(name="John Smith", linkedin_url="https://linkedin.com/in/johnsmith", source="brave_search", location="Tucson AZ"),
    ]
    calls = []

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None):
        mlf.find_leads("prod-1", icp_two_locations, limit=10, supabase_client=fake_db, on_progress=lambda *a: calls.append(a))

    # Search phase: one call per location, completed 0..locations-1, total pinned at 2 (locations_count)
    search_calls = [c for c in calls if "Searching" in c[0]]
    assert len(search_calls) == 2
    assert search_calls[0] == (search_calls[0][0], 0, 2)
    assert search_calls[1] == (search_calls[1][0], 1, 2)
    assert "Phoenix AZ" in search_calls[0][0]

    # Verify phase: one call per deduplicated lead (2 raw leads, no existing dupes -> 2),
    # completed continues counting up from locations_count (2), total becomes 2 + 2 = 4
    verify_calls = [c for c in calls if "Verifying" in c[0]]
    assert len(verify_calls) == 2
    assert verify_calls[0] == (verify_calls[0][0], 2, 4)
    assert verify_calls[1] == (verify_calls[1][0], 3, 4)

    # Final call signals 100% complete
    assert calls[-1] == ("Finalizing results", 4, 4)


def test_run_lead_finder_for_product_writes_progress_columns_and_clears_them_on_completion():
    fake_db = FakeSupabase(responses={
        "mse_icp_configs": [{"product_id": "prod-1", **VALID_ICP_CONFIG}],
        "mse_lead_finder_runs": [{"id": "run-1"}],
        "mse_leads": [{"id": "lead-row-1"}],
        "usage_events": [],
    })
    raw = [RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="brave_search", location="Phoenix AZ")]

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None):
        mlf.run_lead_finder_for_product("prod-1", supabase_client=fake_db)

    run_updates = [c for c in fake_db.executed if c.table_name == "mse_lead_finder_runs" and c.calls[0][0] == "update"]

    # At least one progress write happened with real step/total data before completion
    progress_updates = [c for c in run_updates if "current_step" in c._payload and c._payload.get("current_step")]
    assert progress_updates, "expected at least one live progress write during the run"
    assert progress_updates[0]._payload["total_steps"] is not None
    assert "estimated_seconds_remaining" in progress_updates[0]._payload

    # Final update clears the in-progress fields so the UI doesn't show a stale step
    final_update = run_updates[-1]._payload
    assert final_update["status"] == "complete"
    assert final_update["current_step"] is None
    assert final_update["estimated_seconds_remaining"] == 0


def test_run_lead_finder_for_product_tolerates_a_progress_write_failure():
    """A transient DB hiccup on a progress write must never abort a
    real, otherwise-successful lead-finding run -- same discipline as the
    existing activity-logging-failure tolerance test below."""
    class BrokenProgressDB(FakeSupabase):
        def table(self, name):
            if name == "mse_lead_finder_runs":
                real = super().table(name)
                orig_update = real.update

                def flaky_update(payload):
                    if payload.get("current_step") == "Starting search…":
                        raise Exception("simulated transient write failure")
                    return orig_update(payload)

                real.update = flaky_update
                return real
            return super().table(name)

    fake_db = BrokenProgressDB(responses={
        "mse_icp_configs": [{"product_id": "prod-1", **VALID_ICP_CONFIG}],
        "mse_lead_finder_runs": [{"id": "run-1"}],
        "mse_leads": [{"id": "lead-row-1"}],
        "usage_events": [],
    })
    raw = [RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="brave_search", location="Phoenix AZ")]

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None):
        result = mlf.run_lead_finder_for_product("prod-1", supabase_client=fake_db)

    assert result["status"] == "complete"


def test_run_lead_finder_for_product_tolerates_activity_logging_failure():
    """A real lead-finder run that already succeeded at finding and saving
    leads must not fail just because best-effort mse_activities logging
    errors -- that would turn a working run into a false 'failed' status
    over a non-critical audit-trail write."""
    class BrokenActivitiesDB(FakeSupabase):
        def table(self, name):
            if name == "mse_activities":
                raise Exception("simulated mse_activities outage")
            return super().table(name)

    fake_db = BrokenActivitiesDB(responses={
        "mse_icp_configs": [{"product_id": "prod-1", **VALID_ICP_CONFIG}],
        "mse_lead_finder_runs": [{"id": "run-1"}],
        "mse_leads": [{"id": "lead-row-1"}],
        "usage_events": [],
    })
    raw = [RawLead(name="Jane Doe", linkedin_url="https://linkedin.com/in/janedoe", source="brave_search", location="Phoenix AZ")]

    with patch.object(mlf, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(mlf, "get_vertical_scraper", return_value=None):
        result = mlf.run_lead_finder_for_product("prod-1", supabase_client=fake_db)

    assert result["status"] == "complete"
