"""
Triggers for the 2026-10-18 lead sources (agents/marketing/lead_sources.py).

  POST /marketing/lead-sources/hn-scout        Hacker News hiring threads
  POST /marketing/lead-sources/partner-scout   referral-partner firms (Brave)
  POST /marketing/lead-sources/vc-scout        VC operating partners + portfolios
  POST /marketing/lead-sources/pain-backfill   pain phrases on existing leads

Each creates its mse_lead_finder_runs row synchronously and returns the
run_id (poll GET /marketing/leads/runs/{run_id}); the work runs in the
background. Same MARKETING_API_KEY auth as every other marketing router.

Only partner-scout spends Brave quota, so only it goes through the monthly
budget guard -- and its per-run cap is clamped at 40, the standing ceiling.
"""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field

from api.middleware.auth import require_marketing_api_key
from core.supabase_client import get_supabase

log = logging.getLogger(__name__)

router = APIRouter(prefix="/marketing/lead-sources", tags=["marketing", "leads"])

MAX_QUERIES_PER_RUN = 40


class HnRequest(BaseModel):
    months: int = Field(default=1, ge=1, le=3)


class PartnerRequest(BaseModel):
    max_queries: int = Field(default=30, ge=1, le=MAX_QUERIES_PER_RUN)
    max_firms: int = Field(default=25, ge=1, le=60)
    # Run only these segments' queries (e.g. ["compliance_firm"]); all if empty.
    segments: list[str] = Field(default_factory=list)


class VcRequest(BaseModel):
    max_probes: int = Field(default=60, ge=0, le=400)


def _new_run(db, product_id: str, source: str) -> str:
    row = db.table("mse_lead_finder_runs").insert(
        {"product_id": product_id, "status": "pending", "sources_used": [source]}).execute()
    if not row.data:
        raise HTTPException(status_code=500, detail=f"Failed to create the {source} run")
    return row.data[0]["id"]


def _background(fn, **kwargs) -> None:
    try:
        fn(**kwargs)
    except Exception:
        # The run row and audit_log already carry the failure.
        log.exception("[LeadSources] %s failed", getattr(fn, "__name__", fn))


@router.post("/hn-scout")
async def hn_scout(body: HnRequest, background_tasks: BackgroundTasks,
                   authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    from agents.marketing.lead_sources import run_hn_scout
    from agents.marketing.mkt_lead_finder import THDAGENTIC_CONSULTING_PRODUCT_ID
    run_id = _new_run(get_supabase(), THDAGENTIC_CONSULTING_PRODUCT_ID, "hn_thread")
    background_tasks.add_task(_background, run_hn_scout, months=body.months, run_id=run_id)
    return {"run_id": run_id, "status": "pending", "source": "hn_thread"}


@router.post("/partner-scout")
async def partner_scout(body: PartnerRequest, background_tasks: BackgroundTasks,
                        authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    db = get_supabase()
    from agents.marketing.brave_budget import check_budget
    budget = check_budget(db)
    if not budget.allowed:
        raise HTTPException(status_code=409, detail={"error": "brave_budget_exhausted", "budget": budget.as_dict()})
    from agents.marketing.lead_sources import run_partner_scout
    from agents.marketing.partner_sourcing import PARTNERS_PRODUCT_ID
    run_id = _new_run(db, PARTNERS_PRODUCT_ID, "partner_signal")
    background_tasks.add_task(_background, run_partner_scout, max_queries=body.max_queries,
                              max_firms=body.max_firms, segments=body.segments, run_id=run_id)
    return {"run_id": run_id, "status": "pending", "source": "partner_signal",
            "max_queries": body.max_queries, "brave_budget": budget.as_dict()}


@router.post("/vc-scout")
async def vc_scout(body: VcRequest, background_tasks: BackgroundTasks,
                   authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    from agents.marketing.lead_sources import run_vc_scout_entry
    from agents.marketing.partner_sourcing import PARTNERS_PRODUCT_ID
    run_id = _new_run(get_supabase(), PARTNERS_PRODUCT_ID, "vc_operating_partner")
    background_tasks.add_task(_background, run_vc_scout_entry, max_probes=body.max_probes, run_id=run_id)
    return {"run_id": run_id, "status": "pending", "source": "vc_operating_partner"}


@router.post("/pain-backfill")
async def pain_backfill(background_tasks: BackgroundTasks,
                        authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    from agents.marketing.lead_sources import run_pain_backfill
    background_tasks.add_task(_background, run_pain_backfill)
    return {"status": "started", "result_in": "audit_log action=pain_phrase_backfill"}
