"""
Dashboard-facing marketing triggers for a built product — admin-only,
Supabase-session-JWT-authenticated (request.state.role/tenant_id set by
tenant_context_middleware, same as api/routers/factory.py's trigger_build),
NOT the MARKETING_API_KEY shared-secret pattern api/routers/marketing.py's
/marketing/research and /marketing/campaign use (that pattern is for
n8n/automation and must never be exposed to the browser).

Both routes below call the exact same agent entry points
api/routers/marketing.py's automation routes already call
(run_research_core, run_campaign_orchestrator) — no new agent logic, just
a dashboard-appropriate way to fire them for a specific already-built
product on demand ("fire at will"), since the "Supabase DB trigger -> n8n
webhook" auto-fire-on-approval flow mkt_orch_campaign_orchestrator.py's
own docstring describes was never actually implemented anywhere.
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from core.supabase_client import get_supabase

router = APIRouter(prefix="/products", tags=["product-marketing"])


class LinkedInLeadsRequest(BaseModel):
    csv_text: str
    source: str = "linkedin_manual"


def _require_admin(request: Request) -> str:
    if getattr(request.state, "role", "") != "admin":
        raise HTTPException(status_code=403, detail="Product marketing triggers require admin role")
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="No authenticated user to attribute this trigger to")
    return tenant_id


def _load_opportunity(product_id: str) -> dict:
    db = get_supabase()
    result = db.table("opportunity_pipeline").select("vertical, solution_concept, pain_point").eq("id", product_id).maybe_single().execute()
    if result is None or not result.data:
        raise HTTPException(status_code=404, detail=f"No product found for id {product_id}")
    return result.data


@router.post("/{product_id}/run-research")
async def trigger_product_research(product_id: str, request: Request, background_tasks: BackgroundTasks):
    """Fires MKT-R1 for this product on demand. niche_keywords is derived
    server-side from the opportunity_pipeline row — nothing for the
    dashboard to fill in."""
    _require_admin(request)
    opp = _load_opportunity(product_id)

    niche_keywords = [kw for kw in (opp.get("vertical"), opp.get("solution_concept")) if kw]
    background_tasks.add_task(_run_research, product_id, niche_keywords)
    return {"status": "queued", "product_id": product_id, "niche_keywords": niche_keywords}


@router.post("/{product_id}/run-campaign")
async def trigger_product_campaign(product_id: str, request: Request, background_tasks: BackgroundTasks):
    """Fires MKT-ORCH for this product on demand. 409s before queuing if
    no research report exists yet — run-research must be fired at least
    once first, same requirement run_campaign_orchestrator's own
    _load_research_report already enforces, just surfaced synchronously
    instead of failing inside the background task."""
    _require_admin(request)
    opp = _load_opportunity(product_id)

    db = get_supabase()
    report = db.table("mse_research_reports").select("id").eq("product_id", product_id).limit(1).maybe_single().execute()
    if report is None or not report.data:
        raise HTTPException(status_code=409, detail="No research report for this product yet — run research first")

    background_tasks.add_task(_run_campaign, product_id, opp.get("vertical"))
    return {"status": "queued", "product_id": product_id}


@router.post("/{product_id}/linkedin-leads")
async def add_linkedin_leads(product_id: str, body: LinkedInLeadsRequest, request: Request, background_tasks: BackgroundTasks):
    """
    Dashboard-facing counterpart to agents/marketing/mkt_li_intake.py —
    the active first-customer channel (Apollo suspended, lead-finder has
    no working data source in this environment yet, see 2026-08-18 audit
    log). Kelvin pastes a CSV export from a LinkedIn search (or an
    engager list) directly in the panel; parses it here rather than
    requiring a curl/Postman call to api/routers/linkedin_intake.py's
    MARKETING_API_KEY-gated endpoint. The intake itself is synchronous
    (plain DB writes, no LLM/network calls) so the dashboard gets a real
    added/duplicates_skipped count immediately; if a research report
    already exists for this product, DM-sequence drafting for the
    newly-added leads is queued in the background right after (same
    run_o2_for_linkedin_leads entry point api/routers/linkedin_intake.py's
    /marketing/linkedin/dm-sequences uses) so there's real copy to review,
    not just names, without a second manual step.
    """
    _require_admin(request)
    _load_opportunity(product_id)  # 404s if the product doesn't exist

    from agents.marketing.mkt_li_intake import VALID_SOURCES, parse_csv_leads, run_li_intake

    if body.source not in VALID_SOURCES:
        raise HTTPException(status_code=400, detail=f"source must be one of {sorted(VALID_SOURCES)}")

    leads = parse_csv_leads(body.csv_text)
    if not leads:
        raise HTTPException(status_code=400, detail="No rows parsed from the pasted CSV — check the header row")

    result = run_li_intake(leads=leads, source=body.source, product_id=product_id)

    dm_sequences_queued = False
    if result.get("added", 0) > 0:
        db = get_supabase()
        report = db.table("mse_research_reports").select("report_json").eq("product_id", product_id).order("cycle_date", desc=True).limit(1).maybe_single().execute()
        if report is not None and report.data:
            background_tasks.add_task(_run_dm_sequences_for_leads, product_id, report.data["report_json"])
            dm_sequences_queued = True

    return {**result, "dm_sequences_queued": dm_sequences_queued}


@router.post("/{product_id}/mark-launched")
async def mark_product_launched(product_id: str, request: Request):
    """
    Manual override for the automated build_pipeline -> 'launched'
    transition (agents/factory/build_pipeline.py's run_build_pipeline
    updates opportunity_pipeline.status through 'building' -> 'launched'
    on its own, but only for products built through that pipeline).
    Covers two real cases: a product built or fixed outside the pipeline
    entirely (Showing Signal, built before opportunity_pipeline existed —
    see its 2026-08-18 backfill), or an automated build that actually
    finished but whose status update failed/never ran. Kelvin presses
    this once he's confirmed the product is genuinely live; no further
    gating beyond admin auth — an explicit manual action, not something
    to second-guess with extra status-transition rules.
    """
    _require_admin(request)
    _load_opportunity(product_id)  # 404s if the product doesn't exist

    db = get_supabase()
    result = db.table("opportunity_pipeline").update({"status": "launched"}).eq("id", product_id).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to update status to launched")

    return {"status": "launched", "product_id": product_id}


def _run_research(product_id: str, niche_keywords: list[str]) -> None:
    from agents.marketing.mkt_r1_research_core import run_research_core
    run_research_core(product_id, niche_keywords, source_config={})


def _run_campaign(product_id: str, vertical: str) -> None:
    from agents.marketing.mkt_orch_campaign_orchestrator import run_campaign_orchestrator
    run_campaign_orchestrator(product_id, research_opp_id=product_id, vertical=vertical)


def _run_dm_sequences_for_leads(product_id: str, research_report: dict) -> None:
    from agents.marketing.mkt_o2_cold_dm_writer import run_o2_for_linkedin_leads
    run_o2_for_linkedin_leads(product_id=product_id, research_report=research_report)
