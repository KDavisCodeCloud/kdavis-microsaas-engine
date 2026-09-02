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
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents.marketing.mkt_o5_sequence_sender import _get_lead
from core.email_compliance import append_compliance_footer
from core.supabase_client import get_supabase

router = APIRouter(prefix="/outreach", tags=["outreach"])

# Finding 2 (2026-09-02 HITL audit): an approval with no expiry could sit
# indefinitely and fire whenever the hourly sender next ran, with zero
# freshness check. 7 days matches the audit's own recommendation. Only
# meaningful for 'approved_hitl' -- MKT-O5 never polls 'approved_manual'
# (LinkedIn) at all, so an expiry there would never be checked by anything.
_HITL_APPROVAL_TTL = timedelta(days=7)


def _require_admin(request: Request) -> None:
    if getattr(request.state, "role", "") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


class ResolveSequence(BaseModel):
    resolved_by: str | None = None


@router.get("/dm-sequences/{sequence_id}/preview")
async def preview_dm_sequence(sequence_id: str, request: Request):
    """
    Finding 3 (2026-09-02 HITL audit): the compliance footer (mailing
    address + unsubscribe link, CAN-SPAM-required) is appended at send
    time in mkt_o5_sequence_sender.py, not at queue time -- an approver
    was reviewing touch_1/touch_2 without ever seeing the exact bytes
    Resend actually sends. Read-only, no side effects, does not touch the
    send path at all: reuses mkt_o5_sequence_sender.py's own _get_lead and
    core/email_compliance.py's own append_compliance_footer so the preview
    can't drift from what a real send would produce -- a second,
    independently-written footer-composition function here would just
    create a new way for preview and reality to disagree.

    LinkedIn-sourced sequences (lead_source='linkedin_manual'/
    'linkedin_engager') never reach MKT-O5 and never get a footer -- for
    those this returns the raw touch_1/touch_2 unchanged, `has_footer:
    false`, so the frontend doesn't imply a footer that will never exist.
    """
    _require_admin(request)
    db = get_supabase()

    seq = db.table("mse_dm_sequences").select("*").eq("id", sequence_id).maybe_single().execute()
    if seq is None or not seq.data:
        raise HTTPException(status_code=404, detail="Sequence not found")

    lead_source = seq.data.get("lead_source") or "apollo"
    if lead_source not in ("apollo", "lead_finder"):
        return {"touch_1": seq.data["touch_1"], "touch_2": seq.data["touch_2"], "has_footer": False}

    lead = _get_lead(db, seq.data)
    if not lead or not lead.get("email"):
        raise HTTPException(status_code=422, detail="No email on file for this lead — cannot compose an accurate preview.")

    return {
        "touch_1": append_compliance_footer(seq.data["touch_1"], lead["email"]),
        "touch_2": append_compliance_footer(seq.data["touch_2"], lead["email"]),
        "has_footer": True,
    }


@router.post("/dm-sequences/{sequence_id}/approve")
async def approve_dm_sequence(sequence_id: str, body: ResolveSequence, request: Request):
    _require_admin(request)
    db = get_supabase()

    # lead_source decides the target status, not just a passthrough value:
    # mkt_o5_sequence_sender.py polls specifically for status='approved_hitl'
    # and emails whatever it finds via Resend using the linked lead's email.
    # LinkedIn leads have no email on file at all, so LinkedIn-sourced
    # sequences must land on a status MKT-O5 never queries for —
    # 'approved_manual' — instead of 'approved_hitl'. lead_finder leads
    # DO have a real, SMTP-verified email (agents/marketing/mkt_lead_finder.py
    # only ever hands MKT-O2 email_status='verified' leads) and are meant
    # to be auto-sent same as apollo, so they route to 'approved_hitl' too
    # (2026-08-14) — see mkt_o5_sequence_sender.py's _get_lead() for the
    # matching lookup change.
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
    new_status = "approved_hitl" if lead_source in ("apollo", "lead_finder") else "approved_manual"
    now = datetime.now(timezone.utc)

    update_payload = {
        "status": new_status,
        "hitl_approved_by": body.resolved_by,
        "hitl_approved_at": now.isoformat(),
    }
    if new_status == "approved_hitl":
        update_payload["hitl_approved_expires_at"] = (now + _HITL_APPROVAL_TTL).isoformat()

    result = db.table("mse_dm_sequences").update(update_payload).eq("id", sequence_id).eq("status", "pending_hitl").execute()

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
