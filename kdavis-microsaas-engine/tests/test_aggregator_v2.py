"""
Verdict Agent v5.0 tests. The live web-search-backed research quality
itself can only really be tuned by running real opportunities through it
— these tests cover the deterministic Python-side contract instead: JSON
extraction, status mapping, and the price-adjusted floor enforcement
(now applied uniformly to BUILD and CONDITIONAL, since v5.0 retired the
"floor doesn't clear but might with a named partner" escape hatch that
let a real v4.0 CONDITIONAL result reach the DB at 28% of its target).

Kept as test_aggregator_v2.py (not renamed) since it's the same module
under test (agents/aggregator/agent.py) evolving in place, same as the
prompt itself went v2.0 -> v3.0 -> v4.0 -> v5.0 without a file rename.

v5.0 retired SATURATED/PARTIAL/CLEAR as competitor_state values and
RESUBMIT as a primary verdict -- there are only three legal verdicts now
(BUILD | CONDITIONAL | DO_NOT_BUILD), anchored on a named existing_tool
rather than a competitors_found list. Tests for the retired concepts
were removed rather than kept passing against dead code.
"""
import json

import pytest

from agents.aggregator.agent import _extract_trailing_json, _price_adjusted_floor, run


def _canned(payload: dict, preamble: str = "Some research narrative before the final object.\n\n") -> str:
    return preamble + json.dumps(payload)


def _base_payload(**overrides) -> dict:
    payload = {
        "opportunity_id": "opp-1",
        "vertical": "Solo bookkeepers managing 5+ clients on QuickBooks who need client approval before check runs",
        "solution_concept": "Test Product",
        "existing_tool": {"name": "ApprovalMax", "price": "$54/mo", "rating": 4.6, "review_count": 618, "source_url": "g2.com/products/approvalmax"},
        "pain_confirmed": True,
        "ongoing_complaints": True,
        "pain_evidence": "G2 reviews, June 2026: users citing missing mobile approval flow",
        "gap_type": "FEATURE_GAP",
        "gap_evidence": "ApprovalMax has no mobile-native approval flow — 6 reviews cite this exact gap",
        "icp": "Solo bookkeepers managing 5+ clients on QuickBooks",
        "unhappy_segment_total": 50000,
        "unhappy_segment_source": "QuickBooks ProAdvisor directory count",
        "unhappy_segment_pct": 10,
        "underserved_accounts": 5000,
        "gtm_channel": "QuickBooks App Store listing + ProAdvisor referral program",
        "discovery_rate_pct": 8,
        "reachable_segment": 400,
        "capture_rate_pct": 0.5,
        "paying_accounts": 25,
        "proposed_price": 39,
        "price_tier": "$39-59",
        "gross_mrr": 975,
        "churn_haircut_pct": 20,
        "net_mrr_floor": 780,
        "price_adjusted_floor": 4000,
        "floor_cleared": False,
        "month_floor_cleared": 0,
        "timeline_classification": "FAIL",
        "verdict": "BUILD",
        "failed_at_step": None,
        "reason": None,
        "no_saturation_checklist": {"rating_above_4_3": True, "no_recurring_complaint_pattern": False, "priced_accessibly": True, "no_platform_dependency": True},
    }
    payload.update(overrides)
    return payload


def _with_floor(payload: dict, net_mrr_floor: float) -> dict:
    payload = dict(payload)
    payload["net_mrr_floor"] = net_mrr_floor
    return payload


# ── JSON extraction ──────────────────────────────────────────────

def test_extracts_last_balanced_json_object_ignoring_narrative_braces():
    text = 'Notes: the tool costs {"placeholder": "not this one"} per docs.\n\n' + json.dumps({"verdict": "BUILD", "proposed_price": 39})
    result = _extract_trailing_json(text)
    assert result["verdict"] == "BUILD"


def test_raises_with_raw_text_when_nothing_parses():
    with pytest.raises(RuntimeError):
        _extract_trailing_json("Just narrative, no JSON object anywhere.")


# ── Price-adjusted floor table ──────────────────────────────────────

@pytest.mark.parametrize("price,expected_floor", [
    (25, 3500), (39, 4000), (59, 4000), (69, 4500), (99, 4500), (100, 5000), (150, 5000),
])
def test_price_adjusted_floor_matches_the_spec_table(price, expected_floor):
    assert _price_adjusted_floor(price) == expected_floor


