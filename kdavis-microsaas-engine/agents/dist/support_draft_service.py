"""
Draft -> approve -> send round trip for Phase 7's support drafting layer.
This is the "human approves, reply goes out under a human name" path the
spec's own invariant requires (7.0: "Agents draft. A human approves. The
reply goes out under a human name.") -- not an agent function, a
human-triggered service the dashboard/API calls once an owner reviews a
pending mse_support_drafts row.

Reuses the exact Resend send pattern already proven in
agents/marketing/mkt_o5_sequence_sender.py::_send_email (same
RESEND_API_KEY/RESEND_FROM_EMAIL env vars, same dependency-injected
resend_client for testability) rather than inventing a second send path.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

import resend

from core.supabase_client import get_supabase

AGENT_ID = "sup-support-draft-service"


def _send_email(resend_client, to_email: str, subject: str, body: str, from_email: Optional[str] = None) -> None:
    # Real, live-verified finding (2026-08-30): the global RESEND_FROM_EMAIL
    # default (outreach@outreach.thdstack.com) sends from a domain Resend's
    # own /domains API reports as status="failed" -- every send using the
    # bare default currently errors. Support replies also shouldn't come
    # from a shared marketing-outreach address regardless -- from_email
    # lets a caller pass a real, product-specific, Resend-verified sender
    # (e.g. support@smallporthub.thdstack.com); falls back to the global
    # default only if the caller doesn't supply one.
    sender = from_email or os.environ.get("RESEND_FROM_EMAIL", "support@resend.dev")
    payload = {"from": sender, "to": to_email, "subject": subject, "text": body}
    if resend_client is not None:
        resend_client.Emails.send(payload)
        return
    resend.api_key = os.environ["RESEND_API_KEY"]
    resend.Emails.send(payload)


def _write_audit(db, outcome: str, product_id: Optional[str], metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "support_draft_send",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def approve_and_send(
    draft_id: str,
    approved_by: str,
    to_email: str,
    final_body: Optional[str] = None,
    subject: str = "Re: your support request",
    from_email: Optional[str] = None,
    supabase_client=None,
    resend_client=None,
) -> dict[str, Any]:
    """A real owner action: approves (or approves-with-edit, if final_body
    differs from the draft) a pending mse_support_drafts row and sends it.
    Raises on any failure -- never fails silently, matching this repo's
    established convention (mkt_o5_sequence_sender.py's own audit-every-
    outcome pattern). Does not touch support_autoanswer_enabled or any
    approve_positioning-style gate -- those are separate, unrelated
    guards."""
    db = supabase_client if supabase_client is not None else get_supabase()

    draft = db.table("mse_support_drafts").select("*").eq("id", draft_id).maybe_single().execute()
    draft = draft.data if draft is not None else None
    if not draft:
        raise ValueError(f"support_draft_service: no draft found for id {draft_id}")
    if draft["status"] not in ("pending",):
        raise ValueError(f"support_draft_service: draft {draft_id} is already {draft['status']!r}, cannot send again")

    body_to_send = final_body if final_body is not None else draft["draft_body"]
    was_edited = final_body is not None and final_body != draft["draft_body"]

    ticket = db.table("mse_support_tickets").select("*").eq("id", draft["ticket_id"]).maybe_single().execute()
    ticket = ticket.data if ticket is not None else None
    product_id = ticket.get("product_id") if ticket else None

    try:
        _send_email(resend_client, to_email, subject, body_to_send, from_email=from_email)
    except Exception as exc:
        _write_audit(db, "lose", product_id, {"draft_id": draft_id, "error": str(exc)})
        raise RuntimeError(f"support_draft_service: send failed for draft {draft_id}: {exc}") from exc

    now = datetime.now(timezone.utc).isoformat()
    updated = db.table("mse_support_drafts").update({
        "status": "edited" if was_edited else "approved",
        "final_body": body_to_send,
        "approved_by": approved_by,
        "sent_at": now,
    }).eq("id", draft_id).execute()

    if ticket:
        ticket_update = {"status": "answered"}
        if not ticket.get("first_response_at"):
            ticket_update["first_response_at"] = now
        db.table("mse_support_tickets").update(ticket_update).eq("id", draft["ticket_id"]).execute()

    _write_audit(db, "win", product_id, {"draft_id": draft_id, "edited": was_edited})
    return updated.data[0]


def reject_draft(draft_id: str, approved_by: str, supabase_client=None) -> dict[str, Any]:
    """The other real owner action -- a draft that shouldn't go out at
    all. Real per-item outcome, not a silent no-op."""
    db = supabase_client if supabase_client is not None else get_supabase()
    updated = db.table("mse_support_drafts").update({
        "status": "rejected",
        "approved_by": approved_by,
    }).eq("id", draft_id).eq("status", "pending").execute()
    if not updated.data:
        raise ValueError(f"support_draft_service: no pending draft found for id {draft_id}")
    return updated.data[0]
