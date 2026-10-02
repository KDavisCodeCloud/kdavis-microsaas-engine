"""
Self-hosted lead finder API — POST /marketing/leads/find (kicks off a
run), GET /marketing/leads/runs/{run_id} (poll status), GET /marketing/leads
(paginated list), POST /marketing/icp + GET /marketing/icp/{product_id}
(per-product ICP config, agents/marketing/mkt_lead_finder.py's real
input). Same internal-secret auth as api/routers/marketing.py and
api/routers/linkedin_intake.py — MARKETING_API_KEY, no tenant JWT to
check (n8n/internal-triggered, no end-customer tenant of the marketing
engine itself).
"""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

from api.middleware.auth import require_marketing_api_key
from core.supabase_client import get_supabase

log = logging.getLogger(__name__)

router = APIRouter(prefix="/marketing", tags=["marketing", "leads"])


class FindLeadsRequest(BaseModel):
    product_id: str
    # Overrides the ICP config's target_count for this run only -- omit to
    # use the full configured target_count (the weekly cron's behavior).
    # Real gap fixed 2026-09-22: this field existed but was silently
    # dropped before ever reaching run_lead_finder_for_product, so a
    # caller had no way to ask for a quick handful instead of a full,
    # multi-hour run (target_count leads at 3-6 min/lead SMTP verification).
    limit: Optional[int] = None


class IcpConfigRequest(BaseModel):
    product_id: str
    job_titles: list[str] = []
    locations: list[str] = []
    industries: list[str] = []
    vertical: Optional[str] = None
    search_templates: list[str] = []
    exclude_domains: list[str] = []
    target_count: int = 100


@router.post("/leads/find")
async def trigger_lead_finder(
    body: FindLeadsRequest,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(default=None),
):
    """
    Creates the mse_lead_finder_runs row synchronously — so the caller
    gets a real run_id to poll immediately — and runs the actual find as
    a background task. A full run can take hours (SMTP verification is
    throttled to 10-20/hour, see mkt_lead_finder.py's own module
    docstring), far past any reasonable HTTP request timeout. Pass
    `limit` in the request body for a smaller, faster run than the ICP
    config's full target_count -- e.g. for a manual smoke test.
    """
    require_marketing_api_key(authorization)
    db = get_supabase()

    # Brave budget guard (decision 1e, 2026-10-02). Checked BEFORE the run row
    # is created, so a refused run leaves no misleading 'pending' row behind.
    # 409 not 429: nothing is rate-limiting the caller, the monthly budget is
    # exhausted to its reserve threshold and retrying now cannot succeed.
    from agents.marketing.brave_budget import check_budget
    budget = check_budget(db)
    if not budget.allowed:
        log.warning("[LeadFinder] refusing run for %s: %s", body.product_id, budget.reason)
        raise HTTPException(
            status_code=409,
            detail={"error": "brave_budget_exhausted", "budget": budget.as_dict()},
        )

    run_row = db.table("mse_lead_finder_runs").insert({
        "product_id": body.product_id, "status": "pending", "sources_used": [],
    }).execute()
    if not run_row.data:
        raise HTTPException(status_code=500, detail="Failed to create lead finder run")
    run_id = run_row.data[0]["id"]

    background_tasks.add_task(_run_lead_finder_background, body.product_id, run_id, body.limit)
    return {"run_id": run_id, "status": "pending", "brave_budget": budget.as_dict()}


def _run_lead_finder_background(product_id: str, run_id: str, limit: Optional[int] = None) -> None:
    from agents.marketing.mkt_lead_finder import run_lead_finder_for_product

    try:
        run_lead_finder_for_product(product_id=product_id, run_id=run_id, limit=limit)
    except Exception:
        pass  # run_lead_finder_for_product already writes the failure to the run row + audit log


