"""
Verdict agent v5.0 — complete replacement of v2.0/v3.0/v4.0, July 2026.

22 real opportunities were evaluated across v2.0-v4.0 (6/6/10 respectively):
19 SATURATED, 2 PARTIAL that both failed on MRR math, 0 CLEAR, 0 genuine
RESUBMIT. The competitor-absence gate (CLEAR/PARTIAL/SATURATED) killed an
idea the instant ANY competitor existed, regardless of whether that
competitor was actually serving its users well. v5.0 retires that gate
entirely: Dispatch now anchors every idea on a NAMED existing tool people
are already using and already complaining about (via G2/Capterra/Reddit/
forum reviews), and Verdict asks only whether that competitor is failing
enough people to build a $4K MRR business around the specific gap.

Verdict still independently re-derives the unhappy segment, reachable
segment, and MRR math from scratch via live web search on every call
(core.llm_router.analyze_with_web_search) — it never trusts Dispatch's
submitted numbers outright, unchanged from v2.0-v4.0.

Full reasoning rules live in prompt.md (the v5.0 spec verbatim, plus an
appended OUTPUT CONTRACT). The model narrates its research before the
final JSON object; the contract instructs it to end with one consolidated
object covering every step's key fields.
"""
import json
from pathlib import Path
from typing import Callable, Optional

from core.llm_router import analyze_with_web_search

AGENT_ID = "aggregator-verdict-v5"

SYSTEM_PROMPT = (Path(__file__).parent / "prompt.md").read_text()

# Price-adjusted MRR floor — unchanged across v3.0-v5.0. Applied as
# half-open intervals at the spec's stated tier breakpoints ($19-29,
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
        return 4000  # no price reported — fall back to the standard floor, not the $100+ ceiling
    for ceiling, floor in _PRICE_TIER_FLOORS:
        if proposed_price < ceiling:
            return floor
    return _DEFAULT_FLOOR


# v5.0 has only three legal verdicts (BUILD | CONDITIONAL | DO_NOT_BUILD)
# — RESUBMIT is explicitly retired as a primary output (a malformed
# submission is DO_NOT_BUILD with the missing element named as the
# reason). Any unrecognized verdict value falls back to rejected via
# .get()'s default, same as before.
_VERDICT_TO_STATUS = {
    "BUILD": "READY_TO_BUILD",
    "CONDITIONAL": "validated",
    "DO_NOT_BUILD": "rejected",
}


def run(raw_findings: list[dict], llm: Callable[..., str] = analyze_with_web_search) -> list[dict]:
    """Evaluate every opportunity card through the full v5.0 research pipeline."""
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
    # model's own verdict alone for it. v5.0 flattens the MRR figure to a
    # single top-level net_mrr_floor (no more three-scenario nesting) —
    # fall back to the v3.0/v4.0 nested scenarios.floor.final_mrr_floor
    # shape, then the even older legacy top-level key, in case a response
    # doesn't follow the current contract exactly.
    final_floor = result.get("net_mrr_floor")
    if final_floor is None:
        final_floor = (result.get("scenarios") or {}).get("floor", {}).get("final_mrr_floor")
    if final_floor is None:
        final_floor = result.get("final_mrr_floor") or 0
    adjusted_floor = _price_adjusted_floor(result.get("proposed_price"))
    result["price_adjusted_floor"] = adjusted_floor  # store what was actually enforced, not just what the model claimed

    # v5.0 simplifies the floor rule: BUILD and CONDITIONAL both require
    # the floor to genuinely clear — they differ only in WHEN (month 1-7
    # vs 8-12), never in WHETHER. There is no more "floor doesn't clear
    # but might with a named partner" escape hatch for CONDITIONAL — that
    # exact ambiguity let a real v4.0 CONDITIONAL result reach the DB at
    # 28% of its target floor and trip the mrr_floor_check constraint.
    # Applying one uniform check to both statuses closes that gap by
    # construction instead of requiring a special-cased exception.
    if status in ("READY_TO_BUILD", "validated") and final_floor < adjusted_floor:
        status = "rejected"
        result["reason"] = (
            f"Code-level floor check failed: net_mrr_floor ${final_floor:,.0f} is below "
            f"the ${adjusted_floor:,.0f} price-adjusted floor despite verdict={verdict}. "
            f"v5.0 requires the floor to genuinely clear for both BUILD and CONDITIONAL — "
            f"only the timing differs."
        )

    if status == "rejected":
        rejection_reason = result.get("reason") or "Did not clear v5.0 gates — no reason returned."
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


def _summarize_existing_tool(result: dict) -> str:
    tool = result.get("existing_tool") or {}
    if not tool.get("name"):
        return "no existing_tool detail returned"
    return f"{tool.get('name')} ({tool.get('price')}, {tool.get('rating')} stars, {tool.get('review_count')} reviews)"


def _extract_trailing_json(text: str) -> dict:
    """
    Finds the LAST top-level balanced {...} span in the response text and
    parses it. The model narrates its research before the final contract
    object per the OUTPUT CONTRACT, so the final block is what matters —
    not the first brace encountered, and NOT simply the last '{' character
    in the text either: the contract object itself contains nested dicts
    (existing_tool, no_saturation_checklist), whose own closing braces
    would otherwise be mistaken for the outer object's end. Scanning depth
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
