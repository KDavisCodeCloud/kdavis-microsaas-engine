"""
Coverage for the Cloud Decoded job-signal branch added 2026-09-25,
alongside the existing infra-consulting one (tests/test_infra_consulting_outreach.py,
left untouched by this file and by the production code it covers):

  agents/marketing/mkt_lead_finder.py:
    - _route_job_posting_title (title-based routing: architect/design/
      contract/consultant -> consulting; ongoing-ops -> cloud_decoded;
      ambiguous/both/neither -> consulting)
    - _extract_stack_keywords
    - find_cloud_decoded_job_signals (search + date-confirm-or-drop +
      20-500 company-size filter + JD capture)
    - run_combined_job_signal_scout (runs both branches, routes every
      candidate by title regardless of which search found it, enforces
      company-level dedup across BOTH destinations, writes to mse_leads)

  agents/marketing/mkt_o2_cold_dm_writer.py:
    - _write_cloud_decoded_job_signal_dm_for_lead (2-touch email, force-
      multiplication framing)
    - the cloud_decoded_job_signal branch of run_o2_cold_dm_writer /
      run_o2_for_linkedin_leads

Explicitly does NOT touch or exercise find_job_posting_signals /
run_job_posting_signal_finder / _write_infra_consulting_dm_for_lead --
those are reused as pure building blocks, never modified, per Kelvin's
own "do not touch the consulting branch's queries or sequence"
instruction.
"""
import json
from datetime import date, timedelta

import agents.marketing.mkt_lead_finder as lead_finder_module
import agents.marketing.mkt_o2_cold_dm_writer as mkt_o2_module
from agents.marketing.mkt_lead_finder import (
    CLOUD_DECODED_PRODUCT_ID,
    THDAGENTIC_CONSULTING_PRODUCT_ID,
    _extract_stack_keywords,
    _route_job_posting_title,
    find_cloud_decoded_job_signals,
    run_combined_job_signal_scout,
)
from agents.marketing.mkt_o2_cold_dm_writer import run_o2_cold_dm_writer, run_o2_for_linkedin_leads
from tests.conftest import FakeSupabase

CONSULTING_ICP_CONFIG = {
    "job_titles": ["CTO"],
    "locations": ["United States"],
    "search_templates": ['"{title}" hiring "cloud architect" {location}'],
    "max_company_size": 200,
}


class _FakeBraveSearchScraper:
    """Stands in for scrapers.brave_search.BraveSearchScraper -- same
    shape tests/test_infra_consulting_outreach.py already established."""

    def __init__(self, items_by_query=None, query_count=0):
        self.api_key = "fake-key"
        self.query_count = query_count
        self._items_by_query = items_by_query or {}
        self.queries = []

    def _search(self, query):
        self.queries.append(query)
        self.query_count += 1
        return self._items_by_query.get(query, [])


def _search_item(link, title="Acme Corp - Careers", snippet=""):
    return {"link": link, "title": title, "snippet": snippet}


# ── _route_job_posting_title ────────────────────────────────────────────

def test_architect_title_routes_to_consulting():
    assert _route_job_posting_title("Cloud Solutions Architect") == "consulting"


def test_contract_title_routes_to_consulting():
    assert _route_job_posting_title("Contract DevOps Engineer") == "consulting"


def test_devops_engineer_routes_to_cloud_decoded():
    assert _route_job_posting_title("DevOps Engineer") == "cloud_decoded"


def test_sre_routes_to_cloud_decoded():
    assert _route_job_posting_title("Senior SRE") == "cloud_decoded"


def test_platform_engineer_routes_to_cloud_decoded():
    assert _route_job_posting_title("Platform Engineer II") == "cloud_decoded"


def test_ambiguous_title_matching_both_sets_routes_to_consulting():
    # "Contract DevOps Engineer" (above) already proves the tie-break;
    # this covers an infrastructure-design-flavored ops title too.
    assert _route_job_posting_title("Infrastructure Design Engineer") == "consulting"


