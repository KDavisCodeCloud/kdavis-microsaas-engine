from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from core.supabase_client import get_supabase
from core.retention.milestone_detector import check_milestones

router = APIRouter(prefix="/events", tags=["events"])


class UsageEvent(BaseModel):
    event_type: str
    metadata: dict = {}


@router.post("", status_code=201)
async def log_event(body: UsageEvent, request: Request):
    tenant_id = request.state.tenant_id
    db = get_supabase()

    result = db.table("usage_events").insert({
        "tenant_id": tenant_id,
        "event_type": body.event_type,
        "metadata": body.metadata,
    }).execute()

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to log event")

    newly_achieved = check_milestones(tenant_id, body.event_type)

    return {
        "id": result.data[0]["id"],
        "milestones_achieved": newly_achieved,
    }
