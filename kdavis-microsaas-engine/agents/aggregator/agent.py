"""
Verdict agent v2.0 — Consolidated from an 8-Model Audit, July 2026.

Replaces the earlier deterministic Python gate-checker. That checker
loaded prompt.md into a module constant that was never actually passed
to an LLM call anywhere — confirmed dead code — and just trust-checked
whatever competition_density/stack_compatible/build_confidence_score
labels the upstream vertical agent had self-assigned, with no
independent verification of any kind.

v2.0's entire premise is that competitor discovery must be independently
verified (HARD GATE before any MRR math runs), which means the agent has
to actually search the web rather than recall training data — two of
the audit's named failure modes were exactly "asserted no competitor
exists without searching" and "stale pricing pulled from memory". This
now genuinely calls Sonnet with Anthropic's server-side web_search tool
via core.llm_router.analyze_with_web_search.

Full reasoning rules live in prompt.md (the v2.0 spec verbatim, plus an
appended OUTPUT CONTRACT). The spec's per-step output format is a mix of
labeled text blocks, which isn't reliably parseable on its own — the
contract instructs the model to end with one consolidated JSON object
covering every step's key fields instead.
"""
import json
from pathlib import Path
from typing import Callable

from core.llm_router import analyze_with_web_search

AGENT_ID = "aggregator-verdict-v2"

SYSTEM_PROMPT = (Path(__file__).parent / "prompt.md").read_text()

MRR_FLOOR = 4000

# v2.0's VERDICT enum maps onto the pipeline's existing status vocabulary
# (READY_TO_BUILD/validated/rejected) rather than introducing a second,
# parallel one — brief_generator and the dashboard's filter tabs already
# key off this vocabulary. RESUBMIT (added 2026-07-18) maps to its own
# 'needs_correction' status rather than 'rejected' — the dashboard's
# Reject & Delete button archives-then-deletes 'rejected' rows, which
# would destroy a genuinely fixable opportunity (price inconsistency,
# capture rate misapplied, missing GTM channel) instead of surfacing the
# correction needed.
_VERDICT_TO_STATUS = {
    "BUILD": "READY_TO_BUILD",
    "CONDITIONAL": "validated",
    "DO_NOT_BUILD": "rejected",
    "RESUBMIT": "needs_correction",
}


def run(raw_findings: list[dict], llm: Callable[..., str] = analyze_with_web_search) -> list[dict]:
    """Evaluate every opportunity card through the full v2.0 research pipeline."""
    return [_evaluate(opp, llm) for opp in raw_findings]


def _evaluate(opp: dict, llm: Callable[..., str]) -> dict:
    user_input = json.dumps(opp, default=str)
    raw_response = llm(SYSTEM_PROMPT, user_input)
    result = _extract_trailing_json(raw_response)

    verdict = result.get("verdict", "DO_NOT_BUILD")
    status = _VERDICT_TO_STATUS.get(verdict, "rejected")

    # Hard-enforce the $4K floor at the code level too — CLAUDE.md's floor
    # rule is non-negotiable, and this is the one number the whole
    # factory's business model depends on. Never trust the model's own
    # verdict/mrr_floor_gate_clear alone for it.
    final_floor = result.get("final_mrr_floor") or 0
    if status == "READY_TO_BUILD" and final_floor < MRR_FLOOR:
        status = "rejected"
        result["blocking_issues"] = (result.get("blocking_issues") or []) + [
            f"Code-level floor check failed: final_mrr_floor ${final_floor:,.0f} is below "
            f"the ${MRR_FLOOR:,.0f} floor despite verdict={verdict}."
        ]

    if status == "rejected" and result.get("halt"):
        rejection_reason = f"Competitor exists: {_summarize_competitors(result)}"
    elif status == "rejected":
        rejection_reason = "; ".join(result.get("blocking_issues") or [result.get("next_action") or "Did not clear v2.0 gates."])
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
        return "halted, no competitor detail returned"
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
