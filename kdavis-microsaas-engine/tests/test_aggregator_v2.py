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


# ── Pre-Verdict prefilter (2026-07-20, Kelvin's rule — tokens were being
# wasted on obvious misses). Dispatch's own self-reported
# conservative_mrr_potential gates whether Verdict's web-search call runs
# at all; distinct from the post-Verdict hard gate below, which uses
# Verdict's own independently-computed net_mrr_floor instead. ─────────

def test_prefilter_skips_the_verdict_call_when_dispatch_own_number_is_below_3500():
    calls = []

    def llm(system, user):
        calls.append(1)
        return _canned(_base_payload(verdict="BUILD", confidence_score=90), )

    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 900}], llm=llm)[0]
    assert result["status"] == "killed_below_floor"
    assert "Pre-filter" in result["rejection_reason"]
    assert "$900" in result["rejection_reason"]
    assert calls == []  # the expensive web-search call must never have run


def test_prefilter_lets_a_plausible_submission_through_to_verdict():
    calls = []

    def llm(system, user):
        calls.append(1)
        return _canned(_with_floor(_base_payload(verdict="BUILD", confidence_score=80), net_mrr_floor=4500))

    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"
    assert calls == [1]


# ── Verdict -> status mapping (2026-07-20: only READY_TO_BUILD/validated/
# watch/killed_below_floor are legal now -- final_floor + confidence_score
# decide status purely at the code level, the model's own BUILD/
# CONDITIONAL/DO_NOT_BUILD verdict label is no longer authoritative for
# status, only informational context. See agents/aggregator/agent.py's
# "Dashboard-visibility gate" comment for the full rationale.) ──────

