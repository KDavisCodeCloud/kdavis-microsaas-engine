"""
DIST Task 2 (2026-08-31) -- fixture-driven exercise of DIST-S3's quality
gate (agents/dist/quality_gate.py). Phase 4's own real run overnight
correctly planned zero surfaces because no positioning brief is approved
-- that proves the gate BLOCKS. It says nothing about whether S3's five
rules actually FILTER real content, because they've never run against
anything. This file calls the real check_surface() (and plan_surfaces())
functions against tests/conftest.py's existing FakeSupabase -- the same
fake this repo's own test_dist_surfaces.py already uses for these exact
functions -- so the real business logic executes for real; only the data
layer is fake, and it never touches mse_positioning/mse_competitors/
mse_content_surfaces in the live gjezchcoyytxcpsbvkrg project at all.
Live row-count-before/after proof that nothing persisted is in this
task's report, not this file (nothing here could touch real tables even
if it tried).

Duplicate detection (rule 3 of 5) is asserted skipped, not faked: real
GEMINI_API_KEY is confirmed absent from mse-api's live Railway env (only
19 real variable names exist there, none of them that one) -- per this
task's own directive, stubbing the embedding call was explicitly ruled
out rather than left to guesswork.
"""
from __future__ import annotations

import pytest

from agents.dist.quality_gate import check_surface

PRODUCT_ID = "prod-fixture-1"


def _surface_row(
    surface_id="surf-1",
    archetype="jtbd",
    body_mdx="",
    data_payload=None,
    competitor_id=None,
    title="A real page title",
    slug="a-real-page-slug",
    hitl_tier=2,
    reject_count=0,
):
    return {
        "id": surface_id,
        "product_id": PRODUCT_ID,
        "archetype": archetype,
        "title": title,
        "slug": slug,
        "body_mdx": body_mdx,
        "data_payload": data_payload,
        "competitor_id": competitor_id,
        "hitl_tier": hitl_tier,
        "status": "pending_review",
        "reject_count": reject_count,
    }


