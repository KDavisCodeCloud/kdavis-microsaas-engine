"""
Outreach HITL actions — approving/rejecting a cold email sequence before
MKT-O5 sends it, and marking a LinkedIn lead as manually contacted.
Dashboard-facing (operator JWT via tenant_context_middleware), admin role
required — same pattern as pipeline.py's /stamp endpoint. Reads (listing
pending sequences / the LinkedIn queue) go straight through the frontend's
Supabase client now that RLS actually enforces admin-only access
correctly; this router only owns the side-effecting writes.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.supabase_client import get_supabase

router = APIRouter(prefix="/outreach", tags=["outreach"])


def _require_admin(request: Request) -> None:
    if getattr(request.state, "role", "") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


class ResolveSequence(BaseModel):
    resolved_by: str | None = None


@router.post("/dm-sequences/{sequence_id}/approve")
async def approve_dm_sequence(sequence_id: str, body: ResolveSequence, request: Request):
    _require_admin(request)
    db = get_supabase()

    result = db.table("mse_dm_sequences").update({
        "status": "approved_hitl",
        "hitl_approved_by": body.resolved_by,
        "hitl_approved_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", sequence_id).eq("status", "pending_hitl").execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Sequence not found or already resolved")

    db.table("agent_events").insert({
        "agent_name": "Outreach HITL",
        "department": "marketing",
        "action": f"DM sequence {sequence_id[:8]}… approved by operator",
        "verdict": "pass",
    }).execute()

    return {"status": "approved_hitl", "id": sequence_id}


@router.post("/dm-sequences/{sequence_id}/reject")
async def reject_dm_sequence(sequence_id: str, body: ResolveSequence, request: Request):
    _require_admin(request)
    db = get_supabase()

    result = db.table("mse_dm_sequences").update({
        "status": "rejected_hitl",
        "hitl_approved_by": body.resolved_by,
        "hitl_approved_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", sequence_id).eq("status", "pending_hitl").execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Sequence not found or already resolved")

    db.table("agent_events").insert({
        "agent_name": "Outreach HITL",
        "department": "marketing",
        "action": f"DM sequence {sequence_id[:8]}… rejected by operator",
        "verdict": "flagged",
    }).execute()

    return {"status": "rejected_hitl", "id": sequence_id}


@router.post("/leads/{lead_id}/mark-contacted")
async def mark_lead_contacted(lead_id: str, request: Request):
    """A human has manually sent a LinkedIn DM to this lead — record it so
    it drops out of the manual-outreach queue. Never sent automatically."""
    _require_admin(request)
    db = get_supabase()

    result = db.table("mse_apollo_leads").update({
        "linkedin_contacted_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", lead_id).is_("linkedin_contacted_at", "null").execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found or already marked contacted")

    db.table("agent_events").insert({
        "agent_name": "Outreach HITL",
        "department": "marketing",
        "action": f"Lead {lead_id[:8]}… marked LinkedIn-contacted by operator",
        "verdict": "pass",
    }).execute()

    return {"status": "contacted", "id": lead_id}
