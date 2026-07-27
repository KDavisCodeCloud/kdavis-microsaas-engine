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
import logging
from pathlib import Path
from typing import Callable, Optional

from core.llm_router import HAIKU, analyze_with_web_search
from rag.retriever import _retrieve_similar_outcomes_sync, format_rag_context_block
from rag.embedder import embed_and_insert

log = logging.getLogger(__name__)

AGENT_ID = "aggregator-verdict-v5"

SYSTEM_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


def _default_llm(system: str, user: str) -> str:
    """
    Haiku, not Sonnet (2026-07-19 cost pass) — v5.0's three-step evaluation
    plus the confidence-score step below is explicit enough that Haiku
    follows it reliably; the earlier versions' ambiguous multi-step
    frameworks were the actual reason Sonnet was load-bearing, not the
    web-search-driven fact-finding itself. Verified against real batches
    before going live (see MSE-Build-Order.md's Haiku regression notes),
    same bar as any other model swap on a live-search-backed agent.
    """
    return analyze_with_web_search(system, user, model=HAIKU)

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

# Absolute hard MRR floor (2026-07-20, Kelvin's rule) — the lowest tier
# floor in _PRICE_TIER_FLOORS above, reused as a universal minimum: nothing
# in this factory is worth a dashboard row (or a full Verdict web-search
# call) below what even the cheapest price band would require. See
# _prefilter_reject and _evaluate's dashboard-visibility gate below.
_HARD_MRR_FLOOR = 3500


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


def _prefilter_reject(opp: dict) -> Optional[dict]:
    """
    Cheap gate before spending a full Verdict web-search call (2026-07-20,
    Kelvin's rule — tokens were being wasted on obvious misses). Dispatch's
    own self-reported conservative_mrr_potential is the most favorable
    number this idea will ever have — Verdict only ever independently
    re-derives the same or a lower real number, never a higher one (see
    this module's own header docs). If even Dispatch's own optimistic
    estimate can't clear the $3,500 absolute floor, it is not worth a full
    Verdict run. Returns None (no prefilter hit) if the submission should
    proceed to the real evaluation.
    """
    try:
        submitted_mrr = float(opp.get("conservative_mrr_potential") or 0)
    except (TypeError, ValueError):
        submitted_mrr = 0.0
    if submitted_mrr >= _HARD_MRR_FLOOR:
        return None

    rejection_reason = (
        f"Pre-filter: Dispatch's own submitted conservative_mrr_potential "
        f"(${submitted_mrr:,.0f}) is below the ${_HARD_MRR_FLOOR:,.0f} absolute floor — "
        f"killed before spending a Verdict web-search call."
    )
    # RAG write for prefilter rejections too — "all verdicts" per spec, not
    # just ones that reached a real Verdict web-search call. Same
    # non-blocking try/except as _evaluate's own RAG write.
    try:
        embed_and_insert({
            "product_name":       opp.get("solution_concept", ""),
            "vertical":           opp.get("vertical", ""),
            "verdict":            "DO_NOT_BUILD",
            "confidence_score":   None,
            "projected_mrr_floor": int(submitted_mrr),
            "primary_risk_flag":  "MRR floor, prefiltered",
            "competitor_signals": opp.get("existing_tool"),
            "pain_themes":        [opp.get("pain_point", "")] if opp.get("pain_point") else [],
            "rationale":          rejection_reason,
        })
    except Exception as e:
        log.warning("[%s] RAG outcome write failed on prefilter reject (non-blocking): %s", AGENT_ID, e)

    return {
        "opportunity_id":    opp.get("opportunity_id"),
        "vertical":          opp.get("vertical", ""),
        "solution_concept":  opp.get("solution_concept", ""),
        "status":            "killed_below_floor",
        "rejection_reason":  rejection_reason,
        "verdict_v2_output": None,
    }


def run(raw_findings: list[dict], llm: Callable[..., str] = _default_llm) -> list[dict]:
    """Evaluate every opportunity card through the full v5.0 research pipeline."""
    return [_prefilter_reject(opp) or _evaluate(opp, llm) for opp in raw_findings]


