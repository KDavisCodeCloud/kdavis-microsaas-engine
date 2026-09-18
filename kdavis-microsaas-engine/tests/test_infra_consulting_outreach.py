"""
Coverage for the infra-consulting ICP added 2026-09-16
(mse_products.slug='thdagentic-consulting'): the job-posting-signal
scraper (agents/marketing/mkt_lead_finder.py's find_job_posting_signals /
run_job_posting_signal_finder) and the 3-touch consulting DM sequence
(agents/marketing/mkt_o2_cold_dm_writer.py's
_write_infra_consulting_dm_for_lead / the job_posting_signal branch of
run_o2_cold_dm_writer and run_o2_for_linkedin_leads).

Explicitly does NOT touch or exercise thd_lead_scout.py's own SMB
security-hygiene scoring/scraping — that pipeline is untouched by this
work, per Kelvin's own instruction.
"""
import json
from datetime import date, timedelta

import agents.marketing.mkt_lead_finder as lead_finder_module
import agents.marketing.mkt_o2_cold_dm_writer as mkt_o2_module
from agents.marketing.mkt_lead_finder import find_job_posting_signals, run_job_posting_signal_finder
from agents.marketing.mkt_o2_cold_dm_writer import run_o2_cold_dm_writer, run_o2_for_linkedin_leads
from tests.conftest import FakeSupabase

INFRA_ICP_CONFIG = {
    "job_titles": ["CTO"],
    "locations": ["United States"],
    "search_templates": ['"{title}" hiring "cloud architect" {location}'],
    "max_company_size": 200,
}


class _FakeBraveSearchScraper:
    """Stands in for scrapers.brave_search.BraveSearchScraper -- only
    the surface find_job_posting_signals actually touches (api_key
    presence, .query_count, ._search(query))."""

    def __init__(self, items_by_query=None, query_count=0):
        self.api_key = "fake-key"
        self.query_count = query_count
        self._items_by_query = items_by_query or {}
        self.queries = []

    def _search(self, query):
        self.queries.append(query)
        self.query_count += 1
        return self._items_by_query.get(query, [])


def _search_item(link, title="Acme Corp - Careers", snippet="", pagemap=None):
    item = {"link": link, "title": title, "snippet": snippet}
    if pagemap:
        item["pagemap"] = pagemap
    return item


# ── find_job_posting_signals ──────────────────────────────────────────────

def test_no_api_key_returns_empty_gracefully(monkeypatch):
    class _NoKeyScraper(_FakeBraveSearchScraper):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.api_key = None

    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", _NoKeyScraper)
    signals = find_job_posting_signals("prod-1", INFRA_ICP_CONFIG)
    assert signals == []


def test_relative_days_ago_within_cutoff_is_kept(monkeypatch):
    query = '"CTO" hiring "cloud architect" United States'
    item = _search_item("https://boards.greenhouse.io/acme/jobs/123", snippet="Posted 5 days ago")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)

    signals = find_job_posting_signals("prod-1", INFRA_ICP_CONFIG)
    assert len(signals) == 1
    assert signals[0]["source"] == "job_posting_signal"
    assert signals[0]["job_posting_url"] == item["link"]
    assert signals[0]["job_posting_date"] == (date.today() - timedelta(days=5)).isoformat()
    assert signals[0]["product_id"] == "prod-1"


def test_no_ascertainable_date_is_dropped_not_assumed_recent(monkeypatch):
    query = '"CTO" hiring "cloud architect" United States'
    item = _search_item("https://example.com/jobs/1", snippet="Great opportunity, apply now")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)

    signals = find_job_posting_signals("prod-1", INFRA_ICP_CONFIG)
    assert signals == []


def test_stale_posting_beyond_max_age_is_dropped(monkeypatch):
    query = '"CTO" hiring "cloud architect" United States'
    item = _search_item("https://example.com/jobs/1", snippet="Posted 90 days ago")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)

    signals = find_job_posting_signals("prod-1", INFRA_ICP_CONFIG, max_age_days=30)
    assert signals == []


