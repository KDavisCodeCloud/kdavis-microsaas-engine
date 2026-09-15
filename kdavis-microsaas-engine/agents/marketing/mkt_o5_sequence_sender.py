"""
MKT-O5 Sequence Sender.

Sends the EMAIL side of HITL-approved cold outreach sequences via Resend.
touch_1 fires once a sequence is approved (status='approved_hitl'); touch_2
fires TOUCH_2_DELAY after touch_1 was sent. This agent never touches
LinkedIn — leads with linkedin_url populated are surfaced in the
dashboard's manual outreach queue instead (api/routers/outreach.py) for a
human to message natively; automating that channel carries real ToS/ban
risk with no official API to do it safely. Decided 2026-07-16.

lead_finder leads (2026-08-14, agents/marketing/mkt_lead_finder.py) DO
reach this agent — unlike LinkedIn leads, they have a real, SMTP-verified
email and are meant to be auto-sent same as apollo leads.
outreach.py's approve endpoint already routes lead_source="lead_finder"
sequences to 'approved_hitl' (this agent's actual query target); the only
change needed here is _get_lead() knowing to look in mse_leads (via
mse_dm_sequences.lead_finder_lead_id) when a sequence has no lead_id.

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
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import resend

from core.email_compliance import append_compliance_footer, daily_send_cap, is_suppressed, sends_today
from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

log = logging.getLogger(__name__)

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


def _get_active_product_ids(db) -> set:
    """Marketing stage-gate update (session 2026-09-15). A sequence can be
    approved while its product is active and then have the product move
    to 'warming' or 'building' before touch_1/touch_2 actually sends —
    MKT-O2 gates at write time, but this is the corresponding send-time
    check so a stage change after approval still stops the send, not just
    new sequence creation."""
    result = db.table("mse_icp_configs").select("product_id,selling_stage").eq("selling_stage", "active").execute()
    # Re-checks selling_stage in Python rather than trusting the .eq()
    # filter alone -- belt-and-suspenders, and it's what makes this
    # correct against tests/conftest.py's FakeSupabase too, which (by
    # design, documented on several other tests in this file) returns
    # the same canned rows regardless of which filter was applied.
    return {row["product_id"] for row in (result.data or []) if row.get("selling_stage") == "active"}


def _get_lead(db, seq: dict) -> Optional[dict]:
    # maybe_single().execute() returns bare None (not a Response with
    # .data=None) when zero rows match — a deleted/bad lead_id is exactly
    # that case. mse_dm_sequences carries exactly one of lead_id (apollo)
    # or lead_finder_lead_id (lead_finder) here — linkedin_lead_id-only
    # rows never reach this function, since they never leave
    # 'approved_manual' status, which this agent never polls for.
    if seq.get("lead_finder_lead_id"):
        result = db.table("mse_leads").select("email,first_name").eq("id", seq["lead_finder_lead_id"]).maybe_single().execute()
        return result.data if result is not None else None
    result = db.table("mse_apollo_leads").select("email,first_name").eq("id", seq["lead_id"]).maybe_single().execute()
    return result.data if result is not None else None


def _void_expired_approvals(db) -> int:
    """Finding 2 (2026-09-02 HITL audit): approvals go stale, not silently
    forever-pending. A row past its hitl_approved_expires_at is voided to
    'approval_expired' -- a real, visible terminal status a human has to
    notice and act on -- rather than sitting as 'approved_hitl' forever
    (looks still-valid) or auto-recycling back to pending_hitl (fires late
    with zero re-review, exactly the drift this audit exists to catch)."""
    now = datetime.now(timezone.utc).isoformat()
    result = (
        db.table("mse_dm_sequences")
        .update({"status": "approval_expired"})
        .eq("status", "approved_hitl")
        .lt("hitl_approved_expires_at", now)
        .execute()
    )
    return len(result.data or [])


def _claim_for_send(db, seq_id: str, from_status: str, claiming_status: str):
    """Finding 1 (2026-09-02 HITL audit): send and status-update were two
    separate, non-atomic steps with no guard on the update, so a crash
    between them (or two overlapping hourly runs) could send the same
    email twice. This is the atomic claim that closes it: a conditional
    UPDATE, WHERE id=x AND status=from_status. On real Postgres this
    affects at most one row even under concurrent callers -- whichever
    request's UPDATE commits first wins the row; the loser's WHERE matches
    zero rows. Returns True if this call won the claim, False if another
    run already had it (in which case: skip, don't send -- never treat a
    lost claim as an error)."""
    result = (
        db.table("mse_dm_sequences")
        .update({"status": claiming_status})
        .eq("id", seq_id)
        .eq("status", from_status)
        .execute()
    )
    return bool(result.data)


def _log_outreach_touch(db, seq: dict, touch: int) -> None:
    """DIST Phase 8 (mse_leads.stage + mse_activities, migrations
    20260831000035/20260907000042): only lead_finder-sourced sequences
    (seq["lead_finder_lead_id"] set) connect to mse_leads at all -- apollo
    and linkedin-sourced sequences track through mse_apollo_leads instead,
    which has no stage column, nothing to advance here. touch_1 moves a
    lead from 'new' to 'contacted' (.eq("stage", "new") makes this a
    no-op, not a regression, if a human already advanced it further via
    the leads page). touch_2 never changes stage -- 'contacted' already
    reflects "we've reached out"; there is no automated reply/bounce
    signal anywhere in this codebase (see mkt_o4_outreach_monitor.py's own
    docstring) to justify moving a lead past 'contacted' without a human
    saying so. Best-effort: a logging failure must never undo or fail a
    send that already succeeded."""
    lead_id = seq.get("lead_finder_lead_id")
    if not lead_id:
        return
    try:
        if touch == 1:
            db.table("mse_leads").update({
                "stage": "contacted",
                "last_activity_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", lead_id).eq("stage", "new").execute()
        else:
            db.table("mse_leads").update({
                "last_activity_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", lead_id).execute()
        db.table("mse_activities").insert({
            "product_id": seq.get("product_id"),
            "subject_type": "lead",
            "subject_id": lead_id,
            "kind": "outreach_sent",
            "body": f"touch_{touch} sent",
            "actor": AGENT_ID,
        }).execute()
    except Exception as exc:
        log.warning("mse_activities/stage update failed for lead %s (touch %d): %s", lead_id, touch, exc)


def _release_claim(db, seq_id: str, claiming_status: str, revert_to: str) -> None:
    """A send that fails after a successful claim must not leave the row
    stuck at the transient claiming status forever -- revert to the prior
    approved status so a later run retries it, same as a send that failed
    before ever claiming anything."""
    db.table("mse_dm_sequences").update({"status": revert_to}).eq("id", seq_id).eq("status", claiming_status).execute()


def run_send_touch_1(supabase_client: Optional[Any] = None, resend_client: Optional[Any] = None) -> dict:
    """Sends touch_1 for every approved-but-unsent sequence. Continues past
    a single lead's failure so one bad row doesn't block the whole batch —
    each failure is still audited (never silent), just doesn't raise."""
    db = supabase_client if supabase_client is not None else get_supabase()

    voided = _void_expired_approvals(db)
    if voided:
        _emit_event(db, "sequence_send_touch1_approvals_expired", {"count": voided})

    sequences = db.table("mse_dm_sequences").select("*").eq("status", "approved_hitl").execute().data or []
    _emit_event(db, "sequence_send_touch1_started", {"count": len(sequences)})

    active_product_ids = _get_active_product_ids(db)
    cap = daily_send_cap()
    sent_count = sends_today(db)

    sent, failed, skipped = 0, [], []
    for seq in sequences:
        try:
            if seq.get("product_id") not in active_product_ids:
                skipped.append(seq["id"])
                _write_audit(db, "lose", seq.get("product_id", ""), {"sequence_id": seq["id"], "touch": 1, "skipped": "selling_stage_not_active"})
                continue

            if sent_count >= cap:
                skipped.append(seq["id"])
                _write_audit(db, "lose", seq.get("product_id", ""), {"sequence_id": seq["id"], "touch": 1, "skipped": "daily_cap"})
                continue

            lead = _get_lead(db, seq)
            if not lead or not lead.get("email"):
                raise ValueError(f"No email on file for lead {seq.get('lead_finder_lead_id') or seq.get('lead_id')}")

            if is_suppressed(db, lead["email"]):
                skipped.append(seq["id"])
                db.table("mse_dm_sequences").update({"status": "suppressed"}).eq("id", seq["id"]).eq("status", "approved_hitl").execute()
                _write_audit(db, "lose", seq["product_id"], {"sequence_id": seq["id"], "touch": 1, "skipped": "suppressed"})
                continue

            if not _claim_for_send(db, seq["id"], "approved_hitl", "touch_1_sending"):
                # Another concurrent run already claimed (or already sent)
                # this row -- not a failure, just not ours to send.
                skipped.append(seq["id"])
                _write_audit(db, "lose", seq["product_id"], {"sequence_id": seq["id"], "touch": 1, "skipped": "already_claimed"})
                continue

            try:
                first_name = lead.get("first_name") or ""
                subject = f"Quick question, {first_name}".strip() if first_name else "Quick question"
                body = append_compliance_footer(DataSanitizationShield.clean(seq["touch_1"]), lead["email"])
                _send_email(resend_client, lead["email"], subject, body)
            except Exception:
                _release_claim(db, seq["id"], "touch_1_sending", "approved_hitl")
                raise

            db.table("mse_dm_sequences").update({
                "status": "touch_1_sent",
                "touch_1_sent_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", seq["id"]).eq("status", "touch_1_sending").execute()
            _log_outreach_touch(db, seq, touch=1)
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

    active_product_ids = _get_active_product_ids(db)
    cap = daily_send_cap()
    sent_count = sends_today(db)

    sent, failed, skipped = 0, [], []
    for seq in sequences:
        try:
            if seq.get("product_id") not in active_product_ids:
                skipped.append(seq["id"])
                _write_audit(db, "lose", seq.get("product_id", ""), {"sequence_id": seq["id"], "touch": 2, "skipped": "selling_stage_not_active"})
                continue

            if sent_count >= cap:
                skipped.append(seq["id"])
                _write_audit(db, "lose", seq.get("product_id", ""), {"sequence_id": seq["id"], "touch": 2, "skipped": "daily_cap"})
                continue

            lead = _get_lead(db, seq)
            if not lead or not lead.get("email"):
                raise ValueError(f"No email on file for lead {seq.get('lead_finder_lead_id') or seq.get('lead_id')}")

            # A lead can unsubscribe in the 3-day gap between touch_1 and
            # touch_2 -- re-checking here, not just at touch_1, is the
            # whole point of a suppression check rather than a one-time
            # gate.
            if is_suppressed(db, lead["email"]):
                skipped.append(seq["id"])
                db.table("mse_dm_sequences").update({"status": "suppressed"}).eq("id", seq["id"]).eq("status", "touch_1_sent").execute()
                _write_audit(db, "lose", seq["product_id"], {"sequence_id": seq["id"], "touch": 2, "skipped": "suppressed"})
                continue

            if not _claim_for_send(db, seq["id"], "touch_1_sent", "touch_2_sending"):
                skipped.append(seq["id"])
                _write_audit(db, "lose", seq["product_id"], {"sequence_id": seq["id"], "touch": 2, "skipped": "already_claimed"})
                continue

            try:
                first_name = lead.get("first_name") or ""
                subject = f"Following up, {first_name}".strip() if first_name else "Following up"
                body = append_compliance_footer(DataSanitizationShield.clean(seq["touch_2"]), lead["email"])
                _send_email(resend_client, lead["email"], subject, body)
            except Exception:
                _release_claim(db, seq["id"], "touch_2_sending", "touch_1_sent")
                raise

            db.table("mse_dm_sequences").update({
                "status": "sequence_complete",
                "touch_2_sent_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", seq["id"]).eq("status", "touch_2_sending").execute()
            _log_outreach_touch(db, seq, touch=2)
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
