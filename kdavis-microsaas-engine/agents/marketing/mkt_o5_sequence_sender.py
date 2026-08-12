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

2026-08-12: CAN-SPAM compliance guard added (core/email_compliance.py) —
this agent previously sent real commercial email with no unsubscribe
mechanism and no suppression check, both legally required. Every send now
(a) skips a lead already in mse_email_suppressions before touching Resend
at all, and (b) has the physical mailing address + one-click unsubscribe
link appended to the body. A daily send cap (MARKETING_DAILY_SEND_CAP,
default 200) is a safety measure, not a legal requirement — stops a bad
batch or a runaway retry loop from sending far more mail in one day than
intended; remaining sequences simply stay in their current status for the
next hourly run to pick up, same as any other skip here.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import resend

from core.email_compliance import append_compliance_footer, daily_send_cap, is_suppressed, sends_today
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
    # maybe_single().execute() returns bare None (not a Response with
    # .data=None) when zero rows match — a deleted/bad lead_id is exactly
    # that case.
    result = db.table("mse_apollo_leads").select("email,first_name").eq("id", lead_id).maybe_single().execute()
    return result.data if result is not None else None


def run_send_touch_1(supabase_client: Optional[Any] = None, resend_client: Optional[Any] = None) -> dict:
    """Sends touch_1 for every approved-but-unsent sequence. Continues past
    a single lead's failure so one bad row doesn't block the whole batch —
    each failure is still audited (never silent), just doesn't raise."""
    db = supabase_client if supabase_client is not None else get_supabase()

    sequences = db.table("mse_dm_sequences").select("*").eq("status", "approved_hitl").execute().data or []
    _emit_event(db, "sequence_send_touch1_started", {"count": len(sequences)})

    cap = daily_send_cap()
    sent_count = sends_today(db)

    sent, failed, skipped = 0, [], []
    for seq in sequences:
        try:
            if sent_count >= cap:
                skipped.append(seq["id"])
                _write_audit(db, "lose", seq.get("product_id", ""), {"sequence_id": seq["id"], "touch": 1, "skipped": "daily_cap"})
                continue

            lead = _get_lead(db, seq["lead_id"])
            if not lead or not lead.get("email"):
                raise ValueError(f"No email on file for lead {seq['lead_id']}")

            if is_suppressed(db, lead["email"]):
                skipped.append(seq["id"])
                db.table("mse_dm_sequences").update({"status": "suppressed"}).eq("id", seq["id"]).execute()
                _write_audit(db, "lose", seq["product_id"], {"sequence_id": seq["id"], "touch": 1, "skipped": "suppressed"})
                continue

            first_name = lead.get("first_name") or ""
            subject = f"Quick question, {first_name}".strip() if first_name else "Quick question"
            body = append_compliance_footer(DataSanitizationShield.clean(seq["touch_1"]), lead["email"])
            _send_email(resend_client, lead["email"], subject, body)

            db.table("mse_dm_sequences").update({
                "status": "touch_1_sent",
                "touch_1_sent_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", seq["id"]).execute()
            sent += 1
            sent_count += 1
            _write_audit(db, "win", seq["product_id"], {"sequence_id": seq["id"], "touch": 1})
        except Exception as exc:
            failed.append(seq["id"])
            _write_audit(db, "lose", seq.get("product_id", ""), {"sequence_id": seq["id"], "touch": 1, "error": str(exc)})

    _emit_event(db, "sequence_send_touch1_completed", {"sent": sent, "failed": len(failed), "skipped": len(skipped)})
    return {"sent": sent, "failed": failed, "skipped": skipped}


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

    cap = daily_send_cap()
    sent_count = sends_today(db)

    sent, failed, skipped = 0, [], []
    for seq in sequences:
        try:
            if sent_count >= cap:
                skipped.append(seq["id"])
                _write_audit(db, "lose", seq.get("product_id", ""), {"sequence_id": seq["id"], "touch": 2, "skipped": "daily_cap"})
                continue

            lead = _get_lead(db, seq["lead_id"])
            if not lead or not lead.get("email"):
                raise ValueError(f"No email on file for lead {seq['lead_id']}")

            # A lead can unsubscribe in the 3-day gap between touch_1 and
            # touch_2 -- re-checking here, not just at touch_1, is the
            # whole point of a suppression check rather than a one-time
            # gate.
            if is_suppressed(db, lead["email"]):
                skipped.append(seq["id"])
                db.table("mse_dm_sequences").update({"status": "suppressed"}).eq("id", seq["id"]).execute()
                _write_audit(db, "lose", seq["product_id"], {"sequence_id": seq["id"], "touch": 2, "skipped": "suppressed"})
                continue

            first_name = lead.get("first_name") or ""
            subject = f"Following up, {first_name}".strip() if first_name else "Following up"
            body = append_compliance_footer(DataSanitizationShield.clean(seq["touch_2"]), lead["email"])
            _send_email(resend_client, lead["email"], subject, body)

            db.table("mse_dm_sequences").update({
                "status": "sequence_complete",
                "touch_2_sent_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", seq["id"]).execute()
            sent += 1
            sent_count += 1
            _write_audit(db, "win", seq["product_id"], {"sequence_id": seq["id"], "touch": 2})
        except Exception as exc:
            failed.append(seq["id"])
            _write_audit(db, "lose", seq.get("product_id", ""), {"sequence_id": seq["id"], "touch": 2, "error": str(exc)})

    _emit_event(db, "sequence_send_touch2_completed", {"sent": sent, "failed": len(failed), "skipped": len(skipped)})
    return {"sent": sent, "failed": failed, "skipped": skipped}


def run_sequence_sender(supabase_client: Optional[Any] = None, resend_client: Optional[Any] = None) -> dict:
    """Entry point for the /marketing/send-sequences trigger (n8n cron) —
    runs both touch stages in one pass."""
    t1 = run_send_touch_1(supabase_client=supabase_client, resend_client=resend_client)
    t2 = run_send_touch_2(supabase_client=supabase_client, resend_client=resend_client)
    return {"touch_1": t1, "touch_2": t2}
