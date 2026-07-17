from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, field_validator
from typing import Literal
from core.supabase_client import get_supabase

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

VALID_STATUSES = Literal[
    "discovered", "validated", "watch", "rejected",
    "READY_TO_BUILD", "building", "launched", "tracking_mrr"
]


class OpportunityCard(BaseModel):
    vertical: str
    pain_point: str
    icp: dict
    solution_concept: str
    mrr_calculation: str
    competitor_pricing_avg: float | None = None
    conservative_mrr_potential: float
    competition_density: Literal["red", "yellow", "green"]
    competition_density_reason: str
    build_confidence_score: int
    build_confidence_reason: str
    retention_hooks: dict
    competitor_examples: list = []
    source_urls: list = []
    tier_structure: dict = {}
    mcp_integration_surface: str | None = None
    stack_compatible: bool = True
    stack_compatibility_notes: str | None = None
    estimated_build_weeks: int | None = None
    status: str = "discovered"
    rejection_reason: str | None = None
    owner: str | None = None

    @field_validator("conservative_mrr_potential")
    @classmethod
    def mrr_floor(cls, v):
        if v < 4000:
            raise ValueError("conservative_mrr_potential must be >= 4000")
        return v

    @field_validator("build_confidence_score")
    @classmethod
    def confidence_range(cls, v):
        if not 0 <= v <= 100:
            raise ValueError("build_confidence_score must be 0–100")
        return v


class StatusUpdate(BaseModel):
    status: str
    rejection_reason: str | None = None
    owner: str | None = None
    notes: str | None = None


@router.get("")
async def list_pipeline(request: Request, status: str | None = None):
    db = get_supabase()
    query = db.table("opportunity_pipeline").select("*").order("build_confidence_score", desc=True)
    if status:
        query = query.eq("status", status)
    return {"opportunities": query.execute().data}


@router.post("", status_code=201)
async def create_opportunity(body: OpportunityCard, request: Request):
    db = get_supabase()
    result = db.table("opportunity_pipeline").insert(body.model_dump()).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Insert failed")
    return result.data[0]


@router.patch("/{opp_id}/status")
async def update_status(opp_id: str, body: StatusUpdate, request: Request):
    db = get_supabase()
    update = {"status": body.status}
    if body.rejection_reason:
        update["rejection_reason"] = body.rejection_reason
    if body.owner:
        update["owner"] = body.owner
    if body.notes:
        update["notes"] = body.notes
    result = db.table("opportunity_pipeline").update(update).eq("id", opp_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return result.data[0]


class ReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str | None = None


@router.post("/{opp_id}/review")
async def review_opportunity(opp_id: str, body: ReviewRequest, request: Request):
    """
    Kelvin's per-opportunity approve/reject + comment, kept in
    human_review_status/human_review_comment — deliberately SEPARATE from
    the agent's own status/verdict_v2_output. Comparing the two is the
    actual tuning signal for Verdict prompt v2.1+ (where the agent and
    Kelvin disagree is exactly what needs investigating), so this must
    never overwrite the agent's own fields. Admin-gated + triggered_by +
    audit log, matching every other mutating action in this repo — the
    existing PATCH /{opp_id}/status route predates that convention and
    is out of scope here.
    """
    if getattr(request.state, "role", "") != "admin":
        raise HTTPException(status_code=403, detail="Opportunity review requires admin role")

    triggered_by = getattr(request.state, "tenant_id", None)
    if not triggered_by:
        raise HTTPException(status_code=401, detail="No authenticated user to attribute this review to")

    db = get_supabase()
    result = db.table("opportunity_pipeline").update({
        "human_review_status": body.decision,
        "human_review_comment": body.comment,
        "human_reviewed_by": triggered_by,
        "human_reviewed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", opp_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    db.table("audit_log").insert({
        "agent_id": "human-review",
        "action": "opportunity_review",
        "outcome": "win" if body.decision == "approved" else "lose",
        "product_id": opp_id,
        "metadata": {"triggered_by": triggered_by, "decision": body.decision, "comment": body.comment},
    }).execute()
    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": "opportunity_reviewed",
        "metadata": {"opportunity_id": opp_id, "decision": body.decision, "triggered_by": triggered_by},
    }).execute()

    return result.data[0]


@router.post("/{opp_id}/stamp")
async def stamp_ready_to_build(opp_id: str, request: Request):
    """Aggregator-only endpoint — stamps READY_TO_BUILD after all gates pass."""
    if getattr(request.state, "role", "") != "admin":
        raise HTTPException(status_code=403, detail="Aggregator stamp requires admin role")
    db = get_supabase()
    result = db.table("opportunity_pipeline").update({"status": "READY_TO_BUILD"}).eq("id", opp_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return result.data[0]