@router.get("/leads/runs/{run_id}")
async def get_lead_finder_run(run_id: str, authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    db = get_supabase()

    result = db.table("mse_lead_finder_runs").select("*").eq("id", run_id).maybe_single().execute()
    if result is None or not result.data:
        raise HTTPException(status_code=404, detail="Run not found")
    return result.data


@router.get("/leads")
async def list_leads(
    product_id: Optional[str] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    email_status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
):
    require_marketing_api_key(authorization)
    db = get_supabase()

    query = db.table("mse_leads").select("*")
    if product_id:
        query = query.eq("product_id", product_id)
    if status:
        query = query.eq("status", status)
    if source:
        query = query.eq("source", source)
    if email_status:
        query = query.eq("email_status", email_status)
    result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return {"leads": result.data or [], "limit": limit, "offset": offset}


@router.get("/leads/icp-products")
async def list_icp_configured_products(authorization: Optional[str] = Header(default=None)):
    """Which products the lead finder can actually run against -- the same
    set n8n/lead_finder_workflow.json's own "Get ICP-Configured Products"
    node reads (a plain select on mse_icp_configs), joined to mse_products
    in Python since there's no FK relationship between the two tables for
    PostgREST to embed. Feeds the CEO Decoded dashboard's manual "run now"
    product picker -- the n8n-outage fallback needs to know which product
    ids are valid without Kelvin having to go look one up by hand."""
    require_marketing_api_key(authorization)
    db = get_supabase()

    icp_rows = db.table("mse_icp_configs").select("product_id,vertical,target_count").execute().data or []
    if not any(r.get("product_id") for r in icp_rows):
        return {"products": []}

    # mse_products has no FK relationship to mse_icp_configs for PostgREST
    # to embed (checked live) -- fetching the small full table and joining
    # in Python instead of a second filtered query, same "small enough
    # today, revisit if it grows" call as get_pipeline_summary above.
    products = db.table("mse_products").select("id,name,slug").execute().data or []
    names_by_id = {p["id"]: p for p in products}

    return {
        "products": [
            {
                "product_id": r["product_id"],
                "name": names_by_id.get(r["product_id"], {}).get("name", "Unknown product"),
                "slug": names_by_id.get(r["product_id"], {}).get("slug"),
                "vertical": r.get("vertical"),
                "target_count": r.get("target_count"),
            }
            for r in icp_rows
            if r.get("product_id")
        ]
    }


_PIPELINE_STAGES = ["new", "contacted", "replied", "qualified", "demo", "won", "lost"]


@router.get("/leads/outbound-products")
async def list_outbound_enabled_products(authorization: Optional[str] = Header(default=None)):
    """Products the weekly Lead Finder sweep is authorised to run for
    (decision 1b, 2026-10-02).

    This replaces n8n's old "read every row of mse_icp_configs" node, which
    swept all 9 configured products including shelved ones -- the 2026-10-02
    run burned 2h20m on a real-estate product nobody is selling. The gate is
    mse_icp_configs.outbound_enabled, so turning a product's outbound on or
    off is a config change, not a workflow edit.
    """
    require_marketing_api_key(authorization)
    db = get_supabase()

    rows = (db.table("mse_icp_configs")
            .select("product_id,vertical,target_count,selling_stage,outbound_enabled")
            .eq("outbound_enabled", True).execute().data or [])
    products = {p["id"]: p for p in
                (db.table("mse_products").select("id,name,status").execute().data or [])}
    out = []
    for r in rows:
        pid = r.get("product_id")
        if not pid:
            continue
        out.append({
            "product_id": pid,
            "name": (products.get(pid) or {}).get("name"),
            "vertical": r.get("vertical"),
            "selling_stage": r.get("selling_stage"),
            "target_count": r.get("target_count"),
        })
    out.sort(key=lambda p: (p["name"] or p["product_id"]))
    return {"products": out, "count": len(out)}


@router.get("/leads/weekly-summary")
async def get_weekly_lead_summary(
    days: int = 7,
    authorization: Optional[str] = Header(default=None),
):
    """What the week actually produced, for the Sunday digest and the CEO
    Decoded Marketing page (decision 1d + 5b).

    Replaces the old pattern where n8n waited 15 minutes and polled a single
    run. A real run takes hours, so that wait could only ever report an
    unfinished run as though it were the result. The runs are asynchronous and
    write their own progress; this reads whatever has actually completed in
    the window and says plainly what is still running.
    """
    require_marketing_api_key(authorization)
    db = get_supabase()

    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()

    runs = (db.table("mse_lead_finder_runs").select("*")
            .gte("started_at", since).order("started_at", desc=True).execute().data or [])
    products = {p["id"]: p for p in
                (db.table("mse_products").select("id,name").execute().data or [])}

    by_status: dict[str, int] = {}
    run_rows = []
    for r in runs:
        st = r.get("status") or "unknown"
        by_status[st] = by_status.get(st, 0) + 1
        run_rows.append({
            "run_id": r.get("id"),
            "product_id": r.get("product_id"),
            "product": (products.get(r.get("product_id")) or {}).get("name"),
            "status": st,
            "started_at": r.get("started_at"),
            "completed_at": r.get("completed_at"),
            "leads_found": r.get("leads_found"),
            "leads_verified": r.get("leads_verified"),
            "current_step": r.get("current_step") if st == "running" else None,
            "error_message": r.get("error_message"),
        })

    from agents.marketing.brave_budget import check_budget
    budget = check_budget(db)

    return {
        "window_days": days,
        "since": since,
        "runs": run_rows,
        "runs_by_status": by_status,
        "still_running": by_status.get("running", 0),
        "funnel": _outreach_funnel(db, since),
        "brave_budget": budget.as_dict(),
    }


def _outreach_funnel(db, since: str) -> dict:
    """qualified -> approved -> sent -> replies -> calls booked.

    Each stage is counted from the row that PROVES it happened rather than
    from a status that merely implies it: 'sent' counts a real
    touch_1_sent_at timestamp, not a status string. Stages with no source of
    truth yet report None, not 0 -- a zero would read as "nothing happened"
    when the truth is "we do not measure this".
    """
    leads = (db.table("mse_leads")
             .select("id,status,contact_status,lead_route,created_at").execute().data or [])
    seqs = (db.table("mse_dm_sequences")
            .select("id,status,touch_1_sent_at,touch_2_sent_at,created_at")
            .execute().data or [])

    recent_leads = [l for l in leads if (l.get("created_at") or "") >= since]
    qualified = sum(1 for l in recent_leads
                    if l.get("status") in ("company_qualified", "pending_dm", "pending_email",
                                           "contacted", "converted"))
    approved = sum(1 for s in seqs
                   if (s.get("created_at") or "") >= since
                   and s.get("status") in ("approved_hitl", "approved_manual",
                                           "touch_1_sent", "touch_2_sent", "replied"))
    sent = sum(1 for s in seqs if (s.get("touch_1_sent_at") or "") >= since)

    return {
        "qualified": qualified,
        "approved": approved,
        "sent": sent,
        # REPLIES AND CALLS ARE NOT MEASURED, and are reported as null rather
        # than 0 so the dashboard cannot render "0 replies" as a result. There
        # is no reply-capture path at all: mse_dm_sequences has no replied_at
        # column (verified against information_schema), and Reply-To points at
        # a personal Gmail mailbox, so a prospect's reply never reaches this
        # system. Measuring it needs either Resend inbound parsing on a domain
        # mailbox or IMAP against the reply inbox -- neither is wired.
        "replies": None,
        "calls_booked": None,
        "notes": {
            "qualified": "mse_leads created in the window that reached a qualified status",
            "approved": "mse_dm_sequences created in the window that cleared HITL",
            "sent": "counted from a real touch_1_sent_at timestamp, not a status string",
            "replies": ("NOT MEASURED: no replied_at column and replies go to a personal "
                        "mailbox; needs Resend inbound or IMAP capture"),
            "calls_booked": "NOT MEASURED: no calendar/booking source is wired yet",
        },
    }


@router.get("/leads/pipeline-summary")
async def get_pipeline_summary(product_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)):
    """Real stage counts for the CEO Decoded dashboard's Sales Pipeline
    card (previously a static mock, per that page's own "not_built" status
    note) and decoded-empire-os's leads page header. Counts in Python, not
    a SQL group-by RPC -- mse_leads is small enough today (0 rows in prod
    as of 2026-09-07) that a second DB round trip / new function isn't
    worth it yet; revisit if this table grows into the tens of thousands."""
    require_marketing_api_key(authorization)
    db = get_supabase()

    query = db.table("mse_leads").select("stage")
    if product_id:
        query = query.eq("product_id", product_id)
    result = query.execute()
    rows = result.data or []

    counts = {stage: 0 for stage in _PIPELINE_STAGES}
    for row in rows:
        stage = row.get("stage") or "new"
        counts[stage] = counts.get(stage, 0) + 1

    return {"product_id": product_id, "total": len(rows), "stages": counts}


