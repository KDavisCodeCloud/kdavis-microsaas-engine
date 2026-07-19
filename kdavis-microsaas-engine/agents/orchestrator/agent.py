import asyncio
import json
import importlib
from pathlib import Path
from typing import TypedDict
from langgraph.graph import StateGraph, END
from core.llm_router import analyze_with_web_search
from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

_SYSTEM_PROMPT = (Path(__file__).parent / "prompt.md").read_text()

VERTICAL_MODULE_MAP = {
    "Healthcare / Medical Front Desk": "healthcare_intel",
    "Legal / Professional Services":   "legal_intel",
    "E-commerce / Retail Ops":         "ecommerce_intel",
    "Real Estate / Property Management": "realestate_intel",
    "HR / Ops / People Management":    "hr_ops_intel",
    "Finance / Accounting / Bookkeeping": "finance_intel",
}


class OrchestratorState(TypedDict):
    session_id: str
    verticals: list
    raw_findings: list
    aggregated_results: list
    session_summary: dict
    status: str


# ── Nodes ────────────────────────────────────────────────────────────────────

def node_initialize(state: OrchestratorState) -> OrchestratorState:
    get_supabase().table("usage_events").insert({
        "tenant_id": None,
        "event_type": "research_session_started",
        "metadata": {"session_id": state["session_id"], "verticals": state["verticals"]},
    }).execute()
    return {**state, "status": "dispatching"}


async def _run_one_vertical(vertical: str) -> dict:
    """
    Runs the vertical intel agent if built, otherwise falls back to an
    LLM call using the orchestrator's search strategy + schema.
    """
    module_name = VERTICAL_MODULE_MAP.get(vertical)
    if module_name:
        try:
            mod = importlib.import_module(f"agents.{module_name}.agent")
            if hasattr(mod, "run"):
                findings = await mod.run(vertical)
                return {"vertical": vertical, "findings": findings}
        except ModuleNotFoundError:
            pass

    # Stub: Sonnet performs the research directly using the orchestrator schema.
    # Uses real web search (2026-07-19) -- previously plain analyze() with no
    # search, which meant Dispatch was submitting ideas from training-data
    # memory of what a tool currently does. Confirmed in two real v5.0
    # batches: 4/4 rejections traced back to a claimed FEATURE_GAP the
    # anchor tool had already shipped (Rentec Direct Oct 2024, Gusto Plus,
    # Clio Manage) -- Verdict's own live search caught it every time, but
    # only after burning a full paid Verdict research cycle on an idea that
    # was dead on arrival. Giving Dispatch the same tool lets it verify
    # current tool state before ever writing a submission.
    user_prompt = (
        f"Vertical: {vertical}\n\n"
        "You are the vertical research agent for this vertical. "
        "Apply the search strategy in your system prompt, including the "
        "FEATURE_GAP VERIFICATION section if applicable -- use your web "
        "search tool to check the anchor tool's CURRENT help docs/release "
        "notes before claiming a feature is missing. "
        "Return a JSON array of 2–3 opportunity cards using the exact schema defined above. "
        "Every pain point must cite a real source. Every MRR figure must show math. "
        "Once your research is complete, end your response with the JSON array and "
        "nothing after it -- no closing remarks, no markdown fence around it."
    )
    safe = DataSanitizationShield.clean(user_prompt)
    # max_tokens=16000, not the old 8192: a web-search-backed call narrates
    # through multiple searches before its final array, the same failure
    # mode that truncated Verdict's own web-search call mid-reasoning
    # before core.llm_router.analyze_with_web_search's default was bumped
    # 2026-07-19 -- this explicit override was bypassing that fix.
    raw = analyze_with_web_search(_SYSTEM_PROMPT, safe, max_tokens=16000)
    findings = _extract_trailing_json_array(raw)

    return {"vertical": vertical, "findings": findings}


