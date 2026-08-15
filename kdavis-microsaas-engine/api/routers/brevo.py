"""
Brevo list registration + trial enrollment API — POST /marketing/brevo/lists
(register a product's Brevo list ID, one-time per product after the manual
BREVO_SETUP.md steps), GET /marketing/brevo/lists/{product_id} (read it
back), POST /marketing/brevo/enroll (n8n/trial_enrollment_workflow.json's
trigger target — enrolls one real trial signup into that product's Brevo
list, see agents/marketing/mkt_o3_email_sequence_loader.py's
enroll_trial_in_sequence). Same internal-secret auth as api/routers/leads.py
and api/routers/linkedin_intake.py — MARKETING_API_KEY, no tenant JWT to
check (n8n/internal-triggered).
"""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from agents.marketing.mkt_o3_email_sequence_loader import enroll_trial_in_sequence
from api.middleware.auth import require_marketing_api_key
from core.supabase_client import get_supabase

router = APIRouter(prefix="/marketing/brevo", tags=["marketing", "brevo"])


class BrevoListRequest(BaseModel):
    product_id: str
    brevo_list_id: int
    list_name: Optional[str] = None


class EnrollRequest(BaseModel):
    product_id: str
    email: str
    first_name: str = ""
    last_name: str = ""
    plan_tier: str = ""
    trial_start: str = ""


@router.post("/lists")
async def upsert_brevo_list(body: BrevoListRequest, authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    db = get_supabase()

    result = db.table("mse_brevo_lists").upsert(
        {"product_id": body.product_id, "brevo_list_id": body.brevo_list_id, "list_name": body.list_name},
        on_conflict="product_id",
    ).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save Brevo list mapping")
    return result.data[0]


@router.get("/lists/{product_id}")
async def get_brevo_list(product_id: str, authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    db = get_supabase()

    result = db.table("mse_brevo_lists").select("*").eq("product_id", product_id).maybe_single().execute()
    if result is None or not result.data:
        raise HTTPException(status_code=404, detail="No Brevo list registered for this product")
    return result.data


@router.post("/enroll")
async def enroll_trial(body: EnrollRequest, authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)

    result = enroll_trial_in_sequence(
        product_id=body.product_id, email=body.email,
        first_name=body.first_name, last_name=body.last_name,
        plan_tier=body.plan_tier, trial_start=body.trial_start,
    )
    return {
        "status": result.status,
        "sequence_drafted": result.sequence_drafted,
        "enrolled": result.enrolled,
        "sequence_id": result.sequence_id,
        "brevo_list_id": result.brevo_list_id,
        "error": result.error,
    }