def test_neither_set_matches_defaults_to_consulting():
    assert _route_job_posting_title("Marketing Manager") == "consulting"


def test_empty_or_none_title_defaults_to_consulting():
    assert _route_job_posting_title("") == "consulting"
    assert _route_job_posting_title(None) == "consulting"


def test_routing_is_case_insensitive():
    assert _route_job_posting_title("SENIOR DEVOPS ENGINEER") == "cloud_decoded"
    assert _route_job_posting_title("cloud ARCHITECT") == "consulting"


# ── _extract_stack_keywords ─────────────────────────────────────────────

def test_extract_stack_keywords_matches_present_terms():
    text = "We use Kubernetes on AWS with Terraform for provisioning and GitHub Actions for CI."
    keywords = _extract_stack_keywords(text)
    assert set(keywords) == {"AWS", "Terraform", "Kubernetes", "GitHub Actions"}


def test_extract_stack_keywords_case_insensitive():
    assert "Azure" in _extract_stack_keywords("we run everything on azure")


def test_extract_stack_keywords_azure_devops_matches_both_azure_and_azure_devops():
    # "Azure DevOps" containing "Azure" as a substring is expected, not a bug.
    keywords = _extract_stack_keywords("Our pipelines run on Azure DevOps")
    assert "Azure" in keywords
    assert "Azure DevOps" in keywords


def test_extract_stack_keywords_none_or_empty_returns_empty_list():
    assert _extract_stack_keywords(None) == []
    assert _extract_stack_keywords("") == []


def test_extract_stack_keywords_no_match_returns_empty_list():
    assert _extract_stack_keywords("We use a proprietary in-house deploy tool.") == []


# ── find_cloud_decoded_job_signals ──────────────────────────────────────

def test_no_api_key_returns_empty_gracefully(monkeypatch):
    class _NoKeyScraper(_FakeBraveSearchScraper):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.api_key = None

    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", _NoKeyScraper)
    signals = find_cloud_decoded_job_signals()
    assert signals == []


def test_relative_days_ago_within_cutoff_is_kept(monkeypatch):
    query = '"DevOps engineer" hiring United States'
    item = _search_item("https://boards.greenhouse.io/acme/jobs/1", title="DevOps Engineer", snippet="Posted 5 days ago")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)
    monkeypatch.setattr(lead_finder_module, "_fetch_job_posting_text", lambda url, http_get=None: None)

    signals = find_cloud_decoded_job_signals()
    assert len(signals) == 1
    assert signals[0]["job_posting_url"] == item["link"]
    assert signals[0]["job_posting_date"] == (date.today() - timedelta(days=5)).isoformat()


def test_no_ascertainable_date_is_dropped_not_assumed_recent(monkeypatch):
    query = '"DevOps engineer" hiring United States'
    item = _search_item("https://example.com/jobs/1", snippet="Great opportunity, apply now")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)

    signals = find_cloud_decoded_job_signals()
    assert signals == []


def test_stale_posting_beyond_max_age_is_dropped(monkeypatch):
    query = '"DevOps engineer" hiring United States'
    item = _search_item("https://example.com/jobs/1", snippet="Posted 90 days ago")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)

    signals = find_cloud_decoded_job_signals(max_age_days=30)
    assert signals == []


def test_company_under_min_size_is_excluded_when_detected(monkeypatch):
    query = '"DevOps engineer" hiring United States'
    item = _search_item("https://example.com/jobs/1", snippet="Posted 2 days ago. 2-5 employees.")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)

    signals = find_cloud_decoded_job_signals()
    assert signals == []


def test_company_over_max_size_is_excluded_when_detected(monkeypatch):
    query = '"DevOps engineer" hiring United States'
    item = _search_item("https://example.com/jobs/1", snippet="Posted 2 days ago. 800-1000 employees.")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)

    signals = find_cloud_decoded_job_signals()
    assert signals == []


