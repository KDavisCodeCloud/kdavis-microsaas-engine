"""
Tests for agents/factory/brief_generator.py's generate_research_report_from_verdict
(added 2026-08-22) -- the real-pipeline replacement for the originally-specced
marketing_brief_generator.py (mse_marketing_briefs/mse_group_registry/a
fictional mkt_v1(context) call). This seeds the REAL MKT-R1 schema
(mse_research_reports.report_json) and upserts mse_icp_configs, then calls
the REAL agents/marketing/mkt_orch_campaign_orchestrator.run_campaign_orchestrator
-- which already fires MKT-O1/O2/O3/S1/V1, including MKT-O3's existing
trial-nurture sequence draft (mse_email_sequences, pending_hitl) that turned
out to already be the "draft Brevo copy for review" piece this session's
plan originally assumed was missing.

run_campaign_orchestrator itself is monkeypatched here -- its own behavior
(channel selection, firing each downstream agent) is that module's own
test surface, not this one's. These tests only verify
generate_research_report_from_verdict builds the right report_json/
icp_config and calls the orchestrator with the right arguments.
"""
import agents.factory.brief_generator as brief_generator
import agents.marketing.mkt_orch_campaign_orchestrator as mkt_orch

from conftest import FakeSupabase


def _seed_opportunity(fake_db, **overrides):
    row = {
        "id": "opp-1",
        "vertical": "Residential Trades / Service Contractors",
        "pain_point": "Per-technician pricing scales painfully for 2-3 tech shops",
        "solution_concept": "Flat-fee job management for 1-5 tech HVAC shops",
        "mrr_calculation": "some math",
        "icp": {
            "business_type": "Solo/small HVAC contractor",
            "decision_maker": "Owner-operator",
            "company_size": "2-3 employees",
            "annual_revenue_range": "$200K-$600K",
        },
        "competitor_examples": [{"name": "ServiceTitan", "url": "https://www.servicetitan.com", "notable_weakness": "per-technician pricing"}],
        "tier_structure": {"tier_1": {"name": "Starter", "price_monthly": 79}},
        "source_urls": ["https://www.g2.com/products/servicetitan/reviews"],
        "verdict_v2_output": {"confidence_score": 80},
        "vertical_agent_extras": {
            "agent": "Trades",
            "trade_segment": "HVAC",
            "facebook_groups_identified": ["HVAC Business Owners Network"],
            "state_license_db_sources": ["TX", "CA"],
            "youtube_content_angle": "What ServiceTitan really costs a 2-tech shop",
        },
        **overrides,
    }
    fake_db.responses["opportunity_pipeline"] = [row]
    return row


def _patch_campaign_orchestrator(monkeypatch, return_value=None):
    calls = []

    def fake_run_campaign_orchestrator(product_id, research_opp_id, vertical, supabase_client=None):
        calls.append({"product_id": product_id, "research_opp_id": research_opp_id, "vertical": vertical})
        return return_value or {"campaign_build_id": "cb-1", "channels": ["seo", "email", "facebook"], "status_updates": {}}

    monkeypatch.setattr(mkt_orch, "run_campaign_orchestrator", fake_run_campaign_orchestrator)
    return calls


def test_writes_real_schema_research_report_with_additive_vertical_findings(monkeypatch):
    fake_db = FakeSupabase()
    _seed_opportunity(fake_db)
    orch_calls = _patch_campaign_orchestrator(monkeypatch)

    result = brief_generator.generate_research_report_from_verdict(
        "opp-1", "product-123", "triggered-by-test", supabase_client=fake_db,
    )

    report = result["report"]
    for required_key in (
        "product_id", "cycle_date", "trending_topics", "pain_language",
        "competitor_moves", "content_angles", "proof_signals", "icp_channels",
        "willingness_to_pay_band", "wtp_evidence", "suggested_price",
    ):
        assert required_key in report, f"missing real MKT-R1 field: {required_key}"

    assert report["product_id"] == "product-123"
    assert report["competitor_moves"][0]["competitor"] == "ServiceTitan"
    assert report["icp_channels"] == ["facebook_groups"]

    # Additive, non-breaking key -- real MKT-R1/MKT-ORCH readers only look
    # up the specific keys they expect and never touch this one.
    assert report["vertical_agent_findings"]["agent"] == "Trades"
    assert report["vertical_agent_findings"]["trade_segment"] == "HVAC"

    upsert_calls = [c for c in fake_db.executed if c.table_name == "mse_research_reports"]
    assert len(upsert_calls) == 1
    assert upsert_calls[0]._payload["product_id"] == "product-123"

    assert orch_calls == [{"product_id": "product-123", "research_opp_id": "opp-1", "vertical": "Residential Trades / Service Contractors"}]


