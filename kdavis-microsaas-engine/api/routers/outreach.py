"""
Outreach HITL actions — approving/rejecting a cold DM/email sequence
before it's sent, and marking a LinkedIn lead as manually contacted.
Dashboard-facing (operator JWT via tenant_context_middleware), admin role
required — same pattern as pipeline.py's /stamp endpoint. Reads (listing
pending sequences / the LinkedIn queue) go straight through the frontend's
Supabase client now that RLS actually enforces admin-only access
correctly; this router only owns the side-effecting writes.

2026-08-14: approve/reject below already worked generically on any
mse_dm_sequences row regardless of source — they were never email-only,
despite this module's original docstring calling out "cold email
sequence." So LinkedIn-sourced DM sequences (mkt_o2_cold_dm_writer.py's
lead_source="linkedin_manual"/"linkedin_engager") use these same two
endpoints unmodified; no separate LinkedIn-specific approve/reject was
added. approve/ was extended only to pick the right target status per
source (see below) — nothing here changed shape or added a new route.
frontend/app/outreach/page.tsx (the real, already-wired HITL queue this
router serves) was extended to show LinkedIn-sourced sequences and the
mse_linkedin_leads manual-outreach queue alongside the existing
apollo/email view, rather than standing up a second, parallel
"/marketing/hitl/dm-queue" JSON endpoint that nothing would consume.
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

    # lead_source decides the target status, not just a passthrough value:
    # mkt_o5_sequence_sender.py (untouched here — see migration
    # 20260814000023_linkedin_leads.sql's comment) polls specifically for
    # status='approved_hitl' and emails whatever it finds via Resend using
    # the linked apollo lead's email. LinkedIn leads have no email on file
    # at all, so a LinkedIn-sourced sequence must land on a status MKT-O5
    # never queries for — 'approved_manual' — instead of 'approved_hitl'.
    existing = (
        db.table("mse_dm_sequences")
        .select("lead_source")
        .eq("id", sequence_id)
        .eq("status", "pending_hitl")
        .maybe_single()
        .execute()
    )
    if existing is None or not existing.data:
        raise HTTPException(status_code=404, detail="Sequence not found or already resolved")

    lead_source = existing.data.get("lead_source") or "apollo"
    new_status = "approved_hitl" if lead_source == "apollo" else "approved_manual"

    result = db.table("mse_dm_sequences").update({
        "status": new_status,
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

    return {"status": new_status, "id": sequence_id}


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


@router.post("/linkedin-leads/{lead_id}/mark-sent")
async def mark_linkedin_lead_sent(lead_id: str, request: Request):
    """
    Same action as POST /marketing/linkedin/leads/{id}/mark-sent
    (api/routers/linkedin_intake.py), against the same mse_linkedin_leads
    row — but admin-JWT-gated for the dashboard itself to call, rather
    than the MARKETING_API_KEY shared secret meant for
    automation/internal-script callers. A browser session should never
    hold that secret, same reasoning as every other split between an
    internal-API-key marketing.py route and its dashboard-facing
    outreach.py equivalent (e.g. mark_lead_contacted above).
    """
    _require_admin(request)
    db = get_supabase()

    result = db.table("mse_linkedin_leads").update({
        "status": "contacted",
        "contacted_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", lead_id).eq("status", "pending_dm").execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found or already marked contacted")

    db.table("agent_events").insert({
        "agent_name": "Outreach HITL",
        "department": "marketing",
        "action": f"LinkedIn lead {lead_id[:8]}… marked sent by operator",
        "verdict": "pass",
    }).execute()

    return {"status": "contacted", "id": lead_id}
