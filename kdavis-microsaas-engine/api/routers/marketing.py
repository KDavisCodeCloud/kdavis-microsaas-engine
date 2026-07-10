"""
Marketing engine API routes.

Both endpoints are automation-triggered (n8n / internal cron), not tied to
an end-customer tenant JWT — there is no existing "API key" auth pattern
in this repo to copy (checked: research.py/ceo.py/mcp.py rely on
tenant_context_middleware's JWT check; stripe.py verifies via Stripe's own
webhook signature instead). Since a tenant JWT doesn't make sense for an
n8n webhook, these two paths are exempted from tenant_context_middleware
in api/middleware/tenant_context.py's PUBLIC_PATHS, and instead require a
shared secret via `Authorization: Bearer <MARKETING_API_KEY>` — the
minimal standard version of "same API key pattern as existing routers"
given no existing router actually had one.
"""

import hmac
import os

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/marketing", tags=["marketing"])


def _require_api_key(authorization: str | None) -> None:
    expected = os.environ.get("MARKETING_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="MARKETING_API_KEY not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    provided = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")


class ResearchRequest(BaseModel):
    product_id: str
    niche_keywords: list[str] = []
    source_config: dict = {}


class CampaignRequest(BaseModel):
    product_id: str
    research_opp_id: str
    vertical: str = ""


@router.post("/research")
async def trigger_research(
    body: ResearchRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """Triggers MKT-R1 for a product. Runs in the background; poll
    audit_log or mse_research_reports for completion."""
    _require_api_key(authorization)

    background_tasks.add_task(
        _run_research, body.product_id, body.niche_keywords, body.source_config,
    )
    return {"status": "queued", "product_id": body.product_id}


@router.post("/campaign")
async def trigger_campaign(
    body: CampaignRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """Triggers MKT-ORCH for an approved product. Called by the n8n
    webhook that fires off the Supabase approval DB trigger."""
    _require_api_key(authorization)

    background_tasks.add_task(
        _run_campaign, body.product_id, body.research_opp_id, body.vertical,
    )
    return {"status": "queued", "product_id": body.product_id, "research_opp_id": body.research_opp_id}


def _run_research(product_id: str, niche_keywords: list[str], source_config: dict) -> None:
    from agents.marketing.mkt_r1_research_core import run_research_core
    run_research_core(product_id, niche_keywords, source_config)


def _run_campaign(product_id: str, research_opp_id: str, vertical: str) -> None:
    from agents.marketing.mkt_orch_campaign_orchestrator import run_campaign_orchestrator
    run_campaign_orchestrator(product_id, research_opp_id, vertical)