def test_upserts_icp_config_with_scraper_slug_and_search_templates(monkeypatch):
    fake_db = FakeSupabase()
    _seed_opportunity(fake_db)
    _patch_campaign_orchestrator(monkeypatch)

    brief_generator.generate_research_report_from_verdict("opp-1", "product-123", "triggered-by-test", supabase_client=fake_db)

    icp_upserts = [c for c in fake_db.executed if c.table_name == "mse_icp_configs"]
    assert len(icp_upserts) == 1
    payload = icp_upserts[0]._payload
    assert payload["product_id"] == "product-123"
    # Must be the short slug ("trades") scrapers/verticals/__init__.py's
    # VERTICAL_SCRAPERS registry is keyed on, NOT the long descriptive
    # opportunity vertical string -- agents/marketing/mkt_lead_finder.py
    # does an exact-match lookup against this exact column.
    assert payload["vertical"] == "trades"
    assert payload["job_titles"] == ["Owner-operator"]
    assert payload["search_templates"]["facebook_groups"] == ["HVAC Business Owners Network"]
    assert payload["search_templates"]["state_license_db_sources"] == ["TX", "CA"]


def test_falls_back_to_full_vertical_string_for_non_vertical_agent_opportunities(monkeypatch):
    """An opportunity from one of the original 6 generic-fallback verticals
    (no vertical_agent_extras.agent set) has no matching scraper anyway --
    mse_icp_configs.vertical just carries the original string through,
    which get_vertical_scraper() safely returns None for."""
    fake_db = FakeSupabase()
    _seed_opportunity(fake_db, vertical="Finance / Accounting / Bookkeeping", vertical_agent_extras={})
    _patch_campaign_orchestrator(monkeypatch)

    brief_generator.generate_research_report_from_verdict("opp-1", "product-123", "triggered-by-test", supabase_client=fake_db)

    icp_upserts = [c for c in fake_db.executed if c.table_name == "mse_icp_configs"]
    assert icp_upserts[0]._payload["vertical"] == "Finance / Accounting / Bookkeeping"


def test_raises_and_writes_audit_lose_when_opportunity_not_found(monkeypatch):
    fake_db = FakeSupabase()  # no opportunity_pipeline row seeded
    _patch_campaign_orchestrator(monkeypatch)

    try:
        brief_generator.generate_research_report_from_verdict("missing-opp", "product-123", "triggered-by-test", supabase_client=fake_db)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "not found" in str(exc)

    audit_writes = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert any(c._payload.get("outcome") == "lose" for c in audit_writes)


def test_never_fails_silently_on_campaign_orchestrator_error(monkeypatch):
    fake_db = FakeSupabase()
    _seed_opportunity(fake_db)

    def failing_orchestrator(*args, **kwargs):
        raise RuntimeError("MKT-ORCH found no research_report for product ... -- simulated")

    monkeypatch.setattr(mkt_orch, "run_campaign_orchestrator", failing_orchestrator)

    try:
        brief_generator.generate_research_report_from_verdict("opp-1", "product-123", "triggered-by-test", supabase_client=fake_db)
        assert False, "expected RuntimeError to propagate"
    except RuntimeError as exc:
        assert "Research report seed failed" in str(exc)
