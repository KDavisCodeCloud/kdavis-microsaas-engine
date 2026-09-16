"""
THD Consulting lead scout API — POST /thd-consulting/scrape/find (kicks off
a run), GET /thd-consulting/scrape/runs/{run_id} (poll status),
GET /thd-consulting/leads (paginated list, sortable/filterable),
PATCH /thd-consulting/leads/{id} (status/notes update from the CEO Decoded
Kanban board), GET /thd-consulting/leads/export.csv (CSV export — always
available, independent of the dashboard). Same internal-secret auth as
api/routers/leads.py/marketing.py/linkedin_intake.py — MARKETING_API_KEY,
n8n/internal-triggered, no end-customer tenant JWT to check.

Mirrors api/routers/leads.py's shape deliberately (see
agents/marketing/thd_lead_scout.py's own module docstring for why this
is a parallel system rather than an extension of mse_leads).
"""

import csv
import io
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.middleware.auth import require_marketing_api_key
from core.supabase_client import get_supabase

router = APIRouter(prefix="/thd-consulting", tags=["thd-consulting"])

_VALID_STATUSES = {"new", "contacted", "responded", "qualified", "closed"}
_VALID_OUTCOMES = {"won", "lost"}


class FindLeadsRequest(BaseModel):
    industries: list[str] = []
    locations: list[str] = []
    min_signal_score: Optional[int] = None
    employee_range: str = "25-150"


class LeadUpdateRequest(BaseModel):
    status: Optional[str] = None
    outcome: Optional[str] = None
    notes: Optional[str] = None


@router.post("/scrape/find")
async def trigger_scrape(
    body: FindLeadsRequest,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(default=None),
):
    """Creates the thd_consulting_scrape_runs row synchronously so the
    caller gets a real run_id to poll immediately, then runs the actual
    find as a background task — a full run can take a while (SMTP
    verification throttled per core/email_finder.py), same shape as
    POST /marketing/leads/find."""
    require_marketing_api_key(authorization)
    if not body.locations:
        raise HTTPException(status_code=400, detail="locations is required — nothing to search without at least one")

    db = get_supabase()
    filters = body.model_dump()

    run_row = db.table("thd_consulting_scrape_runs").insert({
        "product_id": "thd_consulting", "filters": filters, "status": "pending", "sources_used": [],
    }).execute()
    if not run_row.data:
        raise HTTPException(status_code=500, detail="Failed to create scrape run")
    run_id = run_row.data[0]["id"]

    background_tasks.add_task(_run_scout_background, filters, run_id)
    return {"run_id": run_id, "status": "pending"}


def _run_scout_background(filters: dict, run_id: str) -> None:
    from agents.marketing.thd_lead_scout import run_lead_scout

    try:
        run_lead_scout(filters=filters, run_id=run_id)
    except Exception:
        pass  # run_lead_scout already writes the failure to the run row + audit log


@router.get("/scrape/status")
async def get_scrape_status(
    run_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """`run_id` explicit -> that run. Omitted -> most recent run, matching
    the dashboard's "Last run timestamp and lead count" requirement
    without the caller needing to have kept the id around."""
    require_marketing_api_key(authorization)
    db = get_supabase()

    query = db.table("thd_consulting_scrape_runs").select("*")
    if run_id:
        result = query.eq("id", run_id).maybe_single().execute()
        if result is None or not result.data:
            raise HTTPException(status_code=404, detail="Run not found")
        return result.data

    result = query.order("started_at", desc=True).limit(1).execute()
    if not result.data:
        return {"status": "never_run"}
    return result.data[0]


@router.get("/leads")
async def list_leads(
    industry: Optional[str] = None,
    status: Optional[str] = None,
    min_score: Optional[int] = None,
    location: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
):
    require_marketing_api_key(authorization)
    db = get_supabase()

    query = db.table("thd_consulting_leads").select("*")
    if industry:
        query = query.eq("industry", industry)
    if status:
        query = query.eq("status", status)
    if min_score is not None:
        query = query.gte("signal_score", min_score)
    if location:
        query = query.ilike("location", f"%{location}%")
    result = query.order("signal_score", desc=True).order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return {"leads": result.data or [], "limit": limit, "offset": offset}


@router.patch("/leads/{lead_id}")
async def update_lead(
    lead_id: str,
    body: LeadUpdateRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_marketing_api_key(authorization)
    if body.status is not None and body.status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_VALID_STATUSES)}")
    if body.outcome is not None and body.outcome not in _VALID_OUTCOMES:
        raise HTTPException(status_code=400, detail=f"outcome must be one of {sorted(_VALID_OUTCOMES)}")

    update = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update:
        raise HTTPException(status_code=400, detail="Provide at least one of status/outcome/notes")

    db = get_supabase()
    result = db.table("thd_consulting_leads").update(update).eq("id", lead_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    return result.data[0]


@router.get("/leads/export.csv")
async def export_leads_csv(
    status: Optional[str] = None,
    min_score: Optional[int] = None,
    authorization: Optional[str] = Header(default=None),
):
    """CSV export via the API — the CLI (scripts/run_thd_lead_scout.py)
    also writes CSV directly, so export works whether or not this backend
    is even running, per the original spec's "CSV export always
    available" requirement."""
    require_marketing_api_key(authorization)
    db = get_supabase()

    query = db.table("thd_consulting_leads").select("*")
    if status:
        query = query.eq("status", status)
    if min_score is not None:
        query = query.gte("signal_score", min_score)
    rows = query.order("signal_score", desc=True).execute().data or []

    buffer = io.StringIO()
    fieldnames = [
        "company_name", "industry", "employee_count_estimate", "location", "website",
        "contact_name", "contact_title", "contact_email", "contact_email_status",
        "contact_linkedin", "contact_phone", "signal_score", "signal_breakdown",
        "source", "status", "outcome", "notes", "created_at",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=thd_consulting_leads.csv"},
    )
