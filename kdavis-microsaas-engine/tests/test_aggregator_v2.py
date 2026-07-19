"""
Verdict Agent v3.0 tests. The live web-search-backed research quality
itself can only really be tuned by running real opportunities through it
— these tests cover the deterministic Python-side contract instead: JSON
extraction, status mapping, the price-adjusted floor enforcement, and the
exact regression scenarios given in prompt.md's REGRESSION TEST CASES
table, using canned LLM responses shaped like what a real run would
produce.

Kept as test_aggregator_v2.py (not renamed) since it's the same module
under test (agents/aggregator/agent.py) evolving in place, same as the
prompt itself went v2.0 -> v3.0 without a file rename.
"""
import json

import pytest

from agents.aggregator.agent import _extract_trailing_json, _price_adjusted_floor, run


def _canned(payload: dict, preamble: str = "Some research narrative before the final object.\n\n") -> str:
    return preamble + json.dumps(payload)


def _scenario(final_mrr_floor=4500, gate_clear=True, **overrides):
    s = {
        "capture_rate_pct": 0.5, "paying_accounts": 25, "price": 39, "gross_mrr": 975,
        "churn_haircut_pct": 20, "net_mrr": 780, "maintenance_deduction": 0,
        "final_mrr_floor": final_mrr_floor, "gate_clear": gate_clear,
    }
    s.update(overrides)
    return s


def _base_payload(**overrides) -> dict:
    payload = {
        "opportunity_id": "opp-1",
        "vertical": "E-commerce / Retail Ops",
        "solution_concept": "Test Product",
        "pain_valid": True,
        "icp": "Shopify merchants with 3-10 SKUs",
        "pain_stakes": "loses $500/mo in wasted ad spend",
        "pain_evidence": "r/shopify thread, 40 upvotes",
        "competitor_state": "CLEAR",
        "competitors_found": [],
        "platform_lock": None,
        "standalone_segment": None,
        "differentiation_thesis": None,
        "comp_set": [{"tool": "CompA", "price_raw": "$29/mo", "price_normalized_monthly": 29, "channel": "Shopify App Store", "source": "shopify.com/apps/compa"}],
        "adjusted_avg_price": 29,
        "proposed_price": 39,
        "price_tier": "$39-59",
        "price_adjusted_floor": 4000,
        "price_anchor_valid": True,
        "price_position": "above market by 34%",
        "maintenance_required": False,
        "maintenance_type": None,
        "maintenance_monthly_cost": 0,
        "tam_source": "Shopify merchant count, public filings",
        "tam_total": 4300000,
        "tam_funnel": [{"filter": "3-10 SKU merchants", "reasoning": "core ICP", "result": 500000}],
        "reachable_segment": 5000,
        "gtm_channel": "Shopify App Store listing + Shopify Partner referral program",
        "scenarios": {
            "floor": _scenario(final_mrr_floor=4500, gate_clear=True),
            "base": _scenario(final_mrr_floor=6500, gate_clear=True, capture_rate_pct=1),
            "stretch": _scenario(final_mrr_floor=12000, gate_clear=True, capture_rate_pct=2, catalyst="Shopify App Store 'Editor's Pick' feature"),
        },
        "time_to_floor": {"ramp_summary": "25 accounts/month for 6 months", "estimated_month": 6, "classification": "STRONG"},
        "marginal_pass": False,
        "marginal_risk_note": None,
        "verdict": "BUILD",
        "blocking_issues": [],
        "next_action": "Proceed to brief generation.",
        "resubmit_reason": None,
        "correction_required": None,
        "resubmit_conditions": None,
    }
    payload.update(overrides)
    return payload


def _with_floor(payload: dict, final_mrr_floor: float, gate_clear: bool = True) -> dict:
    """Overrides just scenarios.floor.final_mrr_floor for a payload built from _base_payload()."""
    payload = dict(payload)
    payload["scenarios"] = dict(payload["scenarios"])
    payload["scenarios"]["floor"] = _scenario(final_mrr_floor=final_mrr_floor, gate_clear=gate_clear)
    return payload


# ── JSON extraction ──────────────────────────────────────────────

def test_extracts_last_balanced_json_object_ignoring_narrative_braces():
    text = 'Notes: the tool costs {"placeholder": "not this one"} per docs.\n\n' + json.dumps({"verdict": "BUILD", "proposed_price": 39})
    result = _extract_trailing_json(text)
    assert result == {"verdict": "BUILD", "proposed_price": 39}


def test_raises_with_raw_text_when_nothing_parses():
    with pytest.raises(RuntimeError, match="did not return a parseable JSON"):
        _extract_trailing_json("I looked into this but ran out of tokens before finishing.")


