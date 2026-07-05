"""
CEO Decoded dashboard API routes.
These routes are called from the ceo-dashboard Next.js app.
All routes require admin-role JWT — enforced in middleware.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core.supabase_client import get_supabase
from core.sanitization import DataSanitizationShield
from core.llm_router import analyze
from pathlib import Path

router = APIRouter(prefix="/ceo", tags=["ceo"])

_LEGAL_SYSTEM = "You are a legal information assistant. Provide factual, general legal information only — never legal advice. Always conclude with: 'Consult a licensed attorney before acting on this information.' Cite sources when possible."
_ADVISORY_SYSTEM = "You are an executive advisor acting as {role}. Provide strategic counsel based on the context provided. Be direct and actionable. Focus on the specific domain: {domain}."


class HitlResolution(BaseModel):
    resolved_by: str | None = None


class LegalQuery(BaseModel):
    question: str


class AdvisoryBrief(BaseModel):
    advisor_role: str  # CFO | CMO | CTO
    context: str


@router.post("/hitl/{item_id}/approve")
async def approve_hitl(item_id: str, body: HitlResolution):
    """Approve a HITL queue item with optimistic write."""
    from datetime import datetime, timezone
    db = get_supabase()
    result = db.table("hitl_queue").update({
        "status": "approved",
        "resolved_by": body.resolved_by,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", item_id).eq("status", "pending").execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Item not found or already resolved")

    db.table("agent_events").insert({
        "agent_name": "HITL Gateway",
        "department": "ops",
        "action": f"HITL item {item_id[:8]}… approved by operator",
        "verdict": "pass",
    }).execute()

    return {"status": "approved", "id": item_id}


@router.post("/hitl/{item_id}/reject")
async def reject_hitl(item_id: str, body: HitlResolution):
    """Reject a HITL queue item."""
    from datetime import datetime, timezone
    db = get_supabase()
    result = db.table("hitl_queue").update({
        "status": "rejected",
        "resolved_by": body.resolved_by,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", item_id).eq("status", "pending").execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Item not found or already resolved")

    db.table("agent_events").insert({
        "agent_name": "HITL Gateway",
        "department": "ops",
        "action": f"HITL item {item_id[:8]}… rejected by operator",
        "verdict": "flagged",
    }).execute()

    return {"status": "rejected", "id": item_id}


@router.post("/legal/query")
async def legal_query(body: LegalQuery):
    """AI-assisted legal Q&A — logs every response."""
    safe_question = DataSanitizationShield.clean(body.question)
    response = analyze(_LEGAL_SYSTEM, safe_question, max_tokens=1024)

    get_supabase().table("advisory_threads").insert({
        "advisor_role": "CFO",
        "advisor_name": "Legal AI",
        "message": response,
        "role": "advisor",
        "memory_summary": f"Q: {safe_question[:80]}…",
    }).execute()

    return {"question": safe_question, "answer": response}


@router.post("/advisory/brief")
async def advisory_brief(body: AdvisoryBrief):
    """Brief an advisor with current context and get strategic counsel."""
    if body.advisor_role not in ("CFO", "CMO", "CTO"):
        raise HTTPException(status_code=400, detail="advisor_role must be CFO, CMO, or CTO")

    domain_map = {"CFO": "finance and revenue", "CMO": "marketing and growth", "CTO": "technology and architecture"}
    system = _ADVISORY_SYSTEM.format(role=body.advisor_role, domain=domain_map[body.advisor_role])
    safe_context = DataSanitizationShield.clean(body.context)
    response = analyze(system, safe_context, max_tokens=2048)

    advisor_names = {"CFO": "Alex Rivera", "CMO": "Jordan Lee", "CTO": "Morgan Chen"}
    get_supabase().table("advisory_threads").insert({
        "advisor_role": body.advisor_role,
        "advisor_name": advisor_names[body.advisor_role],
        "message": response,
        "role": "advisor",
    }).execute()

    return {"advisor_role": body.advisor_role, "response": response}


@router.get("/health")
async def ceo_health():
    """Health check for CEO dashboard backend connectivity."""
    try:
        get_supabase().table("agent_events").select("id").limit(1).execute()
        return {"status": "ok", "supabase": "connected"}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}
