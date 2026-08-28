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
from core.supabase_client import get_supabase

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
    from agents.marketing.mkt_o2_cold_dm_writer import run_o2_for_linkedin_leads

    db = get_supabase()
    pending_linkedin = db.table("mse_linkedin_leads").select("product_id").eq("status", "pending_dm").execute().data or []
    pending_lead_finder = (
        db.table("mse_leads")
        .select("product_id")
        .eq("status", "pending_dm")
        .eq("email_status", "verified")
        .execute()
        .data
        or []
    )
    product_ids = {row["product_id"] for row in pending_linkedin + pending_lead_finder if row.get("product_id")}

    for product_id in product_ids:
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
            continue
        run_o2_for_linkedin_leads(product_id=product_id, research_report=reports[0]["report_json"], supabase_client=db)


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