# ── Price-adjusted floor (v3.0's core change) ──────────────────────

@pytest.mark.parametrize("price,expected_floor", [
    (19, 3500), (25, 3500), (29, 3500), (38.99, 3500),
    (39, 4000), (49, 4000), (59, 4000), (68.99, 4000),
    (69, 4500), (79, 4500), (99, 4500), (99.99, 4500),
    (100, 5000), (149, 5000), (250, 5000),
])
def test_price_adjusted_floor_matches_the_spec_table(price, expected_floor):
    assert _price_adjusted_floor(price) == expected_floor


def test_price_adjusted_floor_falls_back_to_4000_when_price_missing():
    # Not the $100+ ceiling -- an unreported price shouldn't get the
    # HARDEST floor by accident, it should get the v2.0-era default.
    assert _price_adjusted_floor(None) == 4000
    assert _price_adjusted_floor(0) == 4000


# ── Status mapping + code-level floor enforcement ──────────────────

def test_build_verdict_maps_to_ready_to_build():
    llm = lambda system, user: _canned(_base_payload(verdict="BUILD"))
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"
    assert result["rejection_reason"] is None


def test_conditional_verdict_maps_to_validated():
    llm = lambda system, user: _canned(_base_payload(verdict="CONDITIONAL", marginal_pass=True))
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "validated"


def test_do_not_build_maps_to_rejected_with_reason():
    llm = lambda system, user: _canned(_base_payload(
        verdict="DO_NOT_BUILD", blocking_issues=["MRR floor not cleared"], next_action="Do not build.",
    ))
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "MRR floor not cleared" in result["rejection_reason"]


def test_resubmit_maps_to_needs_correction_not_rejected():
    # Real bug found 2026-07-17: a live run's real estate opportunity had
    # no competitor but a capture-rate math error (applied to the 34,000-firm
    # TAM instead of the ~5,000-firm reachable segment) -- that's a fixable
    # error, not a dead opportunity, so it must not land in 'rejected'
    # (which the dashboard's Reject & Delete button archives-then-deletes).
    llm = lambda system, user: _canned(_base_payload(
        verdict="RESUBMIT",
        competitor_state="CLEAR",
        resubmit_reason="Capture rate applied to 34,000-firm TAM instead of reachable segment",
        correction_required="Recompute capture rate against the ~5,040-firm reachable segment",
        resubmit_conditions="Reconciled price across MRR math and tier_structure, corrected capture-rate base, named GTM channel",
    ))
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "needs_correction"
    assert result["status"] != "rejected"
    assert "Capture rate applied to 34,000-firm TAM" in result["rejection_reason"]
    assert "Recompute capture rate" in result["rejection_reason"]
    assert result["verdict_v2_output"]["resubmit_conditions"] == (
        "Reconciled price across MRR math and tier_structure, corrected capture-rate base, named GTM channel"
    )


def test_resubmit_with_no_reason_still_gets_a_readable_message():
    llm = lambda system, user: _canned(_base_payload(verdict="RESUBMIT", resubmit_reason=None, correction_required=None))
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "needs_correction"
    assert result["rejection_reason"]  # never blank/None -- always something readable


def test_code_level_floor_check_overrides_a_build_verdict_below_adjusted_floor():
    # Guards against the model saying BUILD with gate_clear=true but a
    # final_mrr_floor that doesn't actually clear the price-adjusted
    # floor — the one number this factory's business model depends on
    # must never be trusted from the model's self-report alone.
    payload = _with_floor(_base_payload(verdict="BUILD", proposed_price=39), final_mrr_floor=3200, gate_clear=True)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "Code-level floor check failed" in result["rejection_reason"]
    assert "$4,000" in result["rejection_reason"]  # $39/mo tier -> $4,000 floor, not the old flat check


def test_code_level_floor_uses_the_lower_price_adjusted_floor_for_cheap_products():
    # A $25/mo product only needs to clear $3,500, not $4,000 -- this must
    # NOT be code-level-rejected even though it's below the old flat floor.
    payload = _with_floor(_base_payload(verdict="BUILD", proposed_price=25), final_mrr_floor=3700, gate_clear=True)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"


def test_code_level_floor_uses_the_higher_price_adjusted_floor_for_premium_products():
    # A $120/mo product needs $5,000, not $4,000 -- this must be rejected
    # even though $4,200 would have cleared the old flat floor.
    payload = _with_floor(_base_payload(verdict="BUILD", proposed_price=120), final_mrr_floor=4200, gate_clear=True)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "$5,000" in result["rejection_reason"]