def test_build_verdict_with_high_floor_and_high_confidence_maps_to_ready():
    payload = _with_floor(_base_payload(verdict="BUILD", timeline_classification="STRONG", confidence_score=80), net_mrr_floor=4500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"
    assert result["rejection_reason"] is None


def test_floor_in_3500_to_4000_band_is_conditional_regardless_of_confidence():
    # Kelvin's rule: CONDITIONAL is $3,500-$4,000 OR confidence 65-74 --
    # an OR, so landing in the MRR band is conditional even with a strong
    # confidence score.
    payload = _with_floor(_base_payload(verdict="CONDITIONAL", timeline_classification="PASS", month_floor_cleared=10, confidence_score=90), net_mrr_floor=3800)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "validated"


def test_confidence_in_65_to_74_band_is_conditional_even_with_high_floor():
    payload = _with_floor(_base_payload(verdict="BUILD", confidence_score=70), net_mrr_floor=6000)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "validated"


def test_high_floor_with_low_confidence_falls_to_watch_not_rejected():
    # Clears the money easily but confidence is too low for a clean pass --
    # this used to be a straight reject; now it's surfaced as watch so
    # Kelvin can judge the specific risk himself instead of losing it.
    payload = _with_floor(_base_payload(verdict="BUILD", confidence_score=40), net_mrr_floor=6000)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "watch"
    assert "WATCH" in result["rejection_reason"]


def test_missing_confidence_score_with_high_floor_falls_to_watch():
    # No confidence_score at all can't be verified against the >=75 READY
    # bar -- must not default to READY just because it's absent.
    payload = _with_floor(_base_payload(verdict="BUILD"), net_mrr_floor=6000)
    assert "confidence_score" not in payload or payload.get("confidence_score") is None
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "watch"


def test_do_not_build_that_still_clears_3500_becomes_watch_not_rejected():
    # A model DO_NOT_BUILD for a qualitative reason no longer silently
    # disappears if the money genuinely clears the absolute floor -- it's
    # exactly the "clears the math but has a specific named risk" case
    # WATCH exists for.
    payload = _with_floor(_base_payload(
        verdict="DO_NOT_BUILD", failed_at_step=1, confidence_score=55,
        reason="Existing tool serves this segment adequately; risk the incumbent ships this within 6 months.",
    ), net_mrr_floor=5000)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "watch"
    assert "incumbent ships this within 6 months" in result["rejection_reason"]


def test_floor_below_3500_is_killed_below_floor_never_rejected():
    # Kelvin's hard MRR gate (2026-07-20): anything that can't clear the
    # absolute $3,500 floor never reaches the dashboard at all -- not even
    # as a visible 'rejected' row. status is killed_below_floor, a value
    # node_write_pipeline diverts straight to the rejection archive.
    llm = lambda system, user: _canned(_base_payload(
        verdict="DO_NOT_BUILD", failed_at_step=3, confidence_score=42,
        reason="Net MRR floor never clears within 12 months.",
    ), )
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "killed_below_floor"
    assert "Hard MRR gate" in result["rejection_reason"]


def test_unrecognized_verdict_with_floor_below_3500_is_still_killed():
    # RESUBMIT is retired as a primary verdict in v5.0 -- if the model
    # emits it anyway, the numeric gate still applies regardless of the
    # unrecognized label.
    llm = lambda system, user: _canned(_base_payload(verdict="RESUBMIT", reason="Missing evidence source.", confidence_score=30))
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "killed_below_floor"


# ── Code-level floor enforcement (v5.0: uniform across BUILD + CONDITIONAL) ──
# The per-row price_adjusted_floor check still runs and still sets an
# intermediate 'rejected'/reason -- but the final dashboard-visibility
# gate below always has the last word, so these now assert the ultimate
# outcome (killed_below_floor if it's also under the $3,500 absolute
# floor, watch if it clears $3,500 but not its own tier).

def test_code_level_floor_check_below_absolute_floor_is_killed():
    # $3,200 fails both its own $4,000 tier floor AND the $3,500 absolute
    # floor -- killed, not merely watched.
    payload = _with_floor(_base_payload(verdict="BUILD", proposed_price=39, confidence_score=80), net_mrr_floor=3200)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "killed_below_floor"


def test_code_level_floor_check_above_absolute_floor_but_below_own_tier_is_watch():
    # $4,200 fails its own $5,000 tier floor (a $120/mo product) but
    # clears the $3,500 absolute floor -- must surface as watch, not
    # disappear, per the "only 3 states, everything >= $3,500 is shown" rule.
    payload = _with_floor(_base_payload(verdict="BUILD", proposed_price=120, confidence_score=80), net_mrr_floor=4200)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "watch"
    assert "$5,000" in result["rejection_reason"]


def test_code_level_floor_uses_the_lower_price_adjusted_floor_for_cheap_products():
    # A $25/mo product's own tier floor is $3,500, not $4,000 -- clearing
    # ITS OWN floor with a qualifying confidence score is a clean READY,
    # not a reject or a judgment call. (READY checks against adjusted_floor,
    # not a flat $4,000 -- see the "own tier" test below for why.)
    payload = _with_floor(_base_payload(verdict="BUILD", proposed_price=25, confidence_score=80), net_mrr_floor=3700)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"


def test_full_verdict_v5_output_is_preserved_on_the_result():
    payload = _with_floor(_base_payload(verdict="BUILD", confidence_score=80), net_mrr_floor=4500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["verdict_v2_output"]["existing_tool"]["name"] == "ApprovalMax"
    assert result["verdict_v2_output"]["gap_type"] == "FEATURE_GAP"
    assert result["verdict_v2_output"]["price_adjusted_floor"] == 4000  # code-computed, stored back for auditability


# ── Hard MRR gate + READY/CONDITIONAL/WATCH bands (2026-07-20) ──────
# Replaces the old confidence-override thresholds (hard-reject <45,
# downgrade <60) -- see agents/aggregator/agent.py's "Dashboard-
# visibility gate" comment. Kept the same section anchor name
# ("confidence score") since these are still fundamentally about how
# confidence_score affects status.

def test_confidence_below_65_with_high_floor_is_watch_not_rejected():
    payload = _with_floor(_base_payload(verdict="BUILD", confidence_score=30), net_mrr_floor=4500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "watch"


def test_confidence_65_to_74_downgrades_high_floor_build_to_conditional():
    payload = _with_floor(_base_payload(verdict="BUILD", confidence_score=68), net_mrr_floor=4500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "validated"


def test_confidence_score_never_rescues_a_floor_that_fails_the_absolute_gate():
    # A perfect confidence score must not rescue a result whose real
    # net_mrr_floor can't clear even the $3,500 absolute floor.
    payload = _with_floor(_base_payload(verdict="DO_NOT_BUILD", confidence_score=95, reason="Floor never clears."), net_mrr_floor=500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "killed_below_floor"


def test_confidence_75_plus_with_floor_above_4000_is_ready():
    payload = _with_floor(_base_payload(verdict="BUILD", confidence_score=82), net_mrr_floor=4500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"


def test_confidence_score_absent_does_not_default_to_ready():
    # A response that never mentions confidence_score at all (e.g. an
    # older-shaped canned test, or a real result that predates the
    # confidence-score system) can't be verified against the >=75 READY
    # bar -- it falls to watch, not a free pass.
    payload = _with_floor(_base_payload(verdict="BUILD"), net_mrr_floor=4500)
    assert "confidence_score" not in payload or payload.get("confidence_score") is None
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "watch"


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
        confidence_score=80,
    ), net_mrr_floor=4200)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "solution_concept": product, "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"
    assert result["rejection_reason"] is None


def test_regression_a_well_served_market_with_no_gap_is_killed_below_floor():
    # The one legitimate way to DO_NOT_BUILD on competitor grounds alone:
    # all four of the no_saturation_checklist items are true (>4.3 stars,
    # no recurring complaint pattern, accessible pricing, no platform lock).
    # With no gap, the model reports no real net_mrr_floor either -- this
    # now lands as killed_below_floor (never a visible dashboard row),
    # not the old 'rejected'.
    payload = _base_payload(
        solution_concept="Yet another Calendly deposit-collection wrapper",
        existing_tool={"name": "Calendly", "price": "$12/mo", "rating": 4.7, "review_count": 5000, "source_url": "g2.com"},
        pain_confirmed=True,
        ongoing_complaints=False,
        verdict="DO_NOT_BUILD",
        failed_at_step=1,
        confidence_score=25,
        reason="Calendly's native deposit collection is well-reviewed and accessible -- no recurring complaint pattern found.",
        no_saturation_checklist={"rating_above_4_3": True, "no_recurring_complaint_pattern": True, "priced_accessibly": True, "no_platform_dependency": True},
    )
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "killed_below_floor"


# ── Step 2.5 hard stops (2026-08-15, Kelvin's rule) -- code-level
# enforcement independent of what the model's own verdict said, same
# "never trust the model's self-report alone" principle as the MRR floor
# check. Permanent -- never loosened by any active recalibration. ──────

def test_api_capability_hard_stop_forces_rejected_despite_build_verdict():
    payload = _with_floor(_base_payload(
        verdict="BUILD", timeline_classification="STRONG", confidence_score=90,
        api_capability_failed=True, step_2_5_failure_reason="API_CANNOT_PERFORM_CORE_ACTION",
    ), net_mrr_floor=8000)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 8000}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "hard stop" in result["rejection_reason"].lower()


def test_third_party_approval_gate_hard_stop_forces_rejected():
    payload = _with_floor(_base_payload(
        verdict="CONDITIONAL", timeline_classification="PASS", confidence_score=70,
        third_party_approval_gate_failed=True, step_2_5_failure_reason="THIRD_PARTY_APPROVAL_GATE",
    ), net_mrr_floor=4200)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "rejected"


def test_mid_acquisition_platform_hard_stop_forces_rejected():
    payload = _with_floor(_base_payload(
        verdict="BUILD", timeline_classification="STRONG", confidence_score=85,
        mid_acquisition_platform_failed=True, step_2_5_failure_reason="MID_ACQUISITION_PLATFORM",
    ), net_mrr_floor=6000)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 6000}], llm=llm)[0]
    assert result["status"] == "rejected"


