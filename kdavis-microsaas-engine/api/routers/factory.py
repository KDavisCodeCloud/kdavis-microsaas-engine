"""
Factory build pipeline trigger — the HITL gate for Phase 6e. Admin-only,
dashboard-facing (not n8n/automation-triggered like marketing.py's
routes) — a human must explicitly click "build this" and hand over the
Stripe key Kelvin created manually for this product (ADR-006 — see
agents/factory/provision_stripe.py's docstring for why that step can't be
automated). Runs in the background since the full pipeline takes several
minutes (Supabase project provisioning alone can take up to 5).
"""
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/factory", tags=["factory"])

SCAFFOLD_OUTPUT_ROOT = Path("/tmp/mse-products")


class BuildRequest(BaseModel):
    stripe_api_key: str
    org_id: str | None = None


@router.post("/build/{opportunity_id}")
async def trigger_build(
    opportunity_id: str, body: BuildRequest, request: Request, background_tasks: BackgroundTasks,
):
    if getattr(request.state, "role", "") != "admin":
        raise HTTPException(status_code=403, detail="Build pipeline requires admin role")

    triggered_by = getattr(request.state, "tenant_id", None)
    if not triggered_by:
        raise HTTPException(status_code=401, detail="No authenticated user to attribute this build to")

    background_tasks.add_task(_run_build, opportunity_id, body.stripe_api_key, triggered_by, body.org_id)
    return {"status": "queued", "opportunity_id": opportunity_id}


def _run_build(opportunity_id: str, stripe_api_key: str, triggered_by: str, org_id: str | None) -> None:
    from agents.factory.build_pipeline import run_build_pipeline
    run_build_pipeline(
        opportunity_id, SCAFFOLD_OUTPUT_ROOT, stripe_api_key, triggered_by, org_id=org_id,
    )


@router.post("/generate-brief/{opportunity_id}")
async def trigger_generate_brief(
    opportunity_id: str, request: Request, background_tasks: BackgroundTasks,
):
    if getattr(request.state, "role", "") != "admin":
        raise HTTPException(status_code=403, detail="Brief generation requires admin role")

    triggered_by = getattr(request.state, "tenant_id", None)
    if not triggered_by:
        raise HTTPException(status_code=401, detail="No authenticated user to attribute this brief to")

    background_tasks.add_task(_run_generate_brief, opportunity_id, triggered_by)
    return {"status": "queued", "opportunity_id": opportunity_id}


@router.post("/dispatch-brief/{brief_id}")
async def trigger_dispatch_brief(
    brief_id: str, request: Request, background_tasks: BackgroundTasks,
):
    if getattr(request.state, "role", "") != "admin":
        raise HTTPException(status_code=403, detail="Brief dispatch requires admin role")

    triggered_by = getattr(request.state, "tenant_id", None)
    if not triggered_by:
        raise HTTPException(status_code=401, detail="No authenticated user to attribute this dispatch to")

    background_tasks.add_task(_run_dispatch_brief, brief_id, triggered_by)
    return {"status": "queued", "brief_id": brief_id}


def _run_dispatch_brief(brief_id: str, triggered_by: str) -> None:
    from agents.factory.brief_dispatcher import dispatch_brief
    dispatch_brief(brief_id, triggered_by)


def _run_generate_brief(opportunity_id: str, triggered_by: str) -> None:
    from agents.factory.brief_generator import generate_build_brief, generate_research_report_from_verdict
    brief_row = generate_build_brief(opportunity_id, triggered_by)

    # Seeds the real marketing pipeline (mse_research_reports/mse_icp_configs/
    # mse_brevo_sequence_drafts + fires run_campaign_orchestrator) using the
    # brief's own id as product_id -- see generate_research_report_from_verdict's
    # docstring for why (no mse_products table exists anywhere in this schema).
    # Best-effort: a failure here must never take down brief generation itself,
    # which already succeeded and is the thing a human is waiting to review.
    if brief_row and brief_row.get("id"):
        try:
            generate_research_report_from_verdict(opportunity_id, brief_row["id"], triggered_by)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                "[factory] research report seed failed for opportunity %s (brief still generated): %s",
                opportunity_id, exc,
            )
