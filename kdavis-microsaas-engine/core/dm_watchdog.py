"""
core/dm_watchdog.py

"A failed 8am run sends me an alert email" (Kelvin, 2026-10-19).

The 08:00 MST LinkedIn enrollment is fired by n8n, and its endpoint answers
{"status": "queued"} before any work happens -- so neither n8n nor the
exception path can be trusted to notice a failure. On 2026-10-19 the run
stalled after one query and left no row, no error and no log line at all.

So this is a watchdog, not an exception hook: at 08:45 MST (15:45 UTC) it
looks for today's audit_log row action='linkedin_dm_sequences_run'
(written by api/routers/linkedin_intake.py run_linkedin_dm_sequences). No
row, or outcome 'lose', emails the owner once. It runs inside mse-api, so it
still fires when n8n itself is down. The once-a-day guarantee is the same DB
claim row the lead schedules use (mse_scheduled_job_runs).

Sender: Resend, from alerts@<RESEND_FROM_EMAIL's domain> -- the domain the
daily digest already delivers from. Recipient: KELVIN_EMAIL.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Callable, Optional

log = logging.getLogger(__name__)

CHECK_HOUR_UTC, CHECK_MINUTE_UTC = 15, 45  # 08:45 MST (Arizona: no DST)
RUN_ACTION = "linkedin_dm_sequences_run"
DASHBOARD_URL = "https://ceo.thdecodedempire.com/dashboard/loop-map"


def due_period(now: datetime) -> Optional[str]:
    if (now.hour, now.minute) >= (CHECK_HOUR_UTC, CHECK_MINUTE_UTC):
        return now.date().isoformat()
    return None


def todays_run(db, day: str) -> Optional[dict]:
    rows = (db.table("audit_log").select("created_at,outcome,metadata")
            .eq("action", RUN_ACTION).gte("created_at", f"{day}T00:00:00+00:00")
            .order("created_at", desc=True).limit(1).execute().data or [])
    return rows[0] if rows else None


def diagnose(run: Optional[dict], day: str) -> Optional[str]:
    """The problem to alert on, or None if today's run is healthy."""
    if run is None:
        return (f"No 08:00 LinkedIn enrollment run recorded on {day}. Either n8n did not call "
                f"POST /marketing/linkedin/dm-sequences, or the run stalled before finishing.")
    if run.get("outcome") != "win":
        errors = (run.get("metadata") or {}).get("errors") or {}
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items()) or "no error text recorded"
        return f"The 08:00 LinkedIn enrollment run on {day} failed: {detail}"
    return None


def send_alert(subject: str, body: str, *, send: Optional[Callable[[dict], object]] = None) -> dict:
    to = os.environ.get("KELVIN_EMAIL")
    if not to:
        raise RuntimeError("KELVIN_EMAIL is not set on mse-api; the 8am alert has nowhere to go")
    sender = os.environ.get("RESEND_FROM_EMAIL", "")
    if "@" not in sender:
        raise RuntimeError("RESEND_FROM_EMAIL is not set; cannot pick the alert's sending domain")
    payload = {"from": f"THD Alerts <alerts@{sender.split('@', 1)[1].strip().strip('>')}>",
               "to": [to], "subject": subject, "text": body}
    if send is None:
        import resend
        resend.api_key = os.environ["RESEND_API_KEY"]
        send = resend.Emails.send
    result = send(payload)
    return {"sent_to_domain": to.split("@")[-1], "resend_id": (result or {}).get("id") if isinstance(result, dict) else None}


def check(db, run_id=None, *, now: Optional[datetime] = None, send=None) -> dict:
    """Scheduled-job body (agents/marketing/lead_schedule.py)."""
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    run = todays_run(db, day)
    problem = diagnose(run, day)
    if not problem:
        return {"alerted": False, "run_outcome": "win", "run_at": run["created_at"]}
    body = (f"{problem}\n\nNothing was drafted by this run, so new leads are not reaching your "
            f"approval queue.\n\nLoop Map: {DASHBOARD_URL}\n"
            f"Re-run by hand: POST /marketing/linkedin/dm-sequences (MARKETING_API_KEY).\n")
    sent = send_alert(f"8am LinkedIn enrollment failed ({day})", body, send=send)
    log.error("[DMWatchdog] alerted: %s", problem)
    db.table("audit_log").insert({"agent_id": "dm-watchdog", "action": "dm_8am_alert_sent",
                                  "outcome": "win", "metadata": {"day": day, "problem": problem, **sent}}).execute()
    return {"alerted": True, "problem": problem, **sent}
