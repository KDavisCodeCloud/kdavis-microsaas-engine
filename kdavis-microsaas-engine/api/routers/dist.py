"""
DIST subsystem routes -- pull-side distribution. This file grows across
tonight's build; Phase 2 (indexation) was the first route in it, Phase 3
(competitor monitor) is the second, added concurrently in a separate
fork sharing this same working tree -- merged additively rather than
overwritten.

Every route here mirrors api/routers/marketing.py's own MARKETING_API_KEY
shared-secret shape: n8n-triggered, no tenant JWT exists to check. Each
new path added here must also go into
api/middleware/tenant_context.py's PUBLIC_PATHS, same requirement that
file's own require_marketing_api_key docstring states for every router
using it.
"""
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from api.middleware.auth import require_marketing_api_key as _require_api_key
from core.supabase_client import get_supabase
from agents.dist.indexation_monitor import check_stale_and_alert, sync_indexation

router = APIRouter(prefix="/dist", tags=["dist"])


@router.post("/indexation/sync")
async def sync_all_products(authorization: str | None = Header(default=None)):
    """Weekly job entry point (n8n: dist_indexation_sync.json). Syncs
    every product in mse_products against its GSC property (currently
    always skipped with skipped_reason='no_verified_property' or a
    GSCAccessError string -- no product has a verified_property_url
    column populated yet, and no GSC credentials exist to populate one
    against, both real and disclosed, not hidden), then runs the 21-day
    stale check + circuit breaker for each regardless -- the alert/pause
    logic is real and independent of whether a sync actually pulled fresh
    data this run."""
    _require_api_key(authorization)

    db = get_supabase()
    products = db.table("mse_products").select("*").execute().data or []

    results = []
    for product in products:
        sync_result = sync_indexation(
            product["id"],
            product.get("verified_property_url"),
            supabase_client=db,
        )
        alert_result = check_stale_and_alert(
            product["id"], product["slug"], product["name"], supabase_client=db
        )
        results.append({"product_slug": product["slug"], **sync_result, **alert_result})

    return {"products_processed": len(results), "results": results}


@router.post("/competitor-monitor/run")
async def trigger_competitor_monitor(background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    """Monthly job entry point (n8n: dist_competitor_monitor.json). Runs
    DIST-C1 in the background -- the fetch loop makes real outbound HTTP
    requests with a rate-limited delay between each, so this must not
    block the request/response cycle."""
    _require_api_key(authorization)
    background_tasks.add_task(_run_competitor_monitor)
    return {"status": "queued"}


def _run_competitor_monitor() -> None:
    from agents.dist.competitor_monitor import run_competitor_monitor
    run_competitor_monitor()