def test_company_within_20_to_500_range_is_kept(monkeypatch):
    query = '"DevOps engineer" hiring United States'
    item = _search_item("https://example.com/jobs/1", snippet="Posted 2 days ago. 50-100 employees.")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)
    monkeypatch.setattr(lead_finder_module, "_fetch_job_posting_text", lambda url, http_get=None: None)

    signals = find_cloud_decoded_job_signals()
    assert len(signals) == 1


def test_undetectable_employee_count_is_not_treated_as_disqualifying(monkeypatch):
    query = '"DevOps engineer" hiring United States'
    item = _search_item("https://example.com/jobs/1", snippet="Posted 2 days ago.")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)
    monkeypatch.setattr(lead_finder_module, "_fetch_job_posting_text", lambda url, http_get=None: None)

    signals = find_cloud_decoded_job_signals()
    assert len(signals) == 1


def test_duplicate_urls_within_run_are_deduped(monkeypatch):
    query = '"DevOps engineer" hiring United States'
    item = _search_item("https://example.com/jobs/1", snippet="Posted 1 day ago")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item, item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)
    monkeypatch.setattr(lead_finder_module, "_fetch_job_posting_text", lambda url, http_get=None: None)

    signals = find_cloud_decoded_job_signals()
    assert len(signals) == 1


def test_jd_text_is_captured_and_stack_keywords_extracted(monkeypatch):
    query = '"platform engineer" hiring United States'
    item = _search_item("https://example.com/jobs/1", title="Platform Engineer", snippet="Posted 1 day ago")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)
    monkeypatch.setattr(
        lead_finder_module, "_fetch_job_posting_text",
        lambda url, http_get=None: "We run Kubernetes on Azure with Terraform.",
    )

    signals = find_cloud_decoded_job_signals()
    platform_signal = next(s for s in signals if s["job_posting_url"] == "https://example.com/jobs/1")
    assert platform_signal["job_posting_description"] == "We run Kubernetes on Azure with Terraform."
    assert set(platform_signal["job_posting_stack_keywords"]) == {"Kubernetes", "Azure", "Terraform"}


def test_fetch_jd_text_false_skips_real_fetch_and_falls_back_to_snippet(monkeypatch):
    query = '"cloud engineer" hiring United States'
    item = _search_item("https://example.com/jobs/1", title="Cloud Engineer", snippet="Posted 1 day ago, AWS shop")
    scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)

    def _boom(*a, **kw):
        raise AssertionError("_fetch_job_posting_text must not be called when fetch_jd_text=False")

    monkeypatch.setattr(lead_finder_module, "_fetch_job_posting_text", _boom)

    signals = find_cloud_decoded_job_signals(fetch_jd_text=False)
    cloud_signal = next(s for s in signals if s["job_posting_url"] == "https://example.com/jobs/1")
    assert cloud_signal["job_posting_description"] is None
    assert "AWS" in cloud_signal["job_posting_stack_keywords"]  # extracted from the snippet fallback


# ── run_combined_job_signal_scout ───────────────────────────────────────

def _combined_scraper(consulting_query, consulting_items, cd_query, cd_items):
    scraper = _FakeBraveSearchScraper(items_by_query={consulting_query: consulting_items, cd_query: cd_items})
    return scraper


