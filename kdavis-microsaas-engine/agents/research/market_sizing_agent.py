"""
Market Sizing Agent — Week 2 of MSE-Build-Order.md's vertical-intel cadence
(due 2026-07-10, built 2026-07-13, now overdue). Only this one agent was
built this session, not Weeks 3-7 (Competitor signal / ICP research /
Retention pattern / Pricing signal / Distribution channel) — those aren't
due yet and building them now would jump the deliberate one-per-week pacing.

No detailed spec exists beyond the one-line name in MSE-Build-Order.md.
Judgment call made here, documented for whoever picks this thread back up:
the Weeks 2-7 agents are SIGNAL TYPES, not per-vertical modules — a
different axis than agents/orchestrator/agent.py's VERTICAL_MODULE_MAP
(which maps vertical NAMES like "Healthcare / Medical Front Desk" to
not-yet-built modules like healthcare_intel). This agent is a standalone
enrichment pass the orchestrator runs on every raw finding, not a new
vertical module.

Why this scope: the orchestrator's current per-vertical research
(_run_one_vertical's fallback path, since no vertical intel modules exist
yet) is one generic one-shot Sonnet call producing a full opportunity
card, including conservative_mrr_potential/mrr_calculation/
competitor_pricing_avg with no dedicated rigor behind those specific
numbers — exactly the fields the aggregator's hard $4,000/mo floor gate
depends on. This agent re-derives just those fields with a focused,
market-sizing-only prompt and merges the refined numbers back into the
finding, rather than replacing the whole card.
"""

import json

import core.llm_router as llm_router
from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

AGENT_ID = "market-sizing"

_SYSTEM_PROMPT = """You are the Market Sizing specialist for a micro-SaaS opportunity research swarm.
Given one opportunity card's vertical, pain point, solution concept, and any competitor examples already
found, produce a rigorous, defensible market-sizing pass. Return ONLY a single JSON object — no prose, no
markdown fences — matching exactly this schema:

{
  "competitor_pricing_avg": number,
  "conservative_mrr_potential": number,
  "mrr_calculation": string,
  "tam_estimate": string,
  "market_sizing_confidence": number
}

Field rules:
- competitor_pricing_avg: average monthly_price across the competitor_examples given. If none were given,
  estimate from comparable tools in the vertical and say so in mrr_calculation.
- conservative_mrr_potential: the LOWEST defensible number, not an optimistic one — this feeds a hard
  $4,000/mo floor gate; a card that shouldn't pass must not be inflated to pass.
- mrr_calculation: show real arithmetic — addressable accounts x realistic capture rate x price point —
  not a vague sentence.
- tam_estimate: total addressable market size with reasoning, e.g. "~40,000 US independent dental
  practices x 15% realistic reachable segment = 6,000 addressable accounts."
- market_sizing_confidence: 0-100. Score under 40 if the reasoning relies on assumptions not grounded in
  the pain_point or competitor evidence given — don't inflate confidence to look more useful."""


def _emit_event(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": event_type,
        "metadata": metadata,
    }).execute()


def _write_audit(db, outcome: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "market_sizing_pass",
        "outcome": outcome,
        "product_id": None,
        "metadata": metadata,
    }).execute()


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()
    return raw


def size_one_finding(finding: dict, supabase_client=None) -> dict:
    """Re-derives one finding's market-sizing fields. Returns the finding
    with those fields merged in (overwritten) — never mutates other fields.
    Raises on failure; caller decides whether one bad finding should sink
    the whole batch (size_findings below does not let it)."""
    db = supabase_client if supabase_client is not None else get_supabase()
    solution_concept = finding.get("solution_concept", "")

    safe_context = DataSanitizationShield.clean({
        "vertical": finding.get("vertical", ""),
        "pain_point": finding.get("pain_point", ""),
        "solution_concept": solution_concept,
        "competitor_examples": finding.get("competitor_examples") or [],
    })

    user_prompt = (
        f"Vertical: {safe_context['vertical']}\n"
        f"Pain point: {safe_context['pain_point']}\n"
        f"Solution concept: {safe_context['solution_concept']}\n"
        f"Competitor examples already found: {json.dumps(safe_context['competitor_examples'])}\n\n"
        "Produce the market sizing pass now, per your system prompt's schema and field rules."
    )

    try:
        raw = llm_router.analyze(_SYSTEM_PROMPT, user_prompt, max_tokens=1536)
        parsed = json.loads(_strip_fences(raw))
        if not isinstance(parsed, dict):
            raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")

        required = {"competitor_pricing_avg", "conservative_mrr_potential", "mrr_calculation",
                    "tam_estimate", "market_sizing_confidence"}
        missing = required - parsed.keys()
        if missing:
            raise ValueError(f"missing required fields: {missing}")

    except Exception as exc:
        _write_audit(db, "lose", {"solution_concept": solution_concept, "error": str(exc)})
        raise RuntimeError(f"Market sizing failed for '{solution_concept}': {exc}") from exc

    _write_audit(db, "win", {
        "solution_concept": solution_concept,
        "conservative_mrr_potential": parsed["conservative_mrr_potential"],
        "market_sizing_confidence": parsed["market_sizing_confidence"],
    })

    return {
        **finding,
        "competitor_pricing_avg": parsed["competitor_pricing_avg"],
        "conservative_mrr_potential": parsed["conservative_mrr_potential"],
        "mrr_calculation": parsed["mrr_calculation"],
        "tam_estimate": parsed["tam_estimate"],
        "market_sizing_confidence": parsed["market_sizing_confidence"],
    }


def size_findings(findings: list, session_id: str = "") -> list:
    """
    Orchestrator entry point — sizes every finding in a batch. A single
    finding's sizing failure does NOT sink the batch: that finding is kept
    with its original (unsized) MRR fields and audit-logged as a loss,
    same fail-open convention as the orchestrator's own vertical dispatch
    (asyncio.gather(..., return_exceptions=True)).
    """
    db = get_supabase()
    _emit_event(db, "market_sizing_started", {"session_id": session_id, "findings_count": len(findings)})

    sized = []
    for finding in findings:
        try:
            sized.append(size_one_finding(finding, supabase_client=db))
        except RuntimeError:
            sized.append(finding)

    _emit_event(db, "market_sizing_completed", {"session_id": session_id, "findings_count": len(sized)})
    return sized
