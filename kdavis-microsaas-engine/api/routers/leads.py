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

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

from api.middleware.auth import require_marketing_api_key
from core.supabase_client import get_supabase

router = APIRouter(prefix="/marketing", tags=["marketing", "leads"])


class FindLeadsRequest(BaseModel):
    product_id: str
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
    docstring), far past any reasonable HTTP request timeout.
    """
    require_marketing_api_key(authorization)
    db = get_supabase()

    run_row = db.table("mse_lead_finder_runs").insert({
        "product_id": body.product_id, "status": "pending", "sources_used": [],
    }).execute()
    if not run_row.data:
        raise HTTPException(status_code=500, detail="Failed to create lead finder run")
    run_id = run_row.data[0]["id"]

    background_tasks.add_task(_run_lead_finder_background, body.product_id, run_id)
    return {"run_id": run_id, "status": "pending"}


def _run_lead_finder_background(product_id: str, run_id: str) -> None:
    from agents.marketing.mkt_lead_finder import run_lead_finder_for_product

    try:
        run_lead_finder_for_product(product_id=product_id, run_id=run_id)
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
