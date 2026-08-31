import pytest

from agents.dist.surface_planner import plan_surfaces
from agents.dist.quality_gate import check_surface


def _product_row(pid="prod-1", slug="small-portfolio-hub", name="Small Portfolio Hub"):
    return {"id": pid, "slug": slug, "name": name}


def _approved_positioning(pid="prod-1", wedge_type="structural"):
    return {
        "product_id": pid,
        "wedge_type": wedge_type,
        "icp": "Self-managing landlord, 10-30 units",
        "wedge": "Tenants pay nothing, human support",
        "price_rationale": "Argued against $0 cheapest substitute",
        "trigger_event": "Unit count crosses 10; January-February Schedule E prep",
        "status": "approved",
    }


# ---- DIST-S1 plan_surfaces -----------------------------------------

@pytest.mark.asyncio
async def test_plan_surfaces_no_approved_positioning_returns_empty(fake_db):
    fake_db.responses["mse_products"] = [_product_row()]
    fake_db.responses["mse_positioning"] = []  # nothing approved

    result = await plan_surfaces("small-portfolio-hub", supabase_client=fake_db)
    assert result == []


def _upserted_archetypes(fake_db):
    # FakeQuery.execute() only echoes back pre-set fake_db.responses[...],
    # not what was actually upserted -- so plan_surfaces' own return value
    # can't be trusted in this fake. What was really attempted lives in
    # fake_db.executed's recorded upsert payloads, same convention other
    # tests in this repo use for insert/upsert assertions.
    return [
        q._payload["archetype"]
        for q in fake_db.executed
        if q.table_name == "mse_content_surfaces" and q._payload is not None
    ]


@pytest.mark.asyncio
async def test_plan_surfaces_structural_wedge_plans_competitor_archetypes(fake_db):
    fake_db.responses["mse_products"] = [_product_row()]
    fake_db.responses["mse_positioning"] = [_approved_positioning(wedge_type="structural")]
    fake_db.responses["mse_competitors"] = [
        {"id": "comp-1", "name": "Innago"},
        {"id": "comp-2", "name": "TurboTenant"},
    ]

    await plan_surfaces("small-portfolio-hub", supabase_client=fake_db)
    archetypes = _upserted_archetypes(fake_db)
    assert archetypes.count("vs_competitor") == 2
    assert archetypes.count("alternatives_to") == 2
    assert "jtbd" in archetypes
    assert "calculator" in archetypes


@pytest.mark.asyncio
async def test_plan_surfaces_temporary_wedge_excludes_competitor_claims(fake_db):
    fake_db.responses["mse_products"] = [_product_row(pid="prod-2", slug="tradesdesk", name="TradesDesk")]
    fake_db.responses["mse_positioning"] = [_approved_positioning(pid="prod-2", wedge_type="temporary")]
    fake_db.responses["mse_competitors"] = [{"id": "comp-1", "name": "Orcatec"}]

    await plan_surfaces("tradesdesk", supabase_client=fake_db)
    archetypes = _upserted_archetypes(fake_db)
    assert "vs_competitor" not in archetypes
    assert "alternatives_to" not in archetypes
    assert "jtbd" in archetypes  # non-competitor archetypes still plan


# ---- DIST-S3 check_surface -------------------------------------------

def _surface_row(**overrides):
    row = {
        "id": "surf-1",
        "product_id": "prod-1",
        "archetype": "jtbd",
        "slug": "test-surface",
        "title": "Test Surface",
        "body_mdx": "We charge $99/mo for this. " * 60,  # 300 words (5/repeat), clears the 250-word jtbd floor
        "data_payload": None,
        "competitor_id": None,
        "hitl_tier": 2,
        "reject_count": 0,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_check_surface_passes_real_original_content(fake_db):
    fake_db.responses["mse_content_surfaces"] = [_surface_row()]
    result = await check_surface("surf-1", supabase_client=fake_db)
    assert result["passed"] is True


@pytest.mark.asyncio
async def test_check_surface_rejects_thin_restated_marketing(fake_db):
    # No $ math, no "we"/"our" framing, no data_payload -- pure restatement.
    fake_db.responses["mse_content_surfaces"] = [
        _surface_row(body_mdx="Competitor X offers great features and is popular among users.")
    ]
    result = await check_surface("surf-1", supabase_client=fake_db)
    assert result["passed"] is False
    assert "original-value" in result["reason"]


@pytest.mark.asyncio
async def test_check_surface_rejects_below_substance_floor(fake_db):
    fake_db.responses["mse_content_surfaces"] = [_surface_row(body_mdx="We charge $99/mo. Short.")]
    result = await check_surface("surf-1", supabase_client=fake_db)
    assert result["passed"] is False
    assert "substance floor" in result["reason"]


@pytest.mark.asyncio
async def test_check_surface_rejects_stale_competitor_claim(fake_db):
    fake_db.responses["mse_content_surfaces"] = [_surface_row(competitor_id="comp-1")]
    fake_db.responses["mse_competitors"] = [{
        "id": "comp-1", "pricing_url": "https://innago.com/pricing",
        "last_verified_at": "2025-01-01T00:00:00Z",  # >90 days old
    }]
    result = await check_surface("surf-1", supabase_client=fake_db)
    assert result["passed"] is False
    assert "stale" in result["reason"]


@pytest.mark.asyncio
async def test_check_surface_third_rejection_pauses_generator(fake_db):
    fake_db.responses["mse_content_surfaces"] = [_surface_row(body_mdx="thin.", reject_count=2)]
    fake_db.responses["mse_products"] = [_product_row()]

    result = await check_surface("surf-1", supabase_client=fake_db)
    assert result["passed"] is False

    paused = [q for q in fake_db.executed if q.table_name == "mse_generator_state"]
    assert len(paused) == 1
    assert paused[0]._payload["paused"] is True

    events = [q for q in fake_db.executed if q.table_name == "mse_monitoring_events"]
    assert len(events) == 1
    assert events[0]._payload["requires_human_decision"] is True
    assert events[0]._payload["run_type"] == "triggered"
