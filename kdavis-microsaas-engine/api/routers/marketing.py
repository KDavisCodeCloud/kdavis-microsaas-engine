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

import html

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from api.middleware.auth import require_marketing_api_key as _require_api_key
from core.email_compliance import suppress_email, verify_unsubscribe_token
from core.supabase_client import get_supabase

router = APIRouter(prefix="/marketing", tags=["marketing"])


_UNSUB_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Unsubscribed</title></head>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1.5rem;color:#111">
<h1 style="font-size:1.25rem">You're unsubscribed.</h1>
<p>{email} won't receive any further emails from us.</p>
</body></html>"""

_UNSUB_INVALID_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Invalid link</title></head>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1.5rem;color:#111">
<h1 style="font-size:1.25rem">This unsubscribe link isn't valid.</h1>
<p>If you're trying to stop receiving emails from us, reply to any email you received and we'll remove you manually.</p>
</body></html>"""


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(email: str, token: str):
    """One-click CAN-SPAM unsubscribe — public, no auth, since a real human
    clicks this from their own inbox with no session of any kind. Token is
    an HMAC of the email (core/email_compliance.py), not a stored
    per-send value, so this works regardless of which email/campaign the
    link came from."""
    if not verify_unsubscribe_token(email, token):
        return HTMLResponse(_UNSUB_INVALID_PAGE, status_code=400)

    db = get_supabase()
    suppress_email(db, email, reason="unsubscribed")
    return HTMLResponse(_UNSUB_PAGE.format(email=html.escape(email)))


@router.post("/unsubscribe")
async def unsubscribe_one_click(email: str, token: str):
    """RFC 8058 one-click unsubscribe target. A conforming mail client
    POSTs here directly (body `List-Unsubscribe=One-Click`, per the
    List-Unsubscribe-Post header core/email_compliance.py's
    build_list_unsubscribe_headers sets) instead of a human clicking
    through the GET landing page above -- same token verification, plain
    200 response since there's no human to show a page to."""
    if not verify_unsubscribe_token(email, token):
        raise HTTPException(status_code=400, detail="Invalid unsubscribe token")

    db = get_supabase()
    suppress_email(db, email, reason="unsubscribed")
    return {"status": "unsubscribed"}


class ResearchRequest(BaseModel):
    product_id: str
    niche_keywords: list[str] = []
    source_config: dict = {}


class CampaignRequest(BaseModel):
    product_id: str
    research_opp_id: str
    vertical: str = ""


class ApolloListRequest(BaseModel):
    product_id: str
    campaign_build_id: str
    research_report: dict


class DmSequenceRequest(BaseModel):
    product_id: str
    campaign_build_id: str
    research_report: dict
    leads: list[dict] | None = None  # omit to auto-fetch from mse_apollo_leads


class SeoContentRequest(BaseModel):
    product_id: str
    research_report: dict


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


@router.post("/apollo-list")
async def trigger_apollo_list(
    body: ApolloListRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """Triggers MKT-O1 for an approved product's campaign. Runs in the
    background; poll audit_log or campaign_builds.apollo_status for completion."""
    _require_api_key(authorization)

    background_tasks.add_task(
        _run_apollo_list, body.product_id, body.research_report, body.campaign_build_id,
    )
    return {"status": "queued", "product_id": body.product_id, "campaign_build_id": body.campaign_build_id}


@router.post("/dm-sequences")
async def trigger_dm_sequences(
    body: DmSequenceRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """Triggers MKT-O2 for an approved product's campaign. Runs in the
    background; poll audit_log or campaign_builds.dm_sequence_status for
    completion. leads is optional — omit it to auto-fetch from
    mse_apollo_leads by campaign_build_id (the normal case, since MKT-O1
    is what produces them)."""
    _require_api_key(authorization)

    background_tasks.add_task(
        _run_dm_sequences, body.product_id, body.research_report, body.campaign_build_id, body.leads,
    )
    return {"status": "queued", "product_id": body.product_id, "campaign_build_id": body.campaign_build_id}


@router.post("/seo-content")
async def trigger_seo_content(
    body: SeoContentRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """Triggers MKT-S1 for a product's research report. Runs in the
    background; poll audit_log for completion — this endpoint doesn't
    persist the produced content itself, it returns it to the caller
    of run_s1_seo_content_factory (no content-storage table exists yet;
    not part of this task's scope)."""
    _require_api_key(authorization)

    background_tasks.add_task(
        _run_seo_content, body.product_id, body.research_report,
    )
    return {"status": "queued", "product_id": body.product_id}


@router.post("/send-sequences")
async def trigger_sequence_send(
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """Triggers MKT-O5 to send touch_1 for newly-approved sequences and
    touch_2 for anything due. Meant to be called on a schedule (n8n cron,
    e.g. hourly) — not per-campaign like the other trigger endpoints."""
    _require_api_key(authorization)

    background_tasks.add_task(_run_send_sequences)
    return {"status": "queued"}


def _run_research(product_id: str, niche_keywords: list[str], source_config: dict) -> None:
    from agents.marketing.mkt_r1_research_core import run_research_core
    run_research_core(product_id, niche_keywords, source_config)


def _run_campaign(product_id: str, research_opp_id: str, vertical: str) -> None:
    from agents.marketing.mkt_orch_campaign_orchestrator import run_campaign_orchestrator
    run_campaign_orchestrator(product_id, research_opp_id, vertical)


def _run_apollo_list(product_id: str, research_report: dict, campaign_build_id: str) -> None:
    from agents.marketing.mkt_o1_apollo_list_builder import run_o1_apollo_list_builder
    run_o1_apollo_list_builder(product_id, research_report, campaign_build_id)


def _run_dm_sequences(
    product_id: str, research_report: dict, campaign_build_id: str, leads: list[dict] | None,
) -> None:
    from agents.marketing.mkt_o2_cold_dm_writer import run_o2_cold_dm_writer

    if leads is None:
        from core.supabase_client import get_supabase
        db = get_supabase()
        result = db.table("mse_apollo_leads").select("*").eq("campaign_build_id", campaign_build_id).execute()
        leads = result.data or []

    run_o2_cold_dm_writer(product_id, research_report, leads, campaign_build_id)


def _run_seo_content(product_id: str, research_report: dict) -> None:
    from agents.marketing.mkt_s1_seo_content_factory import run_s1_seo_content_factory
    run_s1_seo_content_factory(research_report, product_id)


def _run_send_sequences() -> None:
    from agents.marketing.mkt_o5_sequence_sender import run_sequence_sender
    run_sequence_sender()