def test_consulting_search_result_with_ops_title_routes_to_cloud_decoded(monkeypatch):
    """A consulting-flavored query can still surface an ongoing-ops
    title -- routing is by the posting's own title, not by which
    branch's search found it."""
    consulting_query = '"CTO" hiring "cloud architect" United States'
    item = _search_item("https://example.com/jobs/1", title="Platform Engineer", snippet="Posted 1 day ago")
    scraper = _FakeBraveSearchScraper(items_by_query={consulting_query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)
    monkeypatch.setattr(lead_finder_module, "_fetch_job_posting_text", lambda url, http_get=None: None)

    db = FakeSupabase(responses={
        "mse_icp_configs": [{"product_id": THDAGENTIC_CONSULTING_PRODUCT_ID, **CONSULTING_ICP_CONFIG}],
        "mse_leads": [{"id": "lead-1"}],
        "usage_events": [],
    })

    result = run_combined_job_signal_scout(supabase_client=db)

    assert result["cloud_decoded_leads_written"] == 1
    assert result["consulting_leads_written"] == 0
    insert_calls = [q for q in db.executed if q.table_name == "mse_leads" and q.calls[0][0] == "insert"]
    assert insert_calls[0]._payload[0]["source"] == "cloud_decoded_job_signal"
    assert insert_calls[0]._payload[0]["product_id"] == CLOUD_DECODED_PRODUCT_ID


def test_cloud_decoded_search_result_with_architect_title_routes_to_consulting(monkeypatch):
    """The inverse: a DevOps-flavored query surfacing an
    architect/contract-titled posting must route to consulting."""
    cd_query = '"DevOps engineer" hiring United States'
    item = _search_item("https://example.com/jobs/2", title="Contract Cloud Architect", snippet="Posted 1 day ago")
    scraper = _FakeBraveSearchScraper(items_by_query={cd_query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)
    monkeypatch.setattr(lead_finder_module, "_fetch_job_posting_text", lambda url, http_get=None: None)

    db = FakeSupabase(responses={
        "mse_icp_configs": [],  # no consulting ICP config -- must not block the cloud-decoded search
        "mse_leads": [{"id": "lead-1"}],
        "usage_events": [],
    })

    result = run_combined_job_signal_scout(supabase_client=db)

    assert result["consulting_leads_written"] == 1
    assert result["cloud_decoded_leads_written"] == 0
    insert_calls = [q for q in db.executed if q.table_name == "mse_leads" and q.calls[0][0] == "insert"]
    assert insert_calls[0]._payload[0]["source"] == "job_posting_signal"
    assert insert_calls[0]._payload[0]["product_id"] == THDAGENTIC_CONSULTING_PRODUCT_ID


def test_missing_consulting_icp_config_does_not_raise():
    db = FakeSupabase(responses={"mse_icp_configs": [], "usage_events": []})
    result = run_combined_job_signal_scout(supabase_client=db)
    assert result["status"] == "complete"
    assert result["consulting_leads_written"] == 0


def test_company_already_in_consulting_pipeline_blocks_cloud_decoded_entry(monkeypatch):
    """'One company, one pipeline, ever' -- a domain already present in
    EITHER pipeline must never get a second entry in the other."""
    cd_query = '"cloud engineer" hiring United States'
    item = _search_item("https://already-a-lead.com/jobs/1", title="Cloud Engineer", snippet="Posted 1 day ago")
    scraper = _FakeBraveSearchScraper(items_by_query={cd_query: [item]})
    monkeypatch.setattr(lead_finder_module, "BraveSearchScraper", lambda **kw: scraper)
    monkeypatch.setattr(lead_finder_module, "_fetch_job_posting_text", lambda url, http_get=None: None)

    db = FakeSupabase(responses={
        "mse_icp_configs": [],
        "mse_leads": [{"domain": "already-a-lead.com"}],  # already exists under job_posting_signal
        "usage_events": [],
    })

    result = run_combined_job_signal_scout(supabase_client=db)

    assert result["cloud_decoded_leads_written"] == 0
    assert result["company_dupes_dropped"] == 1


# ── MKT-O2 Cloud Decoded job-signal sequence ────────────────────────────

CD_SEQUENCE_JSON = json.dumps({
    "touch_1": "Saw Acme Corp hiring a Platform Engineer with Kubernetes/Azure in the stack — Cloud Decoded "
               "force-multiplies a team like that.",
    "touch_2": "Cloud Decoded's drift-detection and IAM-minimization agents cover exactly the gaps a new "
               "platform hire spends their first months on. Worth a look at theclouddecoded.com?",
})


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        return type("Msg", (), {"content": [type("Block", (), {"text": self._responses.pop(0)})()]})()


class _FakeAnthropic:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def _cd_job_signal_lead():
    return {
        "id": "lead-1", "company": "Acme Corp",
        "job_posting_title": "Platform Engineer", "job_posting_url": "https://example.com/jobs/1",
        "job_posting_stack_keywords": ["Kubernetes", "Azure"],
    }


def test_cloud_decoded_job_signal_writes_two_touches():
    db = FakeSupabase(responses={
        "mse_icp_configs": [{"selling_stage": "active"}],
        "mse_dm_sequences": [{"id": "seq-1"}],
    })
    anthropic = _FakeAnthropic([CD_SEQUENCE_JSON])

    result = run_o2_cold_dm_writer(
        product_id=CLOUD_DECODED_PRODUCT_ID, research_report={}, leads=[_cd_job_signal_lead()],
        lead_source="cloud_decoded_job_signal", supabase_client=db, anthropic_client=anthropic,
    )

    assert result["sequences_written"] == 1
    insert_calls = [q for q in db.executed if q.table_name == "mse_dm_sequences" and q.calls[0][0] == "insert"]
    row = insert_calls[0]._payload[0]
    assert "touch_3" not in row  # 2-touch, unlike the consulting branch's 3-touch
    assert row["lead_finder_lead_id"] == "lead-1"
    assert row["lead_source"] == "cloud_decoded_job_signal"
    assert "theclouddecoded.com" in row["touch_2"]


def test_cloud_decoded_job_signal_advances_lead_status_to_pending_email():
    db = FakeSupabase(responses={
        "mse_icp_configs": [{"selling_stage": "active"}],
        "mse_dm_sequences": [{"id": "seq-1"}],
    })
    anthropic = _FakeAnthropic([CD_SEQUENCE_JSON])

    run_o2_cold_dm_writer(
        product_id=CLOUD_DECODED_PRODUCT_ID, research_report={}, leads=[_cd_job_signal_lead()],
        lead_source="cloud_decoded_job_signal", supabase_client=db, anthropic_client=anthropic,
    )

    update_calls = [q for q in db.executed if q.table_name == "mse_leads" and q.calls[0][0] == "update"]
    assert update_calls[0]._payload == {"status": "pending_email"}


def test_cloud_decoded_job_signal_missing_touch_2_raises():
    db = FakeSupabase(responses={"mse_icp_configs": [{"selling_stage": "active"}]})
    bad_json = json.dumps({"touch_1": "a"})  # no touch_2
    anthropic = _FakeAnthropic([bad_json])

    try:
        run_o2_cold_dm_writer(
            product_id=CLOUD_DECODED_PRODUCT_ID, research_report={}, leads=[_cd_job_signal_lead()],
            lead_source="cloud_decoded_job_signal", supabase_client=db, anthropic_client=anthropic,
        )
        assert False, "expected ValueError-wrapping RuntimeError"
    except RuntimeError as exc:
        assert "touch_1, touch_2" in str(exc)


def test_run_o2_for_linkedin_leads_includes_cloud_decoded_job_signal_source(monkeypatch):
    # Same tests/conftest.py FakeSupabase limitation test_infra_consulting_outreach.py's
    # own equivalent test already documents.
    db = FakeSupabase(responses={
        "mse_linkedin_leads": [],
        "mse_leads": [_cd_job_signal_lead()],
    })
    calls: list[str] = []

    def fake_writer(*, product_id, research_report, leads, campaign_build_id, lead_source, supabase_client, anthropic_client=None):
        calls.append(lead_source)
        return {"sequences_written": len(leads)}

    monkeypatch.setattr(mkt_o2_module, "run_o2_cold_dm_writer", fake_writer)

    result = run_o2_for_linkedin_leads(product_id=CLOUD_DECODED_PRODUCT_ID, research_report={}, supabase_client=db)

    assert "cloud_decoded_job_signal" in calls
    assert result["by_source"]["cloud_decoded_job_signal"] == 1

    cd_select = [
        c for c in db.executed if c.table_name == "mse_leads" and c.calls[0][0] == "select"
        and ("source", "cloud_decoded_job_signal") in c._filters
    ]
    assert len(cd_select) == 1
