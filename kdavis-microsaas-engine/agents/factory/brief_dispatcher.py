"""
Brief dispatcher — NOVA gap-closure Phase D1 (2026-08-29).

Closes the loop the pasted NOVA description promised and jarvis-decoded's
own Phase D plan confirmed as real and missing: NOVA can see a generated
build brief (mse_build_briefs.claude_code_brief) but had nothing that
pushed it anywhere. Triggered by `POST /factory/dispatch-brief/{brief_id}`
(see api/routers/factory.py) -- mirrors the existing
/factory/build/{id} and /factory/generate-brief/{id} HITL-gated trigger
pattern exactly, and by nova/mcp/tools/factory.py's new dispatch tool
(jarvis-decoded, calling this same endpoint over HTTP).

This does not write code or open a PR -- it sends the brief to Claude for
a real, reviewable response (an implementation plan / first-pass
scaffold outline, framed by the prompt below) and stores that response.
A human still reviews claude_code_brief and this dispatch_result before
anything gets built -- mse_build_briefs.status is untouched by this
module, same boundary brief_generator.py itself already draws between
generating a brief and spending money via run_build_pipeline.
"""
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from core.supabase_client import get_supabase
from core.llm_router import analyze

AGENT_ID = "factory-brief-dispatcher"

DISPATCH_SYSTEM_PROMPT = """You are reviewing a build brief for a new \
micro-SaaS product produced by an automated opportunity-verdict pipeline. \
Read the brief and respond with:
1. A short feasibility read (2-3 sentences) -- does the brief describe a \
buildable, coherent product?
2. The concrete first implementation steps you would take, in order.
3. Any real risks or gaps in the brief a human reviewer should know about \
before approving a build.

Be direct and concrete. This is a planning response for human review, not \
a request to write or execute code."""


def _write_audit(db, outcome: str, product_id: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "brief_dispatch_run",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def dispatch_brief(
    brief_id: str,
    triggered_by: str,
    supabase_client: Optional[Any] = None,
    llm_analyze: Callable[..., str] = analyze,
) -> dict[str, Any]:
    """Reads mse_build_briefs.claude_code_brief for brief_id, sends it to
    Claude for review, and writes the result back onto the same row
    (dispatch_result/dispatched_at/dispatch_status/dispatched_by). Raises
    on any failure -- never fails silently (this project's own
    non-negotiable) -- but always writes dispatch_status='failed' first
    so a failed dispatch is visible on the row, not just in logs.

    supabase_client/llm_analyze injection matches
    brief_generator.generate_build_brief's own signature shape, for the
    same reason: real unit tests without a live Supabase project or a
    real Claude API call."""
    db = supabase_client or get_supabase()

    brief_response = db.table("mse_build_briefs").select("*").eq("id", brief_id).limit(1).execute()
    rows = brief_response.data or []
    if not rows:
        raise ValueError(f"No mse_build_briefs row for id {brief_id}")
    brief = rows[0]

    code_brief_markdown = (brief.get("claude_code_brief") or {}).get("markdown")
    if not code_brief_markdown:
        raise ValueError(f"mse_build_briefs row {brief_id} has no claude_code_brief.markdown to dispatch")

    try:
        response_text = llm_analyze(DISPATCH_SYSTEM_PROMPT, code_brief_markdown, max_tokens=4096)
    except Exception as exc:
        dispatched_at = datetime.now(timezone.utc).isoformat()
        db.table("mse_build_briefs").update({
            "dispatch_status": "failed",
            "dispatched_at": dispatched_at,
            "dispatched_by": triggered_by,
            "dispatch_result": {"error": str(exc)},
        }).eq("id", brief_id).execute()
        # audit_log.product_id is a uuid FK -- brief_id (not product_slug,
        # which is text) is the only uuid this function has in hand. Real
        # bug caught live 2026-08-29: passing product_slug here raised
        # postgrest APIError 22P02 "invalid input syntax for type uuid"
        # and masked the actual dispatch failure being audited.
        _write_audit(db, "lose", brief_id, {"triggered_by": triggered_by, "product_slug": brief.get("product_slug"), "error": str(exc)})
        raise RuntimeError(f"Brief dispatch failed for {brief_id}: {exc}") from exc

    dispatched_at = datetime.now(timezone.utc).isoformat()
    result = {"response": response_text}
    db.table("mse_build_briefs").update({
        "dispatch_status": "dispatched",
        "dispatched_at": dispatched_at,
        "dispatched_by": triggered_by,
        "dispatch_result": result,
    }).eq("id", brief_id).execute()

    _write_audit(db, "win", brief_id, {"triggered_by": triggered_by, "product_slug": brief.get("product_slug")})

    return {"brief_id": brief_id, "dispatch_status": "dispatched", "dispatched_at": dispatched_at, "result": result}
