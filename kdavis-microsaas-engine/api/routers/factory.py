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
