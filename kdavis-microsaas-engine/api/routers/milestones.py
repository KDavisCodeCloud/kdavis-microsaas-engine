from fastapi import APIRouter, Request
from core.supabase_client import get_supabase

router = APIRouter(prefix="/milestones", tags=["milestones"])


@router.get("/{tenant_id}")
async def get_milestones(tenant_id: str, request: Request):
    db = get_supabase()
    rows = (
        db.table("milestones")
        .select("milestone_key,threshold,achieved_at,notified_at")
        .eq("tenant_id", tenant_id)
        .order("threshold")
        .execute()
        .data
    )
    return {"tenant_id": tenant_id, "milestones": rows}