def _evaluate(opp: dict, llm: Callable[..., str]) -> dict:
    # RAG retrieval is a soft input, never a gate (2026-07-27) -- any
    # failure here (no embedding key configured, DB error, etc.) falls
    # through to rag_context = "" and Verdict scores exactly as it did
    # before this existed. Never allowed to raise into the main eval path.
    try:
        rag_query = {
            "product_name": opp.get("solution_concept", ""),
            "vertical": opp.get("vertical", ""),
            "pain_themes": [opp.get("pain_point", "")] if opp.get("pain_point") else [],
            "competitor_names": [c.get("name") for c in (opp.get("competitor_examples") or []) if c.get("name")],
        }
        similar = _retrieve_similar_outcomes_sync(rag_query, top_k=5)
    except Exception as e:
        log.warning("[%s] RAG retrieval failed, continuing without it: %s", AGENT_ID, e)
        similar = []
    rag_block = format_rag_context_block(similar)

    # Spec requires the RAG block appear BEFORE Verdict's scoring
    # instructions, not after -- prepended to the system prompt, not the
    # user input. Note: this varies the system prompt per call whenever
    # rag_block is non-empty, which defeats analyze_with_web_search's
    # prompt-caching optimization (cache_control: ephemeral) for that call
    # specifically -- an accepted, deliberate cost tradeoff for surfacing
    # real factory history, not an oversight.
    system_prompt = f"{rag_block}\n\n{SYSTEM_PROMPT}" if rag_block else SYSTEM_PROMPT

    user_input = json.dumps(opp, default=str)
    raw_response = llm(system_prompt, user_input)
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

    confidence_score = result.get("confidence_score")

    # Dashboard-visibility gate (2026-07-20, Kelvin's rule — obvious misses
    # were reaching the dashboard and wasting review time). Whatever the
    # model's own verdict/floor-check/confidence landed on above, only
    # three states are allowed to reach opportunity_pipeline from here:
    # READY_TO_BUILD, validated (CONDITIONAL), or watch. This replaces the
    # old confidence-override thresholds (hard-reject <45, downgrade <60)
    # entirely — final_floor and confidence_score are now the only two
    # numbers that decide status, purely at the code level, same "never
    # trust the model's self-report alone" principle as the floor check
    # above, just carried one step further than before.
    #
    # $3,500 is not a guessed number — it is _PRICE_TIER_FLOORS' own lowest
    # tier floor (the cheapest price band this factory will ever build
    # for), so "can this idea plausibly clear $3,500 under any realistic
    # scenario" is already the most lenient bar anything in this system can
    # be held to. Anything that fails even that is killed here — archived
    # to opportunity_pipeline_rejections (by node_write_pipeline) for the
    # tuning signal, never inserted into the live dashboard table.
    #
    # Anything that clears $3,500 must be shown, per Kelvin's "only 3
    # states" rule — so a result that failed its own (possibly higher)
    # price_adjusted_floor above, or scored below the CONDITIONAL
    # confidence band, or came back DO_NOT_BUILD from the model itself for
    # a qualitative reason, is not silently dropped; it becomes watch
    # ("clears the money, but here's the specific risk to judge").
    if final_floor < _HARD_MRR_FLOOR:
        status = "killed_below_floor"
        result["reason"] = (
            f"Hard MRR gate: net_mrr_floor ${final_floor:,.0f} is below the absolute "
            f"${_HARD_MRR_FLOOR:,.0f} floor under any realistic scenario — killed before "
            f"reaching the dashboard, archived to the rejection log only."
        )
    # READY requires clearing THIS row's own price-tier floor (adjusted_floor,
    # e.g. $5,000 for a $100+/mo idea) — not a flat $4,000. A flat check would
    # wrongly pass a premium idea that only clears the cheapest tier's bar.
    elif final_floor >= adjusted_floor and confidence_score is not None and confidence_score >= 75:
        status = "READY_TO_BUILD"
    # CONDITIONAL's $3,500-$4,000 band is deliberately the literal absolute
    # range Kelvin specified, not this row's own adjusted_floor — it exists
    # to flag inherently-cheap-tier ideas landing just under $4,000, not to
    # give a premium-tier idea a pass for merely clearing the $3,500 floor.
    elif (_HARD_MRR_FLOOR <= final_floor < 4000) or (
        confidence_score is not None and 65 <= confidence_score <= 74
    ):
        status = "validated"
        result["reason"] = result.get("reason") or (
            f"CONDITIONAL: net_mrr_floor ${final_floor:,.0f} / confidence "
            f"{confidence_score if confidence_score is not None else 'n/a'}/100 — in the "
            f"judgment-call range, not a clean pass."
        )
    else:
        status = "watch"
        risk_note = result.get("reason") or (
            f"verdict={verdict}" if verdict == "DO_NOT_BUILD" else "no reason returned"
        )
        result["reason"] = (
            f"WATCH: clears the ${_HARD_MRR_FLOOR:,.0f} absolute floor (net_mrr_floor "
            f"${final_floor:,.0f}) but doesn't clear its own criteria for READY or "
            f"CONDITIONAL (confidence {confidence_score if confidence_score is not None else 'n/a'}/100). "
            f"Specific risk: {risk_note}"
        )

    # watch surfaces its reason too — the whole point of the status is
    # "clears the money, here's the specific risk to judge," so the risk
    # must actually be visible on the dashboard, not just stored inside
    # verdict_v2_output.
    if status in ("killed_below_floor", "watch", "rejected"):
        rejection_reason = result.get("reason") or "Did not clear v5.0 gates — no reason returned."
    else:
        rejection_reason = None

    # RAG write happens for EVERY verdict outcome (2026-07-27), not just
    # BUILD/CONDITIONAL — a DO_NOT_BUILD is exactly as valuable a data point
    # for future retrieval/calibration as a pass. Never allowed to raise or
    # block this function's real return value; a failed write here costs
    # this one row of future RAG context, nothing more.
    rag_verdict = {"READY_TO_BUILD": "BUILD", "validated": "CONDITIONAL"}.get(status, "DO_NOT_BUILD")
    try:
        embed_and_insert({
            "product_name":       result.get("solution_concept") or opp.get("solution_concept", ""),
            "vertical":           result.get("vertical") or opp.get("vertical", ""),
            "verdict":            rag_verdict,
            "confidence_score":   confidence_score,
            "projected_mrr_floor": int(final_floor) if final_floor is not None else None,
            "primary_risk_flag":  rejection_reason or result.get("existing_tool", {}).get("name"),
            "competitor_signals": result.get("existing_tool"),
            "pain_themes":        [opp.get("pain_point", "")] if opp.get("pain_point") else [],
            "rationale":          rejection_reason or result.get("reason") or "Cleared all v5.0 gates.",
        })
    except Exception as e:
        log.warning("[%s] RAG outcome write failed (non-blocking): %s", AGENT_ID, e)

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