def test_employee_count_over_max_is_excluded_when_detected(monkeypatch):
    query = '"CTO" hiring "cloud architect" United States'
    item = _search_item("https://example.com/jobs/1", snippet="Posted 2 days ago. 800-1000 employees.")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)

    signals = find_job_posting_signals("prod-1", INFRA_ICP_CONFIG)
    assert signals == []


def test_undetectable_employee_count_is_not_treated_as_disqualifying(monkeypatch):
    # Spec's own wording: "company size under 200 if detectable" -- an
    # undetected size must never silently exclude an otherwise-real candidate.
    query = '"CTO" hiring "cloud architect" United States'
    item = _search_item("https://example.com/jobs/1", snippet="Posted 2 days ago.")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)

    signals = find_job_posting_signals("prod-1", INFRA_ICP_CONFIG)
    assert len(signals) == 1


def test_duplicate_urls_within_run_are_deduped(monkeypatch):
    query = '"CTO" hiring "cloud architect" United States'
    item = _search_item("https://example.com/jobs/1", snippet="Posted 1 day ago")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item, item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)

    signals = find_job_posting_signals("prod-1", INFRA_ICP_CONFIG)
    assert len(signals) == 1


# ── run_job_posting_signal_finder ─────────────────────────────────────────

def test_run_job_posting_signal_finder_writes_to_mse_leads(monkeypatch):
    query = '"CTO" hiring "cloud architect" United States'
    item = _search_item("https://boards.greenhouse.io/acme/jobs/1", title="Acme Corp - Careers", snippet="Posted 1 day ago")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)

    db = FakeSupabase(responses={
        "mse_icp_configs": [{"product_id": "prod-1", **INFRA_ICP_CONFIG}],
        "mse_leads": [{"id": "lead-1"}],
        "usage_events": [],
    })

    result = run_job_posting_signal_finder("prod-1", supabase_client=db)

    assert result["leads_written"] == 1
    insert_calls = [q for q in db.executed if q.table_name == "mse_leads" and q.calls[0][0] == "insert"]
    assert len(insert_calls) == 1
    inserted_row = insert_calls[0]._payload[0]
    assert inserted_row["source"] == "job_posting_signal"
    assert inserted_row["job_posting_url"] == item["link"]


def test_run_job_posting_signal_finder_raises_without_icp_config():
    db = FakeSupabase(responses={"mse_icp_configs": []})
    try:
        run_job_posting_signal_finder("prod-no-config", supabase_client=db)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "no ICP config" in str(exc)


# ── MKT-O2 infra-consulting 3-touch sequence ──────────────────────────────

INFRA_SEQUENCE_JSON = json.dumps({
    "touch_1": "Saw Acme Corp is hiring a cloud architect — I help startups like yours with exactly that.",
    "touch_2": "Noticed the cloud architect req — that's usually a scaling-pains signal. I've built production "
               "infra in aerospace and regulated environments (Boeing, Honeywell Aerospace) and currently at "
               "CorVel. Worth a 20-minute call?",
    "touch_3": "Still worth a quick call about that infra scaling gap?",
})


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        return type("Msg", (), {"content": [type("Block", (), {"text": self._responses.pop(0)})()]})()


class _FakeAnthropic:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def _job_posting_lead():
    return {
        "id": "lead-1", "company": "Acme Corp", "title": None,
        "job_posting_title": "cloud architect", "job_posting_url": "https://boards.greenhouse.io/acme/jobs/1",
    }