def test_multiple_oauth_dependencies_hard_stop_forces_rejected():
    # Added 2026-08-23 alongside prompt.md's Step 2.5 hard-gate restructure --
    # matches the exact same enforcement pattern as the other three hard
    # stops above (never trust the model's own verdict alone).
    payload = _with_floor(_base_payload(
        verdict="BUILD", timeline_classification="STRONG", confidence_score=88,
        multiple_oauth_dependencies_failed=True, integration_dependency_count=2,
        step_2_5_failure_reason="MULTIPLE_OAUTH_DEPENDENCIES",
    ), net_mrr_floor=7000)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 7000}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "hard stop" in result["rejection_reason"].lower()


def test_no_hard_stop_failure_does_not_affect_a_clean_build():
    payload = _with_floor(_base_payload(
        verdict="BUILD", timeline_classification="STRONG", confidence_score=90,
        api_capability_failed=False, third_party_approval_gate_failed=False, mid_acquisition_platform_failed=False,
        multiple_oauth_dependencies_failed=False,
    ), net_mrr_floor=8000)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 8000}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"


# ── Active pipeline-health recalibration injection (2026-08-15) ────────

def test_active_verdict_recalibration_is_prepended_to_system_prompt(monkeypatch):
    import agents.aggregator.agent as agg_module

    monkeypatch.setattr(agg_module.pipeline_health, "get_active_recalibration_text", lambda target: "RECALIBRATION MARKER TEXT")

    captured = {}

    def llm(system, user):
        captured["system"] = system
        return _canned(_with_floor(_base_payload(verdict="BUILD", confidence_score=80), net_mrr_floor=4500))

    run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)
    assert "RECALIBRATION MARKER TEXT" in captured["system"]


def test_no_active_recalibration_leaves_system_prompt_unchanged(monkeypatch):
    # prompt.md's own static text legitimately documents the recalibration
    # mechanism (mentions the word "RECALIBRATION"), so this checks for the
    # absence of an actually-injected marker block, not the bare word.
    import agents.aggregator.agent as agg_module

    monkeypatch.setattr(agg_module.pipeline_health, "get_active_recalibration_text", lambda target: "")

    captured = {}

    def llm(system, user):
        captured["system"] = system
        return _canned(_with_floor(_base_payload(verdict="BUILD", confidence_score=80), net_mrr_floor=4500))

    run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)
    assert "RECALIBRATION MARKER TEXT" not in captured["system"]


def test_recalibration_lookup_failure_does_not_block_evaluation(monkeypatch):
    import agents.aggregator.agent as agg_module

    def raise_error(target):
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr(agg_module.pipeline_health, "get_active_recalibration_text", raise_error)

    payload = _with_floor(_base_payload(verdict="BUILD", confidence_score=80), net_mrr_floor=4500)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "conservative_mrr_potential": 5000}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"  # never raises into the main eval path
