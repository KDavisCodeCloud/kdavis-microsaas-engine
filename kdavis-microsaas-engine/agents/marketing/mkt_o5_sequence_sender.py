"""
MKT-O5 Sequence Sender.

Sends the EMAIL side of HITL-approved cold outreach sequences via Resend.
touch_1 fires once a sequence is approved (status='approved_hitl'); touch_2
fires TOUCH_2_DELAY after touch_1 was sent. This agent never touches
LinkedIn — leads with linkedin_url populated are surfaced in the
dashboard's manual outreach queue instead (api/routers/outreach.py) for a
human to message natively; automating that channel carries real ToS/ban
risk with no official API to do it safely. Decided 2026-07-16.

touch_1/touch_2 were written by MKT-O2 as short DM-style copy, not
email-formatted (no separate subject line) — this agent supplies a plain,
generic subject rather than truncating the DM body into one, since a
truncated mid-sentence snippet reads as broken, not as a real subject.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import resend

from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

AGENT_ID = "mkt-o5"
TOUCH_2_DELAY = timedelta(days=3)


def _emit_event(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": event_type,
        "metadata": metadata,
    }).execute()


def _write_audit(db, outcome: str, product_id: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "sequence_send",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def _send_email(resend_client, to_email: str, subject: str, body: str) -> None:
    if resend_client is not None:
        resend_client.Emails.send({
            "from": os.environ.get("RESEND_FROM_EMAIL", "outreach@resend.dev"),
            "to": to_email,
            "subject": subject,
            "text": body,
        })
        return
    resend.api_key = os.environ["RESEND_API_KEY"]
    resend.Emails.send({
        "from": os.environ.get("RESEND_FROM_EMAIL", "outreach@resend.dev"),
        "to": to_email,
        "subject": subject,
        "text": body,
    })


def _get_lead(db, lead_id: str) -> Optional[dict]:
    result = db.table("mse_apollo_leads").select("email,first_name").eq("id", lead_id).maybe_single().execute()
    return result.data


def run_send_touch_1(supabase_client: Optional[Any] = None, resend_client: Optional[Any] = None) -> dict:
    """Sends touch_1 for every approved-but-unsent sequence. Continues past
    a single lead's failure so one bad row doesn't block the whole batch —
    each failure is still audited (never silent), just doesn't raise."""
    db = supabase_client if supabase_client is not None else get_supabase()

    sequences = db.table("mse_dm_sequences").select("*").eq("status", "approved_hitl").execute().data or []
    _emit_event(db, "sequence_send_touch1_started", {"count": len(sequences)})

    sent, failed = 0, []
    for seq in sequences:
        try:
            lead = _get_lead(db, seq["lead_id"])
            if not lead or not lead.get("email"):
                raise ValueError(f"No email on file for lead {seq['lead_id']}")

            first_name = lead.get("first_name") or ""
            subject = f"Quick question, {first_name}".strip() if first_name else "Quick question"
            body = DataSanitizationShield.clean(seq["touch_1"])
            _send_email(resend_client, lead["email"], subject, body)

            db.table("mse_dm_sequences").update({
                "status": "touch_1_sent",
                "touch_1_sent_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", seq["id"]).execute()
            sent += 1
            _write_audit(db, "win", seq["product_id"], {"sequence_id": seq["id"], "touch": 1})
        except Exception as exc:
            failed.append(seq["id"])
            _write_audit(db, "lose", seq.get("product_id", ""), {"sequence_id": seq["id"], "touch": 1, "error": str(exc)})

    _emit_event(db, "sequence_send_touch1_completed", {"sent": sent, "failed": len(failed)})
    return {"sent": sent, "failed": failed}


def run_send_touch_2(supabase_client: Optional[Any] = None, resend_client: Optional[Any] = None) -> dict:
    """Sends touch_2 for every sequence whose touch_1 was sent at least
    TOUCH_2_DELAY ago and hasn't had touch_2 sent yet."""
    db = supabase_client if supabase_client is not None else get_supabase()

    cutoff = (datetime.now(timezone.utc) - TOUCH_2_DELAY).isoformat()
    sequences = (
        db.table("mse_dm_sequences")
        .select("*")
        .eq("status", "touch_1_sent")
        .lte("touch_1_sent_at", cutoff)
        .execute()
        .data
        or []
    )
    _emit_event(db, "sequence_send_touch2_started", {"count": len(sequences)})

    sent, failed = 0, []
    for seq in sequences:
        try:
            lead = _get_lead(db, seq["lead_id"])
            if not lead or not lead.get("email"):
                raise ValueError(f"No email on file for lead {seq['lead_id']}")

            first_name = lead.get("first_name") or ""
            subject = f"Following up, {first_name}".strip() if first_name else "Following up"
            body = DataSanitizationShield.clean(seq["touch_2"])
            _send_email(resend_client, lead["email"], subject, body)

            db.table("mse_dm_sequences").update({
                "status": "sequence_complete",
                "touch_2_sent_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", seq["id"]).execute()
            sent += 1
            _write_audit(db, "win", seq["product_id"], {"sequence_id": seq["id"], "touch": 2})
        except Exception as exc:
            failed.append(seq["id"])
            _write_audit(db, "lose", seq.get("product_id", ""), {"sequence_id": seq["id"], "touch": 2, "error": str(exc)})

    _emit_event(db, "sequence_send_touch2_completed", {"sent": sent, "failed": len(failed)})
    return {"sent": sent, "failed": failed}


def run_sequence_sender(supabase_client: Optional[Any] = None, resend_client: Optional[Any] = None) -> dict:
    """Entry point for the /marketing/send-sequences trigger (n8n cron) —
    runs both touch stages in one pass."""
    t1 = run_send_touch_1(supabase_client=supabase_client, resend_client=resend_client)
    t2 = run_send_touch_2(supabase_client=supabase_client, resend_client=resend_client)
    return {"touch_1": t1, "touch_2": t2}