def test_code_level_floor_check_also_covers_conditional_not_just_build():
    # Real bug found 2026-07-19: a live v4.0 run returned verdict=CONDITIONAL
    # with final_mrr_floor at only 28% of the price-adjusted floor
    # (gate_clear: false) and time_to_floor still classified STRONG -- the
    # code-level floor check only ever guarded BUILD, so this internally
    # inconsistent verdict reached the DB and tripped the mrr_floor_check
    # constraint instead of being caught here first.
    payload = _with_floor(_base_payload(verdict="CONDITIONAL", proposed_price=69, marginal_pass=True), final_mrr_floor=1247, gate_clear=False)
    payload["time_to_floor"] = {"ramp_summary": "n/a", "estimated_month": 6, "classification": "STRONG"}
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "Code-level floor check failed" in result["rejection_reason"]


def test_conditional_below_floor_is_legitimate_when_time_to_floor_is_conditional_class():
    # The one spec-legitimate way for CONDITIONAL to clear below the raw
    # floor scenario: a named distribution partner projected to close the
    # gap within 13-18 months (time_to_floor.classification == "CONDITIONAL").
    # That classification is itself gated behind the model naming a real
    # partner in the prompt, so trusting the label here (not re-deriving
    # the partner claim) is a deliberate, narrow exception.
    payload = _with_floor(_base_payload(verdict="CONDITIONAL", proposed_price=69, marginal_pass=False), final_mrr_floor=3200, gate_clear=False)
    payload["time_to_floor"] = {"ramp_summary": "named partner ramp", "estimated_month": 15, "classification": "CONDITIONAL"}
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "validated"


def test_partial_competitor_state_can_still_build():
    # v3.0's core change: a platform-locked competitor no longer means
    # automatic death -- PARTIAL with an approved differentiation thesis
    # can still reach BUILD.
    llm = lambda system, user: _canned(_base_payload(
        verdict="BUILD",
        competitor_state="PARTIAL",
        platform_lock="Buildium/AppFolio only serve their own PM-suite subscribers",
        standalone_segment="QuickBooks-only property managers with no PM suite",
        differentiation_thesis="Standalone tool for firms that will never adopt a full PM suite",
    ))
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"


def test_full_verdict_v3_output_is_preserved_on_the_result():
    payload = _base_payload(verdict="BUILD")
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["verdict_v2_output"]["tam_source"] == "Shopify merchant count, public filings"
    assert result["verdict_v2_output"]["comp_set"][0]["tool"] == "CompA"
    assert result["verdict_v2_output"]["time_to_floor"]["classification"] == "STRONG"
    assert result["verdict_v2_output"]["price_adjusted_floor"] == 4000  # code-computed, stored back for auditability


# ── Regression cases from prompt.md's Part D REGRESSION TEST CASES ─
# These verify the Python-side handles both outcomes correctly given a
# canned response shaped like a real run — not that the live model will
# always search correctly, which only real runs against real data confirm.

@pytest.mark.parametrize("product,competitor,channel", [
    ("Shopify ad pause on stockout", "AdStockGuard", "Shopify App Store"),
    ("Shopify PO/invoice COGS match", "Settle", "Shopify App Store"),
    ("Legal court deadline calculator", "LawToolBox", "LawNext Directory, Capterra"),
    ("Real estate transaction tracker", "Trackxi", "Capterra, G2"),
    ("Multi-channel inventory sync", "Trunk", "Shopify App Store"),
])
def test_regression_competitor_saturated_halts(product, competitor, channel):
    payload = _base_payload(
        solution_concept=product,
        competitor_state="SATURATED",
        competitors_found=[{"name": competitor, "price": "$35/mo", "channel": channel, "rating_or_reviews": "4.8 stars"}],
        verdict="DO_NOT_BUILD",
        blocking_issues=[f"Competitor saturated: {competitor}"],
        next_action="Do not build -- standalone competitor already serves this market.",
    )
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "solution_concept": product}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert competitor in result["rejection_reason"]


@pytest.mark.parametrize("product,state", [
    ("1099 Compliance Manager", "CLEAR"),
    ("PM Owner Statement Automation", "PARTIAL"),
])
def test_regression_clear_or_partial_proceeds_to_build(product, state):
    overrides = {"solution_concept": product, "competitor_state": state, "verdict": "BUILD"}
    if state == "PARTIAL":
        overrides.update({
            "platform_lock": "Buildium/AppFolio only serve their own PM-suite subscribers",
            "standalone_segment": "QuickBooks-only property managers with no PM suite",
            "differentiation_thesis": "Standalone tool for firms that will never adopt a full PM suite",
        })
    payload = _base_payload(**overrides)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "solution_concept": product}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"
    assert result["rejection_reason"] is None