def test_job_posting_signal_writes_three_touches():
    db = FakeSupabase(responses={
        "mse_icp_configs": [{"selling_stage": "active"}],
        "mse_dm_sequences": [{"id": "seq-1"}],
    })
    anthropic = _FakeAnthropic([INFRA_SEQUENCE_JSON])

    result = run_o2_cold_dm_writer(
        product_id="prod-1", research_report={}, leads=[_job_posting_lead()],
        lead_source="job_posting_signal", supabase_client=db, anthropic_client=anthropic,
    )

    assert result["sequences_written"] == 1
    insert_calls = [q for q in db.executed if q.table_name == "mse_dm_sequences" and q.calls[0][0] == "insert"]
    row = insert_calls[0]._payload[0]
    assert row["touch_3"] == "Still worth a quick call about that infra scaling gap?"
    assert row["lead_finder_lead_id"] == "lead-1"
    assert "lead_id" not in row and "linkedin_lead_id" not in row


def test_job_posting_signal_advances_lead_status_to_pending_email():
    db = FakeSupabase(responses={
        "mse_icp_configs": [{"selling_stage": "active"}],
        "mse_dm_sequences": [{"id": "seq-1"}],
    })
    anthropic = _FakeAnthropic([INFRA_SEQUENCE_JSON])

    run_o2_cold_dm_writer(
        product_id="prod-1", research_report={}, leads=[_job_posting_lead()],
        lead_source="job_posting_signal", supabase_client=db, anthropic_client=anthropic,
    )

    update_calls = [q for q in db.executed if q.table_name == "mse_leads" and q.calls[0][0] == "update"]
    assert len(update_calls) == 1
    assert update_calls[0]._payload == {"status": "pending_email"}


def test_job_posting_signal_missing_touch_3_raises():
    db = FakeSupabase(responses={"mse_icp_configs": [{"selling_stage": "active"}]})
    bad_json = json.dumps({"touch_1": "a", "touch_2": "b"})  # no touch_3
    anthropic = _FakeAnthropic([bad_json])

    try:
        run_o2_cold_dm_writer(
            product_id="prod-1", research_report={}, leads=[_job_posting_lead()],
            lead_source="job_posting_signal", supabase_client=db, anthropic_client=anthropic,
        )
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "touch_1, touch_2, touch_3" in str(exc)


def test_stage_gated_product_writes_nothing():
    db = FakeSupabase(responses={"mse_icp_configs": [{"selling_stage": "building"}]})
    result = run_o2_cold_dm_writer(
        product_id="prod-1", research_report={}, leads=[_job_posting_lead()],
        lead_source="job_posting_signal", supabase_client=db, anthropic_client=_FakeAnthropic([]),
    )
    assert result == {"status": "stage_gated", "sequences_written": 0}


def test_run_o2_for_linkedin_leads_includes_job_posting_signal_source(monkeypatch):
    # Same tests/conftest.py FakeSupabase limitation test_mkt_o2_lead_source.py's
    # own tests already document: .eq() filters aren't actually enforced, so
    # seeding "mse_leads" makes BOTH the lead_finder-scoped and
    # job_posting_signal-scoped queries inside run_o2_for_linkedin_leads see
    # the same row. Mocking run_o2_cold_dm_writer and asserting on call order
    # (same pattern as test_run_o2_for_linkedin_leads_processes_engagers_before_manual)
    # proves the thing that actually matters: a job_posting_signal query now
    # exists and reaches the writer with the right lead_source, independent
    # of the fake's inability to enforce the DB-level filter.
    db = FakeSupabase(responses={
        "mse_linkedin_leads": [],
        "mse_leads": [_job_posting_lead()],
    })
    calls: list[str] = []

    def fake_writer(*, product_id, research_report, leads, campaign_build_id, lead_source, supabase_client, anthropic_client=None):
        calls.append(lead_source)
        return {"sequences_written": len(leads)}

    monkeypatch.setattr(mkt_o2_module, "run_o2_cold_dm_writer", fake_writer)

    result = run_o2_for_linkedin_leads(product_id="prod-1", research_report={}, supabase_client=db)

    assert "job_posting_signal" in calls
    assert result["by_source"]["job_posting_signal"] == 1

    job_posting_select = [
        c for c in db.executed if c.table_name == "mse_leads" and c.calls[0][0] == "select" and ("source", "job_posting_signal") in c._filters
    ]
    assert len(job_posting_select) == 1
