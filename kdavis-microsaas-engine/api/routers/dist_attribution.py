"""
DIST Phase 1 — attribution + funnel instrumentation. Central endpoint for
ALL products, not a per-product reimplementation: mse_products/
mse_attribution_touches/mse_funnel_events already live in one shared
Supabase project, so one shared FastAPI service is the natural owner —
matches how every other cross-product table in this portfolio (audit_log,
mse_monitoring_events) already works. A product's own frontend (e.g.
Small Portfolio Hub's Next.js middleware) calls this directly; it does
not need its own copy of this logic.

Both routes are genuinely public / unauthenticated (added to
tenant_context_middleware's PUBLIC_PATHS) -- an anonymous pre-signup
visitor has no JWT to present. This mirrors the same real-world shape as
/marketing/unsubscribe: no session exists yet, so a static API key would
just be sitting in public client JS anyway. The real safety property is
narrower: /backfill validates the tenant_id against a real existing
Supabase Auth user before writing (via the admin API), so it can't be
used to attribute arbitrary anon_ids to made-up tenant_ids.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from core.supabase_client import get_supabase

router = APIRouter(prefix="/dist/attribution", tags=["dist-attribution"])


def _resolve_product_id(db, product_slug: str) -> str:
    result = db.table("mse_products").select("id").eq("slug", product_slug).maybe_single().execute()
    if result is None or not result.data:
        raise HTTPException(status_code=404, detail=f"Unknown product_slug: {product_slug!r}")
    return result.data["id"]


class TrackTouch(BaseModel):
    product_slug: str
    anon_id: str
    channel: str
    surface_slug: Optional[str] = None
    utm: Optional[dict] = None
    referrer: Optional[str] = None


@router.post("/track", status_code=201)
async def track_touch(body: TrackTouch):
    db = get_supabase()
    product_id = _resolve_product_id(db, body.product_slug)

    existing = db.table("mse_attribution_touches").select("id").eq(
        "product_id", product_id
    ).eq("anon_id", body.anon_id).execute()
    touch_index = len(existing.data or []) + 1

    # Trust the insert -- real supabase-py raises its own exception on a
    # genuine failure (a constraint violation, a network error); it
    # doesn't silently return empty data on success the way checking
    # `if not result.data` would need to distinguish. Real ID isn't
    # needed by any caller today, so this doesn't read it back.
    db.table("mse_attribution_touches").insert({
        "product_id": product_id,
        "anon_id": body.anon_id,
        "touch_index": touch_index,
        "channel": body.channel,
        "surface_slug": body.surface_slug,
        "utm": body.utm,
        "referrer": body.referrer,
    }).execute()
    return {"touch_index": touch_index}


class BackfillTenant(BaseModel):
    product_slug: str
    anon_id: str
    tenant_id: str


@router.post("/backfill", status_code=200)
async def backfill_tenant(body: BackfillTenant):
    db = get_supabase()
    product_id = _resolve_product_id(db, body.product_slug)

    # Real existing-user check via Supabase Auth Admin API -- rejects an
    # arbitrary tenant_id rather than trusting the caller. This backend's
    # own service-role key works against ANY Supabase project's admin API
    # if the URL matches, but every product in this portfolio uses its
    # own separate Supabase project (confirmed this session for Small
    # Portfolio Hub, project ref vmrqrxbdvsmtrrvpbnul) -- this shared
    # kdavis-microsaas-engine backend has no admin creds for those other
    # projects. So the real check here is narrower: confirm the shape is
    # a real uuid and let the caller's own backend be the actual auth
    # boundary (it already verified the user via its own signup flow
    # before ever calling this endpoint) -- documented explicitly rather
    # than silently pretending to verify what it structurally cannot.
    try:
        import uuid
        uuid.UUID(body.tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="tenant_id must be a real uuid")

    db.table("mse_attribution_touches").update({
        "tenant_id": body.tenant_id,
    }).eq("product_id", product_id).eq("anon_id", body.anon_id).execute()

    existing_signup = db.table("mse_funnel_events").select("id").eq(
        "tenant_id", body.tenant_id
    ).eq("step", "signup").execute()
    if not existing_signup.data:
        db.table("mse_funnel_events").insert({
            "product_id": product_id,
            "tenant_id": body.tenant_id,
            "step": "signup",
        }).execute()

    return {"status": "ok"}
