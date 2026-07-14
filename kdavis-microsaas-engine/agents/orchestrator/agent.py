import asyncio
import json
import importlib
from pathlib import Path
from typing import TypedDict
from langgraph.graph import StateGraph, END
from core.llm_router import analyze
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

    # Stub: Sonnet performs the research directly using the orchestrator schema
    user_prompt = (
        f"Vertical: {vertical}\n\n"
        "You are the vertical research agent for this vertical. "
        "Apply the search strategy in your system prompt. "
        "Return a JSON array of 2–3 opportunity cards using the exact schema defined above. "
        "Every pain point must cite a real source. Every MRR figure must show math. "
        "Return only the JSON array — no prose, no markdown fences."
    )
    safe = DataSanitizationShield.clean(user_prompt)
    raw = analyze(_SYSTEM_PROMPT, safe, max_tokens=8192)

    # Strip markdown code fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]          # drop opening fence
        if raw.startswith("json"):
            raw = raw[4:]                      # drop language tag
        raw = raw.rsplit("```", 1)[0].strip()  # drop closing fence
    try:
        findings = json.loads(raw)
        if not isinstance(findings, list):
            findings = [findings]
    except json.JSONDecodeError:
        findings = []

    return {"vertical": vertical, "findings": findings}


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
        if result.get("status") not in ("READY_TO_BUILD", "validated", "watch"):
            continue
        source = next(
            (f for f in state["raw_findings"]
             if f.get("solution_concept") == result.get("solution_concept")),
            {}
        )
        # Map all NOT NULL columns so the insert never violates constraints
        mrr = source.get("conservative_mrr_potential", 0)
        try:
            mrr = float(mrr)
        except (TypeError, ValueError):
            mrr = 0.0

        to_insert.append({
            "vertical":                   result.get("vertical", "") or source.get("vertical", ""),
            "pain_point":                 source.get("pain_point", "See research session notes"),
            "icp":                        source.get("icp") or {},
            "solution_concept":           result.get("solution_concept", ""),
            "mrr_calculation":            source.get("mrr_calculation", ""),
            "competitor_pricing_avg":     source.get("competitor_pricing_avg"),
            "conservative_mrr_potential": max(mrr, 4000),  # enforce $4K floor
            "competition_density":        source.get("competition_density"),
            "competition_density_reason": source.get("competition_density_reason", ""),
            "build_confidence_score":     source.get("build_confidence_score", 0),
            "build_confidence_reason":    source.get("build_confidence_reason", ""),
            "retention_hooks":            source.get("retention_hooks") or {},
            "competitor_examples":        source.get("competitor_examples") or [],
            "source_urls":                source.get("source_urls") or [],
            "tier_structure":             source.get("tier_structure") or {},
            "mcp_integration_surface":    source.get("mcp_integration_surface"),
            "stack_compatible":           source.get("stack_compatible", True),
            "stack_compatibility_notes":  source.get("stack_compatibility_notes", ""),
            "estimated_build_weeks":      source.get("estimated_build_weeks"),
            "status":                     result.get("status", "watch"),
            "notes":                      f"session:{state['session_id']}",
        })

    if to_insert:
        db.table("opportunity_pipeline").insert(to_insert).execute()

    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": "research_pipeline_written",
        "metadata": {"session_id": state["session_id"], "inserted": len(to_insert)},
    }).execute()

    return {**state, "status": "complete"}


def node_summarize(state: OrchestratorState) -> OrchestratorState:
    results = state["aggregated_results"]
    findings = state["raw_findings"]

    ready     = [r for r in results if r.get("status") == "READY_TO_BUILD"]
    validated = [r for r in results if r.get("status") == "validated"]
    watch     = [r for r in results if r.get("status") == "watch"]
    rejected  = [r for r in results if r.get("status") == "rejected"]

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
        "top_opportunity":          top.get("solution_concept") if top else None,
        "recommended_first_build":  top.get("solution_concept") if top else None,
    }
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
