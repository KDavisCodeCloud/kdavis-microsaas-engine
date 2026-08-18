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

from core.supabase_client import get_supabase

router = APIRouter(prefix="/products", tags=["product-marketing"])


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


def _run_research(product_id: str, niche_keywords: list[str]) -> None:
    from agents.marketing.mkt_r1_research_core import run_research_core
    run_research_core(product_id, niche_keywords, source_config={})


def _run_campaign(product_id: str, vertical: str) -> None:
    from agents.marketing.mkt_orch_campaign_orchestrator import run_campaign_orchestrator
    run_campaign_orchestrator(product_id, research_opp_id=product_id, vertical=vertical)
