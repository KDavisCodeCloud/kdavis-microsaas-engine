"""
Verdict agent v3.0 — Consolidated from an 8-Model Audit + Post-Audit
Refinements, July 2026.

Replaces v2.0. v2.0 replaced the original deterministic Python gate-checker
(which never actually called an LLM — prompt.md was loaded but confirmed
dead code) with a genuinely research-backed agent, using Sonnet + Anthropic's
server-side web_search tool via core.llm_router.analyze_with_web_search.
That part is unchanged in v3.0.

v3.0's own premise: v2.0 ran 6 real opportunities and got 6 straight
rejections — 0 BUILD, 0 CONDITIONAL, 0 RESUBMIT. The flat $4K floor and
binary competitor gate (any competitor at all = death) were too tight,
independent of research quality. v3.0 replaces the binary competitor
check with a three-state gate (CLEAR / PARTIAL / SATURATED — PARTIAL lets
through opportunities where an existing tool only serves users already on
some other broader platform) and replaces the flat floor with a
price-adjusted one (lower-priced, larger-TAM products get a lower floor).

Full reasoning rules live in prompt.md (the v3.0 spec verbatim, plus an
appended OUTPUT CONTRACT). The spec's per-step output format is a mix of
labeled text blocks, which isn't reliably parseable on its own — the
contract instructs the model to end with one consolidated JSON object
covering every step's key fields instead.
"""
import json
from pathlib import Path
from typing import Callable, Optional

from core.llm_router import analyze_with_web_search

AGENT_ID = "aggregator-verdict-v3"

SYSTEM_PROMPT = (Path(__file__).parent / "prompt.md").read_text()

# Price-adjusted MRR floor (v3.0) — replaces the flat $4,000 floor. Applied
# as half-open intervals at the spec's stated tier breakpoints ($19-29,
# $39-59, $69-99, $100+) since the spec itself leaves the $30-38/$60-68/
# $99-100 gaps undefined; each gap is folded into the tier below it.
_PRICE_TIER_FLOORS = [
    (39, 3500),   # < $39/mo
    (69, 4000),   # $39 - $68.99/mo
    (100, 4500),  # $69 - $99.99/mo
]
_DEFAULT_FLOOR = 5000  # >= $100/mo, and the fallback when price is missing/None


def _price_adjusted_floor(proposed_price: Optional[float]) -> int:
    if not proposed_price:
        return 4000  # no price reported — fall back to the v2.0 standard floor, not the $100+ ceiling
    for ceiling, floor in _PRICE_TIER_FLOORS:
        if proposed_price < ceiling:
            return floor
    return _DEFAULT_FLOOR


# v3.0's VERDICT enum maps onto the pipeline's existing status vocabulary
# (READY_TO_BUILD/validated/rejected/needs_correction) — unchanged from
# v2.0's mapping. RESUBMIT still maps to 'needs_correction', not 'rejected'
# — the dashboard's Reject & Delete button archives-then-deletes rejected
# rows, which would destroy a genuinely fixable opportunity instead of
# surfacing the correction needed.
_VERDICT_TO_STATUS = {
    "BUILD": "READY_TO_BUILD",
    "CONDITIONAL": "validated",
    "DO_NOT_BUILD": "rejected",
    "RESUBMIT": "needs_correction",
}


def run(raw_findings: list[dict], llm: Callable[..., str] = analyze_with_web_search) -> list[dict]:
    """Evaluate every opportunity card through the full v3.0 research pipeline."""
    return [_evaluate(opp, llm) for opp in raw_findings]


def _evaluate(opp: dict, llm: Callable[..., str]) -> dict:
    user_input = json.dumps(opp, default=str)
    raw_response = llm(SYSTEM_PROMPT, user_input)
    result = _extract_trailing_json(raw_response)

    verdict = result.get("verdict", "DO_NOT_BUILD")
    status = _VERDICT_TO_STATUS.get(verdict, "rejected")

    # Hard-enforce the price-adjusted floor at the code level too —
    # CLAUDE.md's floor rule is non-negotiable, and this is the one number
    # the whole factory's business model depends on. Never trust the
    # model's own verdict/gate_clear alone for it. v3.0 nests the actual
    # number under scenarios.floor.final_mrr_floor (three scenarios are
    # now required); fall back to a legacy top-level final_mrr_floor key
    # in case a response doesn't nest perfectly.
    floor_scenario = (result.get("scenarios") or {}).get("floor") or {}
    final_floor = floor_scenario.get("final_mrr_floor")
    if final_floor is None:
        final_floor = result.get("final_mrr_floor") or 0
    adjusted_floor = _price_adjusted_floor(result.get("proposed_price"))
    result["price_adjusted_floor"] = adjusted_floor  # store what was actually enforced, not just what the model claimed

    if status == "READY_TO_BUILD" and final_floor < adjusted_floor:
        status = "rejected"
        result["blocking_issues"] = (result.get("blocking_issues") or []) + [
            f"Code-level floor check failed: final_mrr_floor ${final_floor:,.0f} is below "
            f"the ${adjusted_floor:,.0f} price-adjusted floor despite verdict={verdict}."
        ]

    if status == "rejected" and result.get("competitor_state") == "SATURATED":
        rejection_reason = f"Competitor saturated: {_summarize_competitors(result)}"
    elif status == "rejected":
        rejection_reason = "; ".join(result.get("blocking_issues") or [result.get("next_action") or "Did not clear v3.0 gates."])
    elif status == "needs_correction":
        rejection_reason = _summarize_resubmit(result)
    else:
        rejection_reason = None

    return {
        "opportunity_id":    opp.get("opportunity_id"),
        "vertical":          result.get("vertical") or opp.get("vertical", ""),
        "solution_concept":  result.get("solution_concept") or opp.get("solution_concept", ""),
        "status":            status,
        "rejection_reason":  rejection_reason,
        "verdict_v2_output": result,
    }


def _summarize_competitors(result: dict) -> str:
    comps = result.get("competitors_found") or []
    if not comps:
        return "saturated, no competitor detail returned"
    return "; ".join(f"{c.get('name')} ({c.get('price')}, {c.get('channel')})" for c in comps)


def _summarize_resubmit(result: dict) -> str:
    reason = result.get("resubmit_reason") or "Fixable error found — see verdict_v2_output."
    correction = result.get("correction_required")
    return f"Needs correction: {reason}" + (f" — {correction}" if correction else "")


def _extract_trailing_json(text: str) -> dict:
    """
    Finds the LAST top-level balanced {...} span in the response text and
    parses it. The model narrates its research before the final contract
    object per the OUTPUT CONTRACT, so the final block is what matters —
    not the first brace encountered, and NOT simply the last '{' character
    in the text either: the contract object itself contains nested dicts
    (comp_set entries, tam_funnel entries), whose own closing braces would
    otherwise be mistaken for the outer object's end. Scanning depth
    across the whole string and recording every span where depth returns
    to zero correctly identifies each *complete* top-level object,
    nested content included — the last one is the one we want.

    Raises (with the raw text attached) rather than silently returning a
    default/empty result if nothing parses, so a malformed response
    surfaces as a loud failure, not a false REJECT.
    """
    spans = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    spans.append((start, i))

    for start, end in reversed(spans):
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            continue  # this span wasn't valid JSON on its own — try an earlier one

    raise RuntimeError(f"Verdict agent did not return a parseable JSON object. Raw response (truncated): {text[:2000]}")
