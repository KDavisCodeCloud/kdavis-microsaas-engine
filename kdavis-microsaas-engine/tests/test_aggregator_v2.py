"""
Verdict Agent v2.0 tests. The live web-search-backed research quality
itself can only really be tuned by running real opportunities through it
(that's the "7 more products" validation pass) — these tests cover the
deterministic Python-side contract instead: JSON extraction, status
mapping, the code-level $4K floor enforcement, and the exact regression
scenarios given in prompt.md's REGRESSION TEST CASES table, using canned
LLM responses shaped like what a real run would produce.
"""
import json

import pytest

from agents.aggregator.agent import _extract_trailing_json, run


def _canned(payload: dict, preamble: str = "Some research narrative before the final object.\n\n") -> str:
    return preamble + json.dumps(payload)


def _base_payload(**overrides) -> dict:
    payload = {
        "opportunity_id": "opp-1",
        "vertical": "E-commerce / Retail Ops",
        "solution_concept": "Test Product",
        "pain_valid": True,
        "icp": "Shopify merchants with 3-10 SKUs",
        "pain_stakes": "loses $500/mo in wasted ad spend",
        "pain_evidence": "r/shopify thread, 40 upvotes",
        "competitor_check": "CLEAR",
        "halt": False,
        "competitors_found": [],
        "differentiation_thesis": None,
        "comp_set": [{"tool": "CompA", "price_raw": "$29/mo", "price_normalized_monthly": 29, "channel": "Shopify App Store", "source": "shopify.com/apps/compa"}],
        "adjusted_avg_price": 29,
        "proposed_price": 39,
        "price_position": "above market by 34%",
        "maintenance_required": False,
        "maintenance_type": None,
        "maintenance_monthly_cost": 0,
        "tam_source": "Shopify merchant count, public filings",
        "tam_total": 4300000,
        "tam_funnel": [{"filter": "3-10 SKU merchants", "reasoning": "core ICP", "result": 500000}],
        "reachable_segment": 5000,
        "capture_rate_pct": 1,
        "paying_accounts_floor": 50,
        "gross_mrr_floor": 1950,
        "churn_haircut_pct": 20,
        "net_mrr_floor": 1560,
        "final_mrr_floor": 4500,
        "mrr_floor_gate_clear": True,
        "marginal_pass": False,
        "marginal_risk_note": None,
        "verdict": "BUILD",
        "blocking_issues": [],
        "next_action": "Proceed to brief generation.",
    }
    payload.update(overrides)
    return payload


# ── JSON extraction ──────────────────────────────────────────────

def test_extracts_last_balanced_json_object_ignoring_narrative_braces():
    text = 'Notes: the tool costs {"placeholder": "not this one"} per docs.\n\n' + json.dumps({"verdict": "BUILD", "final_mrr_floor": 5000})
    result = _extract_trailing_json(text)
    assert result == {"verdict": "BUILD", "final_mrr_floor": 5000}


def test_raises_with_raw_text_when_nothing_parses():
    with pytest.raises(RuntimeError, match="did not return a parseable JSON"):
        _extract_trailing_json("I looked into this but ran out of tokens before finishing.")


# ── Status mapping + code-level floor enforcement ──────────────────

def test_build_verdict_maps_to_ready_to_build():
    llm = lambda system, user: _canned(_base_payload(verdict="BUILD", final_mrr_floor=5000))
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"
    assert result["rejection_reason"] is None


def test_conditional_verdict_maps_to_validated():
    llm = lambda system, user: _canned(_base_payload(verdict="CONDITIONAL", final_mrr_floor=4200, marginal_pass=True))
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
        halt=False,
        competitor_check="CLEAR",
        final_mrr_floor=600,
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
    llm = lambda system, user: _canned(_base_payload(verdict="RESUBMIT", final_mrr_floor=1000, resubmit_reason=None, correction_required=None))
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "needs_correction"
    assert result["rejection_reason"]  # never blank/None -- always something readable


def test_code_level_floor_check_overrides_a_build_verdict_below_4000():
    # Guards against the model saying BUILD with gate_clear=true but a
    # final_mrr_floor that doesn't actually clear $4,000 — the one number
    # this factory's business model depends on must never be trusted from
    # the model's self-report alone.
    llm = lambda system, user: _canned(_base_payload(verdict="BUILD", final_mrr_floor=3200, mrr_floor_gate_clear=True))
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert "Code-level floor check failed" in result["rejection_reason"]


def test_full_verdict_v2_output_is_preserved_on_the_result():
    payload = _base_payload(verdict="BUILD", final_mrr_floor=6000)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1"}], llm=llm)[0]
    assert result["verdict_v2_output"]["tam_source"] == "Shopify merchant count, public filings"
    assert result["verdict_v2_output"]["comp_set"][0]["tool"] == "CompA"


# ── Regression cases from prompt.md's REGRESSION TEST CASES table ──
# These verify the Python-side handles both outcomes correctly given a
# canned response shaped like a real run — not that the live model will
# always search correctly, which only real runs against real data can
# confirm (the "7 more products" tuning pass).

@pytest.mark.parametrize("product,competitor,channel", [
    ("Shopify ad pause on stockout", "AdStockGuard", "Shopify App Store"),
    ("Shopify PO/invoice COGS match", "Settle", "Shopify App Store"),
    ("Legal court deadline calculator", "LawToolBox", "LawNext Directory, Capterra"),
    ("Real estate transaction tracker", "Trackxi", "Capterra, G2"),
    ("Multi-channel inventory sync", "Trunk", "Shopify App Store"),
])
def test_regression_competitor_exists_halts(product, competitor, channel):
    payload = _base_payload(
        solution_concept=product,
        competitor_check="EXISTS",
        halt=True,
        competitors_found=[{"name": competitor, "price": "$35/mo", "channel": channel, "rating_or_reviews": "4.8 stars"}],
        verdict="DO_NOT_BUILD",
        blocking_issues=[f"Competitor exists: {competitor}"],
        next_action="Submit a differentiation thesis to resume, or reject.",
    )
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "solution_concept": product}], llm=llm)[0]
    assert result["status"] == "rejected"
    assert competitor in result["rejection_reason"]


@pytest.mark.parametrize("product", [
    "1099 Compliance Manager",
    "PM Owner Statement Automation",
])
def test_regression_clears_competitor_check_and_proceeds(product):
    payload = _base_payload(solution_concept=product, competitor_check="CLEAR", halt=False, verdict="BUILD", final_mrr_floor=5000)
    llm = lambda system, user: _canned(payload)
    result = run([{"opportunity_id": "opp-1", "solution_concept": product}], llm=llm)[0]
    assert result["status"] == "READY_TO_BUILD"
    assert result["rejection_reason"] is None