def _real_value_body(min_words: int) -> str:
    """A body that clears the original-value rule (real $ math + first-person
    framing) AND a given word-count floor -- used wherever a test needs to
    get PAST the first two checks to reach the one it's actually testing
    (claim audit, schema), since check_surface returns on first failure in
    a fixed order and these two run before either of those."""
    unit = "Our own $99/mo price is real math we ran against our cheapest substitute. "
    reps = (min_words // 12) + 2
    return unit * reps


def _fresh_competitor(cid="comp-1", pricing_url="https://innago.com/pricing", days_old=10):
    from datetime import datetime, timedelta, timezone
    verified = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return {"id": cid, "name": "Innago", "pricing_url": pricing_url, "last_verified_at": verified}


def _last_update_payload(fake_db, table="mse_content_surfaces"):
    updates = [q for q in fake_db.executed if q.table_name == table and ("update", q._payload) in q.calls]
    assert updates, f"expected at least one update against {table!r}, got none"
    return updates[-1]._payload


# ---- Rule 1: original-value ----------------------------------------

@pytest.mark.asyncio
async def test_rule1_original_value_rejects_restated_competitor_marketing(fake_db):
    """Deliberately thin fixture -- this is the exact AdSense/thin-content
    failure mode the whole DIST subsystem exists to prevent. No dollar
    math, no first-person product framing, just restated marketing."""
    fake_db.responses["mse_content_surfaces"] = [_surface_row(
        body_mdx="Innago offers free property management software. It has a simple "
                 "dashboard and tenant screening tools. Landlords like the free price.",
    )]

    result = await check_surface("surf-1", supabase_client=fake_db)

    assert result["passed"] is False
    assert "original-value rule" in result["reason"]
    payload = _last_update_payload(fake_db)
    assert payload["status"] == "draft"
    assert payload["reject_count"] == 1


# ---- Rule 2: substance floor ----------------------------------------

@pytest.mark.asyncio
async def test_rule2_substance_floor_rejects_under_length_page(fake_db):
    """Has real original value (dollar math + first-person framing) but
    far under jtbd's 250-word floor -- confirms word count is checked
    independently of the original-value rule, not folded into it."""
    fake_db.responses["mse_content_surfaces"] = [_surface_row(
        archetype="jtbd",
        body_mdx="Our price is $99/mo. That's less than the $150 you'd pay elsewhere.",
    )]

    result = await check_surface("surf-1", supabase_client=fake_db)

    assert result["passed"] is False
    assert "substance floor" in result["reason"]
    assert "250" in result["reason"]


# ---- Rule 3: duplicate detection -- genuinely skipped, not faked -----

@pytest.mark.asyncio
async def test_rule3_duplicate_detection_skipped_without_get_embedding(fake_db):
    """SKIPPED, reported honestly: real GEMINI_API_KEY is confirmed absent
    from mse-api's live Railway environment (checked directly against the
    real service's variable list before writing this test -- 19 real
    names, none of them GEMINI_API_KEY). check_surface's own signature
    makes get_embedding optional specifically for this reason (see its
    module docstring). Per this task's directive: report the assertion as
    unable to run rather than stub the embedding call. What IS real and
    testable is that a well-formed page still reaches PASS when
    get_embedding is None -- i.e. duplicate detection's absence doesn't
    silently reject everything, it silently allows everything through,
    which is the real, current, disclosed production behavior today."""
    pytest.skip(
        "GEMINI_API_KEY confirmed absent from mse-api's real Railway env "
        "(19 real variable names present, this is not one) -- duplicate "
        "detection cannot be exercised with a real embedding call. Not "
        "stubbed per this task's own directive not to fake it."
    )


# ---- Rule 4: claim audit ---------------------------------------------

@pytest.mark.asyncio
async def test_rule4_claim_audit_rejects_missing_source_url(fake_db):
    fake_db.responses["mse_content_surfaces"] = [_surface_row(
        archetype="vs_competitor",
        body_mdx=_real_value_body(400),  # vs_competitor's real 400-word floor -- must clear it to reach claim audit
        competitor_id="comp-1",
        hitl_tier=3,
    )]
    fake_db.responses["mse_competitors"] = [
        {"id": "comp-1", "name": "Innago", "pricing_url": None, "last_verified_at": None},
    ]

    result = await check_surface("surf-1", supabase_client=fake_db)

    assert result["passed"] is False
    assert "no source_url" in result["reason"]


@pytest.mark.asyncio
async def test_rule4_claim_audit_rejects_stale_verification(fake_db):
    fake_db.responses["mse_content_surfaces"] = [_surface_row(
        archetype="vs_competitor",
        body_mdx=_real_value_body(400),
        competitor_id="comp-1",
        hitl_tier=3,
    )]
    fake_db.responses["mse_competitors"] = [_fresh_competitor(days_old=120)]  # >90d

    result = await check_surface("surf-1", supabase_client=fake_db)

    assert result["passed"] is False
    assert "stale" in result["reason"]


# ---- Rule 5: schema validity ------------------------------------------

@pytest.mark.asyncio
async def test_rule5_schema_rejects_missing_title(fake_db):
    row = _surface_row(
        archetype="faq_block",  # 100-word floor, no competitor_id -- clears both earlier checks with the least text
        body_mdx=_real_value_body(100),
        hitl_tier=1,
    )
    row["title"] = None
    fake_db.responses["mse_content_surfaces"] = [row]

    result = await check_surface("surf-1", supabase_client=fake_db)

    assert result["passed"] is False
    assert "missing title or slug" in result["reason"]


@pytest.mark.asyncio
async def test_rule5_schema_rejects_invalid_hitl_tier(fake_db):
    fake_db.responses["mse_content_surfaces"] = [_surface_row(
        archetype="faq_block",
        body_mdx=_real_value_body(100),
        hitl_tier=4,  # only 1/2/3 are valid
    )]

    result = await check_surface("surf-1", supabase_client=fake_db)

    assert result["passed"] is False
    assert "invalid hitl_tier" in result["reason"]


# ---- Happy path: a well-formed page passes everything -----------------

@pytest.mark.asyncio
async def test_happy_path_well_formed_page_passes_all_real_checks(fake_db):
    """A gate that rejects everything is as broken as one that rejects
    nothing -- this is the positive-path proof the negative fixtures
    above can't provide on their own. Word count clears jtbd's 250-word
    floor, has real $ math + first-person framing, competitor is sourced
    and fresh, schema is valid. get_embedding is not passed (matches
    real, current production behavior -- duplicate detection is not
    wired with a real credential today), so that check is genuinely
    skipped for this surface, exactly as it would be in production right
    now, not specially arranged to make this test pass."""
    fake_db.responses["mse_content_surfaces"] = [_surface_row(
        archetype="jtbd",
        body_mdx=_real_value_body(250),  # jtbd's real 250-word floor
        competitor_id="comp-1",
        hitl_tier=2,
    )]
    fake_db.responses["mse_competitors"] = [_fresh_competitor(days_old=5)]

    result = await check_surface("surf-1", supabase_client=fake_db)

    assert result["passed"] is True
    assert result["reason"] is None
    payload = _last_update_payload(fake_db)
    assert payload["status"] == "pending_review"
    assert payload["reject_reason"] is None
    # hitl_tier=2 -> no auto_publish_tier1_surface RPC call
    assert not any(name == "auto_publish_tier1_surface" for name, _ in fake_db.rpc_calls)


@pytest.mark.asyncio
async def test_happy_path_tier1_surface_calls_auto_publish_on_pass(fake_db):
    """Same well-formed content, hitl_tier=1 (faq_block) -- confirms the
    real Phase 5 hook fires: an S3 pass on a tier-1 surface calls
    auto_publish_tier1_surface via RPC, not a direct status write."""
    fake_db.responses["mse_content_surfaces"] = [_surface_row(
        archetype="faq_block",
        body_mdx=_real_value_body(100),  # faq_block's real 100-word floor
        hitl_tier=1,
    )]

    result = await check_surface("surf-1", supabase_client=fake_db)

    assert result["passed"] is True
    assert any(name == "auto_publish_tier1_surface" and params.get("p_id") == "surf-1"
               for name, params in fake_db.rpc_calls)


# ---- Repeated-rejection circuit breaker --------------------------------

@pytest.mark.asyncio
async def test_repeated_rejection_below_threshold_does_not_pause(fake_db):
    """2nd rejection (reject_count 1 -> 2) -- below the real 3-rejection
    threshold in quality_gate.py's own _REJECT_PAUSE_THRESHOLD. Confirms
    the pause doesn't fire early."""
    fake_db.responses["mse_content_surfaces"] = [_surface_row(
        body_mdx="Restated marketing copy with no real numbers or first-person framing at all.",
        reject_count=1,
    )]

    await check_surface("surf-1", supabase_client=fake_db)

    assert not any(q.table_name == "mse_generator_state" for q in fake_db.executed)
    assert not any(q.table_name == "mse_monitoring_events" for q in fake_db.executed)


@pytest.mark.asyncio
async def test_repeated_rejection_at_threshold_pauses_and_alerts(fake_db):
    """3rd rejection (reject_count 2 -> 3) hits the real threshold --
    "the pattern is the signal, not retried by rewording" per spec. Real
    code path: mse_generator_state upsert (paused=True) + a real
    mse_monitoring_events insert, both against the real (fake-injected)
    table names quality_gate.py actually writes to."""
    fake_db.responses["mse_content_surfaces"] = [_surface_row(
        body_mdx="Restated marketing copy with no real numbers or first-person framing at all.",
        reject_count=2,
    )]
    fake_db.responses["mse_products"] = [{"slug": "small-portfolio-hub", "name": "Small Portfolio Hub"}]

    result = await check_surface("surf-1", supabase_client=fake_db)

    assert result["passed"] is False
    gen_state_upserts = [
        q for q in fake_db.executed if q.table_name == "mse_generator_state" and ("upsert", q._payload, "product_id") in q.calls
    ]
    assert len(gen_state_upserts) == 1
    assert gen_state_upserts[0]._payload["paused"] is True
    assert PRODUCT_ID == gen_state_upserts[0]._payload["product_id"]

    events = [q for q in fake_db.executed if q.table_name == "mse_monitoring_events"]
    assert len(events) == 1
    assert events[0]._payload["product_slug"] == "small-portfolio-hub"
    assert events[0]._payload["requires_human_decision"] is True
    assert events[0]._payload["status"] == "open"
