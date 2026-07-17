from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel
from core.supabase_client import get_supabase

router = APIRouter(prefix="/research", tags=["research"])

VALID_VERTICALS = {
    "Healthcare / Medical Front Desk",
    "Legal / Professional Services",
    "E-commerce / Retail Ops",
    "Real Estate / Property Management",
    "HR / Ops / People Management",
    "Finance / Accounting / Bookkeeping",
}


class ResearchRunRequest(BaseModel):
    verticals: list[str] = []


@router.post("/run")
async def run_research(body: ResearchRunRequest, request: Request, background_tasks: BackgroundTasks):
    """
    Triggers the research orchestrator for the specified verticals.
    Full swarm runs if verticals list is empty.
    Returns session_id immediately — poll /research/session/{id} for results.
    """
    verticals = body.verticals if body.verticals else list(VALID_VERTICALS)

    invalid = [v for v in verticals if v not in VALID_VERTICALS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "invalid_vertical", "message": f"Unknown verticals: {invalid}"}
        )

    import uuid
    session_id = str(uuid.uuid4())

    background_tasks.add_task(_run_orchestrator, session_id, verticals)

    return {
        "session_id": session_id,
        "status": "queued",
        "verticals": verticals,
    }


@router.get("/session/{session_id}")
async def get_session(session_id: str, request: Request):
    """Poll for results of a research run by session_id. session_summary is
    only present once node_summarize's completion event has landed in
    usage_events — its absence means the swarm is still running, not that
    the run failed."""
    db = get_supabase()
    results = (
        db.table("opportunity_pipeline")
        .select("id,vertical,solution_concept,conservative_mrr_potential,build_confidence_score,status")
        .eq("notes", f"session:{session_id}")
        .order("build_confidence_score", desc=True)
        .execute()
        .data
    )

    completion = (
        db.table("usage_events")
        .select("metadata")
        .eq("event_type", "research_session_complete")
        .eq("metadata->>session_id", session_id)
        .maybe_single()
        .execute()
        .data
    )

    return {
        "session_id": session_id,
        "opportunities": results,
        "session_summary": completion["metadata"] if completion else None,
    }


async def _run_orchestrator(session_id: str, verticals: list[str]) -> None:
    from agents.orchestrator.agent import run as orchestrator_run
    await orchestrator_run(session_id, verticals)
