"""
LinkedIn lead intake routes.

Internal/n8n-triggered, same auth pattern as api/routers/marketing.py:
shared-secret Bearer MARKETING_API_KEY, no tenant JWT (there's no
end-customer tenant of the marketing engine itself). Registered in
api/middleware/tenant_context.py's PUBLIC_PATHS for that reason — see
require_marketing_api_key()'s own docstring in api/middleware/auth.py.

Reading (GET /marketing/linkedin/leads) is deliberately a real backend
endpoint rather than the "frontend reads Supabase directly" convention
api/routers/outreach.py uses for mse_dm_sequences/mse_apollo_leads — this
one is what n8n/linkedin_outreach_workflow.json polls each morning to
know which leads are waiting, and an n8n HTTP node has no Supabase
session of its own to read through.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel

from agents.marketing.mkt_li_intake import parse_csv_leads, run_li_intake
from api.middleware.auth import require_marketing_api_key
from core.supabase_client import get_supabase, get_supabase_for_job

router = APIRouter(prefix="/marketing/linkedin", tags=["marketing", "linkedin"])


class LinkedInIntakeBody(BaseModel):
    leads: list[dict]
    source: str = "linkedin_manual"
    product_id: Optional[str] = None


@router.post("/intake")
async def linkedin_intake(request: Request, authorization: Optional[str] = Header(default=None)):
    """
    Accepts either a CSV file upload (multipart/form-data: file, source,
    product_id form fields) or a JSON body ({leads, source, product_id}) —
    the same endpoint covers all three input paths from the task spec
    (CSV export upload, manual JSON paste, and pasted engager profile
    lists) since an engager list is just JSON with source="linkedin_engager".
    """
    require_marketing_api_key(authorization)

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None:
            raise HTTPException(status_code=400, detail="file is required for a multipart upload")
        source = str(form.get("source") or "linkedin_manual")
        product_id = form.get("product_id")
        product_id = str(product_id) if product_id else None
        raw_bytes = await upload.read()
        leads = parse_csv_leads(raw_bytes.decode("utf-8"))
    else:
        body_json = await request.json()
        body = LinkedInIntakeBody(**body_json)
        leads = body.leads
        source = body.source
        product_id = body.product_id

    if source not in ("linkedin_manual", "linkedin_engager"):
        raise HTTPException(status_code=400, detail=f"source must be linkedin_manual or linkedin_engager, got {source!r}")

    result = run_li_intake(leads=leads, source=source, product_id=product_id)
    return {"added": result["added"], "duplicates_skipped": result["duplicates_skipped"]}


@router.get("/leads")
async def list_linkedin_leads(
    status: Optional[str] = None,
    source: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """
    Used by n8n/linkedin_outreach_workflow.json to pull the day's pending
    leads (engager query first, manual query second — the workflow itself
    owns that ordering by calling this twice, see the workflow file)."""
    require_marketing_api_key(authorization)
    db = get_supabase()

    query = db.table("mse_linkedin_leads").select("*")
    if status:
        query = query.eq("status", status)
    if source:
        query = query.eq("source", source)
    result = query.order("created_at").execute()
    return {"leads": result.data or []}


@router.post("/dm-sequences")
async def trigger_linkedin_dm_sequences(
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(default=None),
):
    """
    Triggers MKT-O2 (run_o2_for_linkedin_leads) for every product that
    currently has pending_dm leads from ANY source it handles (LinkedIn
    engager/manual leads in mse_linkedin_leads, or lead-finder leads in
    mse_leads) — the single call n8n's daily workflow makes after its two
    GET /marketing/linkedin/leads polls above. Looks up each product's
    most recent MKT-R1 research report itself (mse_research_reports) so
    n8n never has to carry that payload around; a product with no
    research report yet is skipped, not failed, since MKT-O2 has nothing
    to ground the copy in.

    Fixed 2026-08-27: product discovery only ever checked
    mse_linkedin_leads, so a product with pending_dm rows in mse_leads
    (lead_finder-sourced) but zero mse_linkedin_leads rows was silently
    never picked up here at all — run_o2_for_linkedin_leads itself already
    processes mse_leads correctly once called for a product_id (see its
    own docstring), it just never got called for lead-finder-only
    products. Discovery now unions both sources.
    """
    require_marketing_api_key(authorization)
    background_tasks.add_task(_run_linkedin_dm_sequences)
    return {"status": "queued"}


def _run_linkedin_dm_sequences() -> None:
    """
    Fixed 2026-09-29 -- this function is the daily enrollment engine
    (n8n "LinkedIn Outreach — Daily 8am MST") and had been ACTIVE but
    completely inert since at least 2026-09-22: every scheduled run
    discovered zero products and therefore never called MKT-O2 once,
    while 62 pending_dm leads sat untouched. Two independent bugs, either
    one alone sufficient to produce that:

    1. Discovery filtered mse_leads on email_status='verified'. Zero of
       the 62 pending_dm rows are 'verified' (58 unverified, 4
       catch_all), and mse_linkedin_leads had no pending_dm rows at all,
       so `product_ids` was always the empty set and the loop below never
       iterated. That filter is per-SOURCE policy, not discovery policy,
       and run_o2_for_linkedin_leads already applies it correctly to the
       one source that wants it (lead_finder) while deliberately NOT
       applying it to job_posting_signal / cloud_decoded_job_signal --
       those are LinkedIn-DM/manual-send leads that typically have no
       email at all. That exact reasoning is already written out in
       run_o2_for_linkedin_leads' own job_posting_signal block; the fix
       had simply never been carried up here into discovery, so the
       function that knows better was never reached.

    2. `if not reports: continue` skipped any product with no MKT-R1
       research report. None of the three products that actually have
       pending leads has one, so even with (1) fixed nothing would have
       enrolled. research_report only grounds the generic
       apollo/linkedin_*/lead_finder copy path -- job_posting_signal and
       cloud_decoded_job_signal ignore it entirely (see
       run_o2_cold_dm_writer's per-source branch). Missing report is now
       a warning + empty dict rather than a silent skip; every draft
       still lands at status='pending_hitl' for human review before
       anything can send.

    Run record + skip table (2026-10-19). Every run now writes ONE audit_log
    row (action='linkedin_dm_sequences_run', win or lose) and one
    mse_dm_enrollment_skips row per lead it passed over, with the reason.
    Before this the reasons reached only the logs, and the Oct 19 run that
    stalled on a dead shared connection left no trace at all -- which is
    what the 08:45 watchdog (core/dm_watchdog.py) now looks for.
    """
    run_linkedin_dm_sequences()


def run_linkedin_dm_sequences(db=None) -> dict:
    import logging
    from datetime import datetime, timezone

    from agents.marketing.mkt_o2_cold_dm_writer import run_o2_for_linkedin_leads

    log = logging.getLogger(__name__)
    # Its own reconnecting client: on the shared one this run stalled
    # silently on 2026-10-19 after its first query.
    db = db or get_supabase_for_job()
    run_at = datetime.now(timezone.utc).isoformat()
    per_product: dict[str, dict] = {}
    errors: dict[str, str] = {}
    try:
        pending_linkedin = db.table("mse_linkedin_leads").select("product_id").eq("status", "pending_dm").execute().data or []
        pending_lead_finder = (
            db.table("mse_leads")
            .select("product_id")
            .eq("status", "pending_dm")
            .execute()
            .data
            or []
        )
        product_ids = {row["product_id"] for row in pending_linkedin + pending_lead_finder if row.get("product_id")}
        log.warning("[LinkedInDMSequences] discovered %d product(s) with pending_dm leads", len(product_ids))

        for product_id in sorted(product_ids):
            try:
                reports = (
                    db.table("mse_research_reports")
                    .select("report_json")
                    .eq("product_id", product_id)
                    .order("cycle_date", desc=True)
                    .limit(1)
                    .execute()
                    .data
                    or []
                )
                if not reports:
                    log.warning(
                        "[LinkedInDMSequences] product %s has no MKT-R1 research report -- enrolling anyway; "
                        "job-signal sources don't use it, and any grounded source still gates on its own filters",
                        product_id,
                    )
                research_report = reports[0]["report_json"] if reports else {}
                result = run_o2_for_linkedin_leads(product_id=product_id, research_report=research_report,
                                                   supabase_client=db)
                per_product[product_id] = {k: result.get(k) for k in
                                           ("sequences_written", "pending_dm_total", "skip_reasons")}
                _record_skips(db, product_id, run_at, result.get("skipped") or [])
            except Exception as exc:  # one product's failure must not hide the others'
                errors[product_id] = f"{type(exc).__name__}: {exc}"[:500]
                log.error("[LinkedInDMSequences] product %s failed: %s", product_id, exc)
    except Exception as exc:
        errors["_run"] = f"{type(exc).__name__}: {exc}"[:500]
        log.error("[LinkedInDMSequences] run failed: %s", exc)

    summary = {"run_at": run_at, "products": per_product, "errors": errors,
               "sequences_written": sum((p.get("sequences_written") or 0) for p in per_product.values())}
    try:
        db.table("audit_log").insert({
            "agent_id": "mkt-o2-cold-dm-writer", "action": "linkedin_dm_sequences_run",
            "outcome": "lose" if errors else "win", "metadata": summary,
        }).execute()
    except Exception as exc:
        log.error("[LinkedInDMSequences] audit_log write failed: %s", exc)
    log.warning("[LinkedInDMSequences] run %s: written=%s errors=%s",
                "lose" if errors else "win", summary["sequences_written"], list(errors))
    return summary


def _record_skips(db, product_id: str, run_at: str, skipped: list[dict]) -> None:
    rows = [{"product_id": product_id, "lead_id": s.get("lead_id"), "run_at": run_at,
             "reason": s.get("code") or "other",
             "detail": " -- ".join(x for x in (s.get("company"), s.get("reason")) if x)[:1000] or None}
            for s in skipped]
    if rows:
        db.table("mse_dm_enrollment_skips").insert(rows).execute()


@router.post("/leads/{lead_id}/mark-sent")
async def mark_linkedin_lead_sent(lead_id: str, authorization: Optional[str] = Header(default=None)):
    """Kelvin calls this after he pastes and sends a DM manually during his
    daily LinkedIn session — nothing here sends anything, it only records
    that a human already did."""
    require_marketing_api_key(authorization)
    db = get_supabase()

    result = (
        db.table("mse_linkedin_leads")
        .update({"status": "contacted", "contacted_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", lead_id)
        .eq("status", "pending_dm")
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found or already marked contacted")

    return {"status": "contacted", "id": lead_id}