def _extract_trailing_json_array(text: str) -> list:
    """
    Finds the LAST top-level balanced [...] span in the response text and
    parses it -- mirrors agents/aggregator/agent.py's _extract_trailing_json
    for objects. Needed once this call gained real web search: a
    search-backed response narrates its research before the final array,
    the same way Verdict's does, instead of emitting bare JSON at the very
    start of the response the way the old no-search prompt could get away
    with assuming. Depth is tracked only over '[' / ']' so a nested array
    inside an opportunity card (source_evidence, milestone_sequence) never
    gets mistaken for the outer array's own close.
    """
    spans = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    spans.append((start, i))

    for start, end in reversed(spans):
        try:
            parsed = json.loads(text[start:end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            continue
    return []


async def node_dispatch_verticals(state: OrchestratorState) -> OrchestratorState:
    tasks = [_run_one_vertical(v) for v in state["verticals"]]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    raw_findings = []
    for r in results:
        if isinstance(r, Exception):
            continue
        raw_findings.extend(r.get("findings", []))

    get_supabase().table("usage_events").insert({
        "tenant_id": None,
        "event_type": "research_verticals_complete",
        "metadata": {"session_id": state["session_id"], "findings_count": len(raw_findings)},
    }).execute()

    return {**state, "raw_findings": raw_findings, "status": "aggregating"}


def node_size_market(state: OrchestratorState) -> OrchestratorState:
    from agents.research.market_sizing_agent import size_findings
    sized = size_findings(state["raw_findings"], session_id=state["session_id"])
    return {**state, "raw_findings": sized, "status": "aggregating"}


def node_run_aggregator(state: OrchestratorState) -> OrchestratorState:
    from agents.aggregator.agent import run as aggregate
    results = aggregate(state["raw_findings"])
    return {**state, "aggregated_results": results, "status": "writing"}


def node_write_pipeline(state: OrchestratorState) -> OrchestratorState:
    db = get_supabase()
    to_insert = []
    for result in state["aggregated_results"]:
        # Every evaluated opportunity gets a row now, including rejected
        # ones — the dashboard has had a "rejected" filter tab since the
        # Opportunities page was built, but this filter used to skip
        # writing rejected rows entirely, so that tab has always been
        # empty. Visibility into rejections (with the agent's actual
        # reasoning) is exactly what the human-review tuning loop needs.
        source = next(
            (f for f in state["raw_findings"]
             if f.get("solution_concept") == result.get("solution_concept")),
            {}
        )
        v2 = result.get("verdict_v2_output") or {}

        # The Verdict agent's own independently-researched MRR floor is
        # authoritative when present — it's backed by real segment-sizing
        # and live pricing research, not the upstream Dispatch submission's
        # unverified self-report. v5.0 flattens this to a single top-level
        # net_mrr_floor (no more three-scenario FLOOR/BASE/STRETCH nesting)
        # — fall back to the v3.0/v4.0 nested shape, then the v2.0 legacy
        # top-level key, in case an older-shaped response ever comes
        # through. Falls back to the upstream figure only if the
        # aggregator didn't run at all (e.g. legacy findings).
        mrr = v2.get("net_mrr_floor")
        if mrr is None:
            mrr = (v2.get("scenarios") or {}).get("floor", {}).get("final_mrr_floor")
        if mrr is None:
            mrr = v2.get("final_mrr_floor")  # legacy v2.0 top-level shape
        if mrr is None:
            mrr = source.get("conservative_mrr_potential", 0)
        try:
            mrr = float(mrr)
        except (TypeError, ValueError):
            mrr = 0.0
        # No floor clamp here — a sub-$4K number must be visible as what
        # it actually is. The $4K floor is enforced by REJECTING the
        # opportunity (in agents/aggregator/agent.py), never by inflating
        # its reported value. Presenting a clamped-up number as if it
        # cleared the floor is exactly the "floor inflation" failure mode
        # Verdict v2.0 was written to eliminate.

        # v5.0 anchors each idea on a single named existing_tool (not a
        # competitors_found list) — fall back through the older shapes for
        # any legacy-format result.
        competitor_examples = source.get("competitor_examples") or []
        if v2.get("existing_tool", {}).get("name"):
            competitor_examples = [v2["existing_tool"]["name"]]
        elif v2.get("competitors_found"):
            competitor_examples = [c.get("name") for c in v2["competitors_found"] if c.get("name")] or competitor_examples

        to_insert.append({
            "vertical":                   result.get("vertical", "") or source.get("vertical", ""),
            "pain_point":                 v2.get("gap_evidence") or v2.get("pain_evidence") or v2.get("pain_stakes") or source.get("pain_point", "See research session notes"),
            "icp":                        source.get("icp") or {},
            "solution_concept":           result.get("solution_concept", ""),
            "mrr_calculation":            source.get("mrr_calculation", ""),
            "competitor_pricing_avg":     v2.get("adjusted_avg_price") if v2.get("adjusted_avg_price") is not None else source.get("competitor_pricing_avg"),
            "conservative_mrr_potential": mrr,
            "competition_density":        source.get("competition_density"),
            "competition_density_reason": source.get("competition_density_reason", ""),
            "build_confidence_score":     source.get("build_confidence_score", 0),
            "build_confidence_reason":    source.get("build_confidence_reason", ""),
            "retention_hooks":            source.get("retention_hooks") or {},
            "competitor_examples":        competitor_examples,
            "source_urls":                source.get("source_urls") or [],
            "tier_structure":             source.get("tier_structure") or {},
            "mcp_integration_surface":    source.get("mcp_integration_surface"),
            "stack_compatible":           source.get("stack_compatible", True),
            "stack_compatibility_notes":  source.get("stack_compatibility_notes", ""),
            "estimated_build_weeks":      source.get("estimated_build_weeks"),
            "status":                     result.get("status", "watch"),
            "rejection_reason":           result.get("rejection_reason"),
            "verdict_v2_output":          v2 or None,
            # v3.0's price-adjusted floor (agents/aggregator/agent.py's
            # _price_adjusted_floor, computed independently of the
            # model's own claim and stored back onto the result) — the
            # mrr_floor_check DB constraint (migration 016) compares
            # conservative_mrr_potential against this per-row, replacing
            # the old flat $4,000 check.
            "price_adjusted_floor":       v2.get("price_adjusted_floor", 4000),
            "notes":                      f"session:{state['session_id']}",
        })

    # One row per insert, not a single batch insert — a batch INSERT is one
    # atomic statement, so a single row violating a DB constraint (e.g. the
    # mrr_floor_check on an internally-inconsistent verdict) rolls back
    # every other row in the batch too, silently losing otherwise-valid
    # results. Found 2026-07-19 when a bad CONDITIONAL row blocked two
    # correctly-computed SATURATED rows from ever being written.
    inserted = 0
    failed = []
    for row in to_insert:
        try:
            db.table("opportunity_pipeline").insert([row]).execute()
            inserted += 1
        except Exception as e:
            failed.append({"solution_concept": row.get("solution_concept", ""), "error": str(e)})

    if failed:
        db.table("usage_events").insert({
            "tenant_id": None,
            "event_type": "research_pipeline_write_failed",
            "metadata": {"session_id": state["session_id"], "failed": failed},
        }).execute()

    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": "research_pipeline_written",
        "metadata": {"session_id": state["session_id"], "inserted": inserted, "failed_count": len(failed)},
    }).execute()

    return {**state, "status": "complete"}


