"""
Outreach lanes for the CEO Decoded Marketing page (Kelvin's decision 3,
2026-10-05).

ONE approval surface. The MSE dashboard's own Outreach page is being retired
to read-only; these endpoints back the four lanes that replace it:

  (b) Approve Drafts   GET  /marketing/outreach/drafts
                       POST /marketing/outreach/drafts/{id}/edit
                       (approve/reject stay in api/routers/outreach.py, which
                        already owns _approval_target_status and the HITL
                        state machine -- duplicating that is how a second,
                        subtly different approval path gets born)
  (c) Ready to Paste   GET  /marketing/outreach/ready-to-paste
                       POST /marketing/outreach/ready-to-paste/{id}/mark
  (d) Conversations    GET  /marketing/outreach/conversations
                       POST /marketing/outreach/conversations/{lead_id}/stage

Lane (a) Find the Buyer is api/routers/buyer_research.py, unchanged.

Same MARKETING_API_KEY auth as every other internal marketing router.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, field_validator

from api.middleware.auth import require_marketing_api_key
from core.supabase_client import get_supabase

log = logging.getLogger(__name__)

router = APIRouter(prefix="/marketing/outreach", tags=["marketing", "outreach"])

# Ordered: the API only ever moves a conversation FORWARD, so a mis-click in
# the UI cannot regress a won deal back to "replied".
STAGE_ORDER = ["replied", "call_booked", "proposal_sent", "won", "lost"]
STAGE_TIMESTAMP = {
    "replied": "replied_at",
    "call_booked": "call_booked_at",
    "proposal_sent": "proposal_sent_at",
    "won": "won_at",
    "lost": "lost_at",
}


def _lead_for(db, seq: dict) -> dict:
    lfid = seq.get("lead_finder_lead_id")
    if lfid:
        r = db.table("mse_leads").select(
            "id,company,domain,first_name,last_name,title,email,email_grade,"
            "linkedin_url,lead_route,contact_status,job_posting_title,"
            "job_posting_url,job_posting_date,open_role_count,fit_score,"
            "intent_score,score_reasons,stack_tags"
        ).eq("id", lfid).maybe_single().execute()
        return (r.data if r else None) or {}
    lid = seq.get("lead_id")
    if lid:
        r = db.table("mse_apollo_leads").select("*").eq("id", lid).maybe_single().execute()
        return (r.data if r else None) or {}
    lnid = seq.get("linkedin_lead_id")
    if lnid:
        r = db.table("mse_linkedin_leads").select("*").eq("id", lnid).maybe_single().execute()
        return (r.data if r else None) or {}
    return {}


def _posting_age_days(lead: dict) -> Optional[int]:
    raw = lead.get("job_posting_date")
    if not raw:
        return None
    try:
        posted = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - posted).days)
    except Exception:
        return None


def _card(db, seq: dict, products: dict) -> dict:
    """Everything one Approve-Drafts card needs, in one shape.

    Assembled server-side on purpose: the MSE page built its card from a
    PostgREST embed that omitted mse_leads entirely and silently rendered
    "Unknown / no contact on file" for all 11 drafts. A card the API composes
    cannot lose a join the client forgot to ask for.
    """
    lead = _lead_for(db, seq)
    name = " ".join(x for x in [lead.get("first_name"), lead.get("last_name")] if x).strip()
    return {
        "sequence_id": seq.get("id"),
        "status": seq.get("status"),
        "lead_source": seq.get("lead_source"),
        "product_id": seq.get("product_id"),
        "product": (products.get(seq.get("product_id")) or {}).get("name"),
        "lead_id": lead.get("id"),
        "company": lead.get("company"),
        "domain": lead.get("domain"),
        "contact": {
            "name": name or None,
            "title": lead.get("title"),
            "linkedin_url": lead.get("linkedin_url"),
            "email": lead.get("email"),
            "email_grade": lead.get("email_grade"),
            "contact_status": lead.get("contact_status"),
        },
        "route": lead.get("lead_route"),
        "signal": {
            "role": lead.get("job_posting_title"),
            "posting_url": lead.get("job_posting_url"),
            "posting_age_days": _posting_age_days(lead),
            "open_role_count": lead.get("open_role_count"),
            "stack": lead.get("stack_tags") or [],
        },
        "scores": {"fit": lead.get("fit_score"), "intent": lead.get("intent_score")},
        "why": lead.get("score_reasons") or [],
        "touches": {k: seq.get(k) for k in ("touch_1", "touch_2", "touch_3") if seq.get(k)},
        "created_at": seq.get("created_at"),
    }


def _products(db) -> dict:
    return {p["id"]: p for p in
            (db.table("mse_products").select("id,name").execute().data or [])}


@router.get("/drafts")
async def list_drafts(
    product_id: Optional[str] = None,
    limit: int = 50,
    authorization: Optional[str] = Header(default=None),
):
    """Lane (b): drafts awaiting approval, newest first."""
    require_marketing_api_key(authorization)
    db = get_supabase()
    q = db.table("mse_dm_sequences").select("*").eq("status", "pending_hitl")
    if product_id:
        q = q.eq("product_id", product_id)
    seqs = q.order("created_at", desc=True).limit(limit).execute().data or []
    products = _products(db)
    return {"drafts": [_card(db, s, products) for s in seqs], "count": len(seqs)}


class EditTouches(BaseModel):
    """Only the touches are editable. Status, route and approver are decided by
    the approve endpoint's own state machine, and letting the client post them
    would be a second approval path."""

    touch_1: Optional[str] = None
    touch_2: Optional[str] = None
    touch_3: Optional[str] = None

    @field_validator("touch_1", "touch_2", "touch_3")
    @classmethod
    def _not_blank(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("a touch cannot be edited to empty; reject the draft instead")
        return v


@router.post("/drafts/{sequence_id}/edit")
async def edit_draft(
    sequence_id: str,
    body: EditTouches,
    authorization: Optional[str] = Header(default=None),
):
    """Lane (b): edit copy before approving.

    Only a pending_hitl draft is editable. Editing after approval would change
    text that was already approved -- the audit trail would then show an
    approval of copy that no longer exists.
    """
    require_marketing_api_key(authorization)
    db = get_supabase()

    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        raise HTTPException(status_code=400, detail="No touches supplied")

    result = (db.table("mse_dm_sequences").update(payload)
              .eq("id", sequence_id).eq("status", "pending_hitl").execute())
    if not result.data:
        raise HTTPException(
            status_code=409,
            detail="Draft not found, or no longer pending approval (it cannot be edited after approval)",
        )
    log.info("[Outreach] draft %s edited: %s", sequence_id, sorted(payload))
    return {"sequence_id": sequence_id, "edited": sorted(payload)}


@router.get("/ready-to-paste")
async def list_ready_to_paste(
    limit: int = 50,
    authorization: Optional[str] = Header(default=None),
):
    """Lane (c): approved copy on the manual/LinkedIn track.

    'approved_manual' is the status the approve endpoint assigns when a lead
    cannot be emailed -- MKT-O5 never polls it, which is exactly why these
    need a human surface with copy buttons.
    """
    require_marketing_api_key(authorization)
    db = get_supabase()
    seqs = (db.table("mse_dm_sequences").select("*")
            .eq("status", "approved_manual")
            .order("hitl_approved_at", desc=True).limit(limit).execute().data or [])
    products = _products(db)
    return {"ready": [_card(db, s, products) for s in seqs], "count": len(seqs)}


class PasteMark(BaseModel):
    event: str  # sent | accepted | replied

    @field_validator("event")
    @classmethod
    def _known(cls, v):
        v = (v or "").strip().lower()
        if v not in ("sent", "accepted", "replied"):
            raise ValueError("event must be sent, accepted or replied")
        return v


@router.post("/ready-to-paste/{sequence_id}/mark")
async def mark_paste_event(
    sequence_id: str,
    body: PasteMark,
    authorization: Optional[str] = Header(default=None),
):
    """Lane (c): record that a LinkedIn touch was actually sent/accepted/replied.

    'replied' here does the same two things the Conversations lane does --
    stops the sequence and opens a conversation -- because a reply noticed
    while pasting is the same event as one noticed later, and making the user
    record it twice guarantees the funnel undercounts.
    """
    require_marketing_api_key(authorization)
    db = get_supabase()

    seq = (db.table("mse_dm_sequences").select("*").eq("id", sequence_id)
           .maybe_single().execute())
    seq = seq.data if seq else None
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")

    now = datetime.now(timezone.utc).isoformat()
    if body.event == "sent":
        db.table("mse_dm_sequences").update({"touch_1_sent_at": now}).eq("id", sequence_id).execute()
        return {"sequence_id": sequence_id, "event": "sent", "touch_1_sent_at": now}

    if body.event == "accepted":
        # No column for "connection accepted"; recording it in notes rather
        # than inventing a status that nothing else understands.
        note = f"[{now[:10]}] LinkedIn connection accepted"
        lead_id = seq.get("lead_finder_lead_id")
        if lead_id:
            cur = db.table("mse_leads").select("notes").eq("id", lead_id).maybe_single().execute()
            prior = ((cur.data if cur else None) or {}).get("notes") or ""
            db.table("mse_leads").update({"notes": (prior + "\n" + note).strip()}).eq("id", lead_id).execute()
        return {"sequence_id": sequence_id, "event": "accepted", "recorded": note}

    return await _record_reply(db, seq, source="ready_to_paste")


async def _record_reply(db, seq: dict, *, source: str) -> dict:
    """Stop the sequence and open a conversation. Idempotent."""
    now = datetime.now(timezone.utc).isoformat()
    db.table("mse_dm_sequences").update({"status": "replied"}).eq("id", seq["id"]).execute()

    lead_id = seq.get("lead_finder_lead_id")
    if not lead_id:
        return {"sequence_id": seq["id"], "event": "replied",
                "conversation": None,
                "note": "no mse_leads row to attach a conversation to"}

    existing = (db.table("mse_outreach_conversations").select("id,stage")
                .eq("lead_id", lead_id).maybe_single().execute())
    existing = existing.data if existing else None
    if existing:
        return {"sequence_id": seq["id"], "event": "replied",
                "conversation": existing["id"], "stage": existing["stage"],
                "note": "conversation already open"}

    row = db.table("mse_outreach_conversations").insert({
        "product_id": seq.get("product_id"),
        "lead_id": lead_id,
        "sequence_id": seq["id"],
        "stage": "replied",
        "replied_at": now,
        "notes": f"opened from {source}",
    }).execute()
    conv = (row.data or [{}])[0]
    return {"sequence_id": seq["id"], "event": "replied",
            "conversation": conv.get("id"), "stage": "replied"}


@router.get("/conversations")
async def list_conversations(
    limit: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    """Lane (d): everyone who replied, and how far it went."""
    require_marketing_api_key(authorization)
    db = get_supabase()
    convs = (db.table("mse_outreach_conversations").select("*")
             .order("updated_at", desc=True).limit(limit).execute().data or [])
    products = _products(db)
    out = []
    for c in convs:
        lead = {}
        if c.get("lead_id"):
            r = db.table("mse_leads").select(
                "company,first_name,last_name,title,email,linkedin_url"
            ).eq("id", c["lead_id"]).maybe_single().execute()
            lead = (r.data if r else None) or {}
        name = " ".join(x for x in [lead.get("first_name"), lead.get("last_name")] if x).strip()
        out.append({
            "conversation_id": c.get("id"),
            "lead_id": c.get("lead_id"),
            "product": (products.get(c.get("product_id")) or {}).get("name"),
            "company": lead.get("company"),
            "contact": {"name": name or None, "title": lead.get("title"),
                        "email": lead.get("email"), "linkedin_url": lead.get("linkedin_url")},
            "stage": c.get("stage"),
            "timestamps": {k: c.get(v) for k, v in STAGE_TIMESTAMP.items()},
            "lost_reason": c.get("lost_reason"),
            "notes": c.get("notes"),
            "updated_at": c.get("updated_at"),
        })
    by_stage: dict[str, int] = {}
    for c in convs:
        by_stage[c.get("stage") or "?"] = by_stage.get(c.get("stage") or "?", 0) + 1
    return {"conversations": out, "count": len(out), "by_stage": by_stage}


class StageUpdate(BaseModel):
    stage: str
    lost_reason: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("stage")
    @classmethod
    def _known_stage(cls, v):
        v = (v or "").strip().lower()
        if v not in STAGE_ORDER:
            raise ValueError(f"stage must be one of {', '.join(STAGE_ORDER)}")
        return v


@router.post("/conversations/{lead_id}/stage")
async def set_conversation_stage(
    lead_id: str,
    body: StageUpdate,
    authorization: Optional[str] = Header(default=None),
):
    """Lane (d): advance a conversation, creating it if the reply was noticed here.

    Forward-only for the progress stages: a mis-click must not regress a won
    deal to 'replied'. 'lost' is reachable from anywhere, because a deal can
    die at any point, and re-marking the SAME stage is allowed so a note or a
    lost_reason can be corrected.
    """
    require_marketing_api_key(authorization)
    db = get_supabase()

    lead = db.table("mse_leads").select("id,product_id").eq("id", lead_id).maybe_single().execute()
    lead = lead.data if lead else None
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    now = datetime.now(timezone.utc).isoformat()
    existing = (db.table("mse_outreach_conversations").select("*")
                .eq("lead_id", lead_id).maybe_single().execute())
    existing = existing.data if existing else None

    payload = {"stage": body.stage, "updated_at": now,
               STAGE_TIMESTAMP[body.stage]: now}
    if body.lost_reason is not None:
        payload["lost_reason"] = body.lost_reason
    if body.notes is not None:
        payload["notes"] = body.notes

    if not existing:
        payload.update({"product_id": lead.get("product_id"), "lead_id": lead_id})
        # Any stage implies a reply happened, so stamp it rather than leaving a
        # won deal with no reply date and breaking the funnel's first step.
        payload.setdefault("replied_at", now)
        row = db.table("mse_outreach_conversations").insert(payload).execute()
        if not row.data:
            raise HTTPException(status_code=500, detail="Could not open the conversation")
        return {"conversation_id": row.data[0]["id"], "stage": body.stage, "created": True}

    if body.stage != "lost":
        current = existing.get("stage") or "replied"
        if STAGE_ORDER.index(body.stage) < STAGE_ORDER.index(current):
            raise HTTPException(
                status_code=409,
                detail=(f"conversation is already at '{current}'; refusing to move it back to "
                        f"'{body.stage}'. Stages only move forward (except 'lost')."),
            )
    updated = (db.table("mse_outreach_conversations").update(payload)
               .eq("id", existing["id"]).execute())
    if not updated.data:
        raise HTTPException(status_code=500, detail="Could not update the conversation")
    return {"conversation_id": existing["id"], "stage": body.stage, "created": False}