_MEETING_STAGES = ("demo", "won", "lost")  # reached demo stage or further


@router.get("/leads/outreach-summary")
async def get_outreach_summary(authorization: Optional[str] = Header(default=None)):
    """Real per-product outreach stats for the CEO Decoded dashboard's
    "Cold Outreach Tracker" card (previously a static mock with a single
    fake "MSE Intro Sequence" row -- there is no real "named sequence
    template" concept in this schema, mse_dm_sequences rows are generated
    per-lead by MKT-O2, so this groups by product instead of inventing a
    sequence name that doesn't exist).

    "sent" = mse_dm_sequences rows where touch_1_sent_at is set (a real
    send happened; excludes suppressed/pending/rejected rows, which never
    reach a send). "meetings" is an honest approximation, not a real
    booked-meeting count -- there is no calendar/booking integration
    anywhere in this codebase -- it's leads currently at mse_leads.stage
    'demo' or later (a human moved them there via the leads page after an
    actual demo call). Open rate has no real signal at all (no email-open
    tracking is wired up) and is deliberately omitted, same as the mock's
    own hardcoded "opens: '—'" before this endpoint existed -- never
    fabricated as a fake percentage."""
    require_marketing_api_key(authorization)
    db = get_supabase()

    sequences = db.table("mse_dm_sequences").select("product_id,touch_1_sent_at").execute().data or []
    sent_by_product: dict[str, int] = {}
    for seq in sequences:
        pid = seq.get("product_id")
        if pid and seq.get("touch_1_sent_at"):
            sent_by_product[pid] = sent_by_product.get(pid, 0) + 1

    leads = db.table("mse_leads").select("product_id,stage").execute().data or []
    meetings_by_product: dict[str, int] = {}
    for lead in leads:
        pid = lead.get("product_id")
        if pid and (lead.get("stage") or "new") in _MEETING_STAGES:
            meetings_by_product[pid] = meetings_by_product.get(pid, 0) + 1

    product_ids = set(sent_by_product) | set(meetings_by_product)
    if not product_ids:
        return {"products": []}

    products = db.table("mse_products").select("id,name").execute().data or []
    names_by_id = {p["id"]: p.get("name", "Unknown product") for p in products}

    return {
        "products": [
            {
                "product_id": pid,
                "name": names_by_id.get(pid, "Unknown product"),
                "sent": sent_by_product.get(pid, 0),
                "meetings": meetings_by_product.get(pid, 0),
            }
            for pid in sorted(product_ids)
        ]
    }


@router.post("/icp")
async def upsert_icp_config(body: IcpConfigRequest, authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    db = get_supabase()

    result = db.table("mse_icp_configs").upsert(body.model_dump(), on_conflict="product_id").execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save ICP config")
    return result.data[0]


@router.get("/icp/{product_id}")
async def get_icp_config(product_id: str, authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    db = get_supabase()

    result = db.table("mse_icp_configs").select("*").eq("product_id", product_id).maybe_single().execute()
    if result is None or not result.data:
        raise HTTPException(status_code=404, detail="No ICP config for this product")
    return result.data
