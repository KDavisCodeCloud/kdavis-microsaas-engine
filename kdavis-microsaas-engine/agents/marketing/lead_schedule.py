"""
agents/marketing/lead_schedule.py

In-process schedules for the 2026-10-18 lead sources (Kelvin, 2026-10-19).

  pain_rescan   weekly, after the Sunday ATS scout     consulting + Cloud Decoded
  hn_scout      monthly, on the 3rd                    consulting (source=hn_thread)
  partner_scout monthly, on the 1st, <=20 Brave        partners   (new firms only)
  vc_scout      monthly, on the 1st                    partners   (new contacts only)
  dm_8am_watchdog  daily 08:45 MST: email the owner if the 08:00 enrollment failed
                   (core/dm_watchdog.py; no run row -- it is a check, not a scout)

Why in-process: same reason as the Loop Map's hourly history
(api/routers/loop_map.py snapshot_loop) -- this service cannot read or verify
n8n executions, and a job nobody can prove ran is not a job.

Double-run guard: mse_scheduled_job_runs has a unique index on
(job_key, product_id, period_key). Claiming a run IS inserting that row, so a
second replica or a restarted process gets a unique violation and stands down.
Each claim records a per-process token, so our own insert retried after a
dropped connection is recognised as ours rather than someone else's.

Every scheduled run writes an mse_lead_finder_runs row (created before the
work starts, so a crash leaves a visible row) and audit_log through the
lead_sources entry points. A window is several days wide so a deploy or
outage on the day itself still catches up.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from core.supabase_client import get_supabase_for_job

log = logging.getLogger(__name__)

AGENT_ID = "mkt-lead-schedule"
TICK_SECONDS = 15 * 60
PARTNER_SCHEDULED_MAX_QUERIES = 20
VC_SCHEDULED_MAX_PROBES = 60
# A claim older than this that never finished belongs to a process that died.
ABANDONED_AFTER = timedelta(hours=6)
# The Sunday scout is fired by n8n at 13:00 UTC; the rescan waits for it to
# finish, but not past this hour.
SUNDAY_SCOUT_HOUR_UTC = 13
PAIN_RESCAN_DEADLINE_HOUR_UTC = 20

INSTANCE_TOKEN = uuid.uuid4().hex


@dataclass(frozen=True)
class Job:
    key: str
    product_id: str
    source: Optional[str]  # mse_lead_finder_runs.sources_used[0]; None = no run row (watchdogs)
    period: Callable[[datetime], Optional[str]]  # period key if due now, else None
    run: Callable[..., dict]  # run(db, run_id) -> result dict


def _monthly(first_day: int, last_day: int, hour: int) -> Callable[[datetime], Optional[str]]:
    def period(now: datetime) -> Optional[str]:
        if first_day <= now.day <= last_day and (now.day > first_day or now.hour >= hour):
            return now.strftime("%Y-%m")
        return None
    return period


def _sunday_after_scout(product_id: str) -> Callable[..., Optional[str]]:
    """Due from Sunday 13:00 UTC through Monday; the period is that Sunday."""
    def period(now: datetime) -> Optional[str]:
        if now.weekday() == 6 and now.hour >= SUNDAY_SCOUT_HOUR_UTC:
            return now.date().isoformat()
        if now.weekday() == 0:
            return (now.date() - timedelta(days=1)).isoformat()
        return None
    return period


def scout_still_running(db, product_id: str, sunday: str, now: datetime) -> bool:
    """True while that Sunday's ATS scout for the product is pending/running
    and the deadline has not passed -- the rescan should read its leads."""
    if now.date().isoformat() == sunday and now.hour >= PAIN_RESCAN_DEADLINE_HOUR_UTC:
        return False
    if now.date().isoformat() != sunday:
        return False
    rows = (db.table("mse_lead_finder_runs").select("status,sources_used")
            .eq("product_id", product_id).gte("started_at", f"{sunday}T00:00:00+00:00")
            .in_("status", ["pending", "running"]).execute().data or [])
    side = {"pain_rescan", "hn_thread"}
    if any(not set(r.get("sources_used") or []) & side for r in rows):
        return True
    # The n8n call may not have landed yet.
    started = (db.table("mse_lead_finder_runs").select("id")
               .eq("product_id", product_id).gte("started_at", f"{sunday}T00:00:00+00:00")
               .execute().data or [])
    return not started and now.hour < SUNDAY_SCOUT_HOUR_UTC + 2


def partner_page_offset(today: date) -> int:
    """Brave result page for this month's partner run. Page 0 was the
    2026-10 manual runs; each month after asks for the next page, cycling
    through the first five, so the same queries keep surfacing new firms."""
    months = (today.year - 2026) * 12 + (today.month - 10)
    return months % 5


# ── job bodies ────────────────────────────────────────────────────────────

def _run_pain(product_id: str):
    def run(db, run_id: str) -> dict:
        from agents.marketing.lead_sources import run_pain_rescan
        return run_pain_rescan(db, product_id=product_id, run_id=run_id)
    return run


def _run_hn(db, run_id: str) -> dict:
    from agents.marketing.lead_sources import run_hn_scout
    return run_hn_scout(db, months=1, run_id=run_id)


def _run_partners(db, run_id: str) -> dict:
    from agents.marketing.brave_budget import check_budget
    from agents.marketing.lead_sources import _finish_run, run_partner_scout
    budget = check_budget(db)
    if not budget.allowed:
        msg = f"Brave budget guard refused the scheduled partner run: {budget.reason}"
        _finish_run(db, run_id, written=0, funnel={"brave_budget": budget.as_dict()}, error=msg)
        raise RuntimeError(msg)
    return run_partner_scout(db, max_queries=PARTNER_SCHEDULED_MAX_QUERIES, max_firms=25, run_id=run_id,
                             page_offset=partner_page_offset(datetime.now(timezone.utc).date()))


def _run_vc(db, run_id: str) -> dict:
    from agents.marketing.lead_sources import run_vc_scout_entry
    return run_vc_scout_entry(db, max_probes=VC_SCHEDULED_MAX_PROBES, run_id=run_id)


def jobs() -> list[Job]:
    from agents.marketing.mkt_lead_finder import CLOUD_DECODED_PRODUCT_ID, THDAGENTIC_CONSULTING_PRODUCT_ID
    from agents.marketing.partner_sourcing import PARTNERS_PRODUCT_ID
    return [
        Job("pain_rescan", THDAGENTIC_CONSULTING_PRODUCT_ID, "pain_rescan",
            _sunday_after_scout(THDAGENTIC_CONSULTING_PRODUCT_ID), _run_pain(THDAGENTIC_CONSULTING_PRODUCT_ID)),
        Job("pain_rescan", CLOUD_DECODED_PRODUCT_ID, "pain_rescan",
            _sunday_after_scout(CLOUD_DECODED_PRODUCT_ID), _run_pain(CLOUD_DECODED_PRODUCT_ID)),
        Job("hn_scout", THDAGENTIC_CONSULTING_PRODUCT_ID, "hn_thread", _monthly(3, 7, 14), _run_hn),
        Job("partner_scout", PARTNERS_PRODUCT_ID, "partner_signal", _monthly(1, 5, 14), _run_partners),
        Job("vc_scout", PARTNERS_PRODUCT_ID, "vc_operating_partner", _monthly(1, 5, 15), _run_vc),
        Job("dm_8am_watchdog", THDAGENTIC_CONSULTING_PRODUCT_ID, None, _watchdog_period, _run_watchdog),
    ]


def _watchdog_period(now: datetime) -> Optional[str]:
    from core.dm_watchdog import due_period
    return due_period(now)


def _run_watchdog(db, run_id) -> dict:
    from core.dm_watchdog import check
    return check(db)


# ── the lock ──────────────────────────────────────────────────────────────

def _is_unique_violation(exc: Exception) -> bool:
    return "23505" in str(getattr(exc, "code", "") or "") or "23505" in str(exc) \
        or "duplicate key" in str(exc).lower()


def claim(db, job: Job, period_key: str) -> Optional[str]:
    """Insert the claim row. Returns its id, or None if this period is
    already claimed by anyone else."""
    try:
        row = db.table("mse_scheduled_job_runs").insert({
            "job_key": job.key, "product_id": job.product_id, "period_key": period_key,
            "status": "claimed", "result": {"claimed_by": INSTANCE_TOKEN},
        }).execute()
        return row.data[0]["id"] if row.data else None
    except Exception as exc:
        if not _is_unique_violation(exc):
            raise
    existing = (db.table("mse_scheduled_job_runs").select("id,status,result,run_id")
                .eq("job_key", job.key).eq("product_id", job.product_id).eq("period_key", period_key)
                .execute().data or [])
    mine = existing and (existing[0].get("result") or {}).get("claimed_by") == INSTANCE_TOKEN \
        and existing[0].get("status") == "claimed" and not existing[0].get("run_id")
    return existing[0]["id"] if mine else None


def _finish_claim(db, claim_id: str, *, status: str, run_id: Optional[str], result: dict,
                  error: Optional[str] = None) -> None:
    db.table("mse_scheduled_job_runs").update({
        "status": status, "run_id": run_id, "error": (error or None) and error[:1000],
        "result": {"claimed_by": INSTANCE_TOKEN, **result},
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", claim_id).execute()


def fail_abandoned(db, now: datetime) -> int:
    """Claims a dead process left 'claimed' become 'failed', and their run row
    too, so the Loop Map shows the failure instead of a run 'in progress'
    forever. The period is NOT re-run automatically: a half-finished
    partner run already spent its Brave queries."""
    cutoff = (now - ABANDONED_AFTER).isoformat()
    stale = (db.table("mse_scheduled_job_runs").select("id,run_id,job_key")
             .eq("status", "claimed").lt("claimed_at", cutoff).execute().data or [])
    for row in stale:
        msg = "process stopped mid-run (deploy or restart); not re-run automatically"
        db.table("mse_scheduled_job_runs").update(
            {"status": "failed", "error": msg, "finished_at": now.isoformat()}).eq("id", row["id"]).execute()
        if row.get("run_id"):
            db.table("mse_lead_finder_runs").update(
                {"status": "failed", "error_message": msg, "completed_at": now.isoformat()}
            ).eq("id", row["run_id"]).in_("status", ["pending", "running"]).execute()
    return len(stale)


def _audit(db, outcome: str, job: Job, metadata: dict) -> None:
    try:
        db.table("audit_log").insert({"agent_id": AGENT_ID, "action": f"scheduled_{job.key}",
                                      "outcome": outcome, "product_id": job.product_id,
                                      "metadata": metadata}).execute()
    except Exception as exc:
        log.error("[LeadSchedule] audit_log write failed: %s", exc)


# ── one tick ──────────────────────────────────────────────────────────────

def run_job(db, job: Job, period_key: str) -> Optional[dict]:
    """Claim, create the run row, run, record. None if someone else has it."""
    claim_id = claim(db, job, period_key)
    if not claim_id:
        return None
    run_id = None
    if job.source:
        run = db.table("mse_lead_finder_runs").insert(
            {"product_id": job.product_id, "status": "pending", "sources_used": [job.source]}).execute()
        run_id = run.data[0]["id"]
        db.table("mse_scheduled_job_runs").update({"run_id": run_id}).eq("id", claim_id).execute()
    meta = {"period": period_key, "run_id": run_id, "claim_id": claim_id}
    try:
        result = job.run(db, run_id)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        # The entry points mark their own run row failed; this covers a raise
        # before they got that far.
        if run_id:
            db.table("mse_lead_finder_runs").update(
                {"status": "failed", "error_message": err[:1000],
                 "completed_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", run_id).in_("status", ["pending", "running"]).execute()
        _finish_claim(db, claim_id, status="failed", run_id=run_id, result={}, error=err)
        _audit(db, "lose", job, {**meta, "error": err[:500]})
        log.error("[LeadSchedule] %s %s failed: %s", job.key, period_key, err)
        return {"job": job.key, "status": "failed", **meta, "error": err}
    summary = {k: result.get(k) for k in ("leads_written", "scanned", "matched", "newly_matched_leads",
                                          "alerted", "problem") if k in result}
    _finish_claim(db, claim_id, status="complete", run_id=run_id, result=summary)
    _audit(db, "win", job, {**meta, **summary})
    log.warning("[LeadSchedule] %s %s complete: %s", job.key, period_key, summary)
    return {"job": job.key, "status": "complete", **meta, **summary}


def tick(db=None, now: Optional[datetime] = None, job_list: Optional[list[Job]] = None) -> list[dict]:
    """Run every job that is due and unclaimed for its current period."""
    db = db or get_supabase_for_job()
    now = now or datetime.now(timezone.utc)
    fail_abandoned(db, now)
    done = []
    for job in job_list if job_list is not None else jobs():
        period_key = job.period(now)
        if not period_key:
            continue
        already = (db.table("mse_scheduled_job_runs").select("id")
                   .eq("job_key", job.key).eq("product_id", job.product_id).eq("period_key", period_key)
                   .execute().data or [])
        if already:
            continue
        if job.key == "pain_rescan" and scout_still_running(db, job.product_id, period_key, now):
            continue
        out = run_job(db, job, period_key)
        if out:
            done.append(out)
    return done


async def schedule_loop() -> None:
    """Started from api/main.py's lifespan. Every TICK_SECONDS; a failing
    tick is logged and the loop carries on."""
    import asyncio
    await asyncio.sleep(120)
    while True:
        try:
            ran = await asyncio.to_thread(tick)
            if ran:
                log.warning("[LeadSchedule] tick ran %s", [(r["job"], r["status"]) for r in ran])
        except Exception as exc:
            log.error("[LeadSchedule] tick raised: %s", exc)
        await asyncio.sleep(TICK_SECONDS)