def test_price_adjusted_floor_falls_back_to_4000_when_price_missing():
    assert _price_adjusted_floor(None) == 4000
    assert _price_adjusted_floor(0) == 4000


# ── Verdict -> status mapping ──────────────────────────────────────

def test_build_verdict_maps_to_ready_to_build():
    payload = _with_floor(_base_payload(verdict="BUILD", timeline_classification="STRONG"), net_mrr_floor=4500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"
    assert result["rejection_reason"] is None


def test_conditional_verdict_maps_to_validated_when_floor_genuinely_clears():
    # v5.0: CONDITIONAL differs from BUILD only in WHEN the floor clears
    # (month 8-12 instead of 1-7), never in WHETHER it clears.
    payload = _with_floor(_base_payload(verdict="CONDITIONAL", timeline_classification="PASS", month_floor_cleared=10), net_mrr_floor=4200)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "validated"


def test_do_not_build_maps_to_rejected_with_reason():
    llm = lambda system, user: _canned(_base_payload(
        verdict="DO_NOT_BUILD", failed_at_step=3, reason="Net MRR floor never clears within 12 months.",
    ))
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "Net MRR floor never clears" in result["rejection_reason"]


def test_do_not_build_with_no_reason_still_gets_a_readable_message():
    llm = lambda system, user: _canned(_base_payload(verdict="DO_NOT_BUILD", reason=None))
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert result["rejection_reason"]  # never blank/None -- always something readable


def test_unrecognized_verdict_falls_back_to_rejected():
    # RESUBMIT is retired as a primary verdict in v5.0 -- if the model
    # emits it anyway, it must not be silently treated as a pass-through.
    llm = lambda system, user: _canned(_base_payload(verdict="RESUBMIT", reason="Missing evidence source."))
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"


# ── Code-level floor enforcement (v5.0: uniform across BUILD + CONDITIONAL) ──

def test_code_level_floor_check_overrides_a_build_verdict_below_adjusted_floor():
    # Guards against the model saying BUILD with a net_mrr_floor that
    # doesn't actually clear the price-adjusted floor — the one number
    # this factory's business model depends on must never be trusted from
    # the model's self-report alone.
    payload = _with_floor(_base_payload(verdict="BUILD", proposed_price=39), net_mrr_floor=3200)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "Code-level floor check failed" in result["rejection_reason"]
    assert "$4,000" in result["rejection_reason"]  # $39/mo tier -> $4,000 floor


def test_code_level_floor_check_also_covers_conditional_not_just_build():
    # Real bug found 2026-07-19 under v4.0: a live CONDITIONAL verdict
    # reached the DB with net_mrr_floor at only 28% of the price-adjusted
    # floor because the old spec allowed a "named distribution partner"
    # escape hatch for CONDITIONAL. v5.0 retires that escape hatch
    # entirely -- CONDITIONAL requires the SAME floor-clearing as BUILD,
    # just later. This must still be caught at the code level regardless
    # of what the model claims.
    payload = _with_floor(_base_payload(verdict="CONDITIONAL", proposed_price=69, timeline_classification="PASS"), net_mrr_floor=1247)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "Code-level floor check failed" in result["rejection_reason"]


def test_code_level_floor_uses_the_lower_price_adjusted_floor_for_cheap_products():
    # A $25/mo product only needs to clear $3,500, not $4,000 -- this must
    # NOT be code-level-rejected even though it's below the old flat floor.
    payload = _with_floor(_base_payload(verdict="BUILD", proposed_price=25), net_mrr_floor=3700)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"


def test_code_level_floor_uses_the_higher_price_adjusted_floor_for_premium_products():
    # A $120/mo product needs $5,000, not $4,000 -- this must be rejected
    # even though $4,200 would have cleared the old flat floor.
    payload = _with_floor(_base_payload(verdict="BUILD", proposed_price=120), net_mrr_floor=4200)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "$5,000" in result["rejection_reason"]


def test_full_verdict_v5_output_is_preserved_on_the_result():
    payload = _with_floor(_base_payload(verdict="BUILD"), net_mrr_floor=4500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["verdict_v2_output"]["existing_tool"]["name"] == "ApprovalMax"
    assert result["verdict_v2_output"]["gap_type"] == "FEATURE_GAP"
    assert result["verdict_v2_output"]["price_adjusted_floor"] == 4000  # code-computed, stored back for auditability


# ── Confidence score override (added 2026-07-19) ────────────────────

def test_confidence_below_45_forces_do_not_build_regardless_of_verdict():
    payload = _with_floor(_base_payload(verdict="BUILD", confidence_score=30), net_mrr_floor=4500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "Confidence override" in result["rejection_reason"]
    assert "30" in result["rejection_reason"]


def test_confidence_45_to_59_downgrades_build_to_conditional():
    payload = _with_floor(_base_payload(verdict="BUILD", confidence_score=52), net_mrr_floor=4500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "validated"
    assert "Downgraded BUILD to CONDITIONAL" in result["verdict_v2_output"]["confidence_downgrade_note"]


def test_confidence_score_never_upgrades_a_do_not_build():
    # A perfect confidence score must not rescue a verdict that already
    # failed Steps 1-3 on the merits (e.g. the floor never cleared).
    payload = _with_floor(_base_payload(verdict="DO_NOT_BUILD", confidence_score=95, reason="Floor never clears."), net_mrr_floor=500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"


def test_confidence_60_plus_leaves_build_verdict_untouched():
    payload = _with_floor(_base_payload(verdict="BUILD", confidence_score=82), net_mrr_floor=4500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"
    assert "confidence_downgrade_note" not in result["verdict_v2_output"]


def test_confidence_score_absent_does_not_affect_legacy_style_responses():
    # A response that never mentions confidence_score at all (e.g. an
    # older-shaped canned test) must not be touched by this override --
    # the branch only activates when the model actually reports a score.
    payload = _with_floor(_base_payload(verdict="BUILD"), net_mrr_floor=4500)
    assert "confidence_score" not in payload or payload.get("confidence_score") is None
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"


# ── Regression cases from prompt.md's SATURATED-is-retired rule ─────
# A competitor existing (even a well-reviewed one) is never grounds for
# DO_NOT_BUILD on its own -- only a failure of Steps 1-3 is. These verify
# the Python-side handles both outcomes correctly given a canned response
# shaped like a real run.

@pytest.mark.parametrize("product,existing_tool,gap_type", [
    ("Mobile approval flow for ApprovalMax users", "ApprovalMax", "FEATURE_GAP"),
    ("Budget-tier alternative to Trunk for micro-sellers", "Trunk", "PRICE_GAP"),
    ("Standalone tool for firms not on Buildium/AppFolio", "Buildium", "PLATFORM_GAP"),
])
def test_regression_a_gap_in_an_existing_tool_can_still_build(product, existing_tool, gap_type):
    payload = _with_floor(_base_payload(
        solution_concept=product,
        existing_tool={"name": existing_tool, "price": "$35/mo", "rating": 4.1, "review_count": 200, "source_url": "g2.com"},
        gap_type=gap_type,
        verdict="BUILD",
        timeline_classification="STRONG",
        month_floor_cleared=5,
    ), net_mrr_floor=4200)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "solution_concept": product}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"
    assert result["rejection_reason"] is None


def test_regression_a_well_served_market_with_no_gap_is_do_not_build():
    # The one legitimate way to DO_NOT_BUILD on competitor grounds alone:
    # all four of the no_saturation_checklist items are true (>4.3 stars,
    # no recurring complaint pattern, accessible pricing, no platform lock).
    payload = _base_payload(
        solution_concept="Yet another Calendly deposit-collection wrapper",
        existing_tool={"name": "Calendly", "price": "$12/mo", "rating": 4.7, "review_count": 5000, "source_url": "g2.com"},
        pain_confirmed=True,
        ongoing_complaints=False,
        verdict="DO_NOT_BUILD",
        failed_at_step=1,
        reason="Calendly's native deposit collection is well-reviewed and accessible -- no recurring complaint pattern found.",
        no_saturation_checklist={"rating_above_4_3": True, "no_recurring_complaint_pattern": True, "priced_accessibly": True, "no_platform_dependency": True},
    )
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "no recurring complaint pattern" in result["rejection_reason"]