def node_summarize(state: OrchestratorState) -> OrchestratorState:
    results = state["aggregated_results"]
    findings = state["raw_findings"]

    ready     = [r for r in results if r.get("status") == "READY_TO_BUILD"]
    validated = [r for r in results if r.get("status") == "validated"]
    watch     = [r for r in results if r.get("status") == "watch"]
    rejected  = [r for r in results if r.get("status") == "rejected"]
    # RESUBMIT (added 2026-07-18) — a fixable error, not a dead opportunity.
    needs_correction = [r for r in results if r.get("status") == "needs_correction"]

    # Score each passing result to find top opportunity
    def _score(r):
        src = next((f for f in findings if f.get("solution_concept") == r.get("solution_concept")), {})
        return src.get("build_confidence_score", 0)

    top_candidates = sorted(ready + validated, key=_score, reverse=True)
    top = top_candidates[0] if top_candidates else None

    summary = {
        "session_id":               state["session_id"],
        "verticals_scanned":        len(state["verticals"]),
        "ready_to_build":           len(ready),
        "validated_pending_review": len(validated),
        "watch_list":               len(watch),
        "rejected":                 len(rejected),
        "needs_correction":         len(needs_correction),
        "top_opportunity":          top.get("solution_concept") if top else None,
        "recommended_first_build":  top.get("solution_concept") if top else None,
    }

    # Persisted completion signal — this used to only live in the in-memory
    # graph state, which a background task's caller never sees. Without
    # this, /research/session/{id} had no way to know the run finished at
    # all (research.py falls back to a fixed time estimate otherwise).
    get_supabase().table("usage_events").insert({
        "tenant_id": None,
        "event_type": "research_session_complete",
        "metadata": summary,
    }).execute()

    return {**state, "session_summary": summary, "status": "done"}


# ── Graph ─────────────────────────────────────────────────────────────────────

_graph = None


def _build_graph():
    g = StateGraph(OrchestratorState)
    g.add_node("initialize",         node_initialize)
    g.add_node("dispatch_verticals", node_dispatch_verticals)
    g.add_node("size_market",        node_size_market)
    g.add_node("run_aggregator",     node_run_aggregator)
    g.add_node("write_pipeline",     node_write_pipeline)
    g.add_node("summarize",          node_summarize)

    g.set_entry_point("initialize")
    g.add_edge("initialize",         "dispatch_verticals")
    g.add_edge("dispatch_verticals", "size_market")
    g.add_edge("size_market",        "run_aggregator")
    g.add_edge("run_aggregator",     "write_pipeline")
    g.add_edge("write_pipeline",     "summarize")
    g.add_edge("summarize",          END)

    return g.compile()


async def run(session_id: str, verticals: list[str]) -> dict:
    """Entry point called by api/routers/research.py."""
    global _graph
    if _graph is None:
        _graph = _build_graph()

    initial: OrchestratorState = {
        "session_id":        session_id,
        "verticals":         verticals,
        "raw_findings":      [],
        "aggregated_results": [],
        "session_summary":   {},
        "status":            "initializing",
    }
    final = await _graph.ainvoke(initial)
    return final["session_summary"]
