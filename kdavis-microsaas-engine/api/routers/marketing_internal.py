"""
CEO Decoded dashboard-facing approval API for lifecycle email sequences.

Built 2026-09-21 for the cross-repo "Email Campaign HITL Queue" build --
kdavis-agentic-platform's ceo-dashboard/ proxies to this router to give
Kelvin one unified approval queue across both the Cloud Decoded and MSE
email systems. Admin-JWT-gated (request.state.role, same convention as
api/routers/product_marketing.py and factory.py), NOT the
MARKETING_API_KEY shared-secret pattern api/routers/marketing.py's
automation routes use -- this is a browser-facing dashboard route.

**Wraps the REAL, existing mse_email_sequences table (migration 007),
deliberately not a new parallel schema.** The build spec this session was
written against (see the 2026-09-21 GAPS entry) assumed a
per-product-registry-scoped `mse_email_templates`/`mse_email_sequences`/
subscriber/enrollment schema keyed on mse_products.id. That schema does
not exist here and was NOT built this session -- recon found
mse_email_sequences.product_id is a pre-mse_products-era UUID with no
resolvable FK to mse_products (0/2 existing rows match any real
mse_products.id), and the live campaign trigger
(agents/marketing/mkt_orch_campaign_orchestrator.py, fired from
api/routers/product_marketing.py's POST /products/{id}/run-campaign)
actually keys "product_id" off opportunity_pipeline.id, a third, unrelated
ID space. Building a clean new product-scoped schema on top of that
mismatch without resolving it first would have been building on sand.
This router instead exposes what's real today: one row per generated
sequence, its own id as the addressable key, `product_id` passed through
verbatim (may or may not resolve to an mse_products row -- both cases
handled below), and the sequence's `emails` JSONB blob exposed as its
step list. See docs/internal/email-approval-api.md for the full contract
and this gap's writeup.
"""

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.supabase_client import get_supabase

router = APIRouter(prefix="/marketing/internal", tags=["marketing-internal"])

_VALID_STATUSES = {"pending_hitl", "loaded_unactivated", "activated", "failed", "retired"}


def _require_admin(request: Request) -> str:
    if getattr(request.state, "role", "") != "admin":
        raise HTTPException(status_code=403, detail="Email approval queue requires admin role")
    actor = getattr(request.state, "tenant_id", None)
    if not actor:
        raise HTTPException(status_code=401, detail="No authenticated user to attribute this approval to")
    return actor


def _product_lookup(db) -> dict[str, dict]:
    result = db.table("mse_products").select("id,slug,name").execute()
    return {row["id"]: row for row in (result.data or [])}


def _serialize(row: dict, products: dict[str, dict]) -> dict[str, Any]:
    product = products.get(row["product_id"])
    steps = row.get("emails") or []
    first_step = steps[0] if isinstance(steps, list) and steps else {}
    return {
        "template_key": row["id"],
        "sequence_key": row["id"],
        "step_count": len(steps) if isinstance(steps, list) else None,
        "subject": first_step.get("subject") if isinstance(first_step, dict) else None,
        "preheader": first_step.get("preheader") if isinstance(first_step, dict) else None,
        "status": row["status"],
        "origin": row.get("origin", "generated"),
        "source_script": row.get("source_script"),
        "grounding_sources": row.get("grounding_sources", []),
        "product": {
            "id": row["product_id"],
            "slug": product["slug"] if product else None,
            "name": product["name"] if product else None,
            "resolved": product is not None,
        },
        "campaign_build_id": row.get("campaign_build_id"),
        "hitl_approved_by": row.get("hitl_approved_by"),
        "hitl_approved_at": row.get("hitl_approved_at"),
        "created_at": row.get("created_at"),
    }


@router.get("/email-templates")
async def list_email_templates(request: Request, status: Optional[str] = None, product_id: Optional[str] = None):
    _require_admin(request)
    db = get_supabase()
    query = db.table("mse_email_sequences").select("*")
    if status:
        if status not in _VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_VALID_STATUSES)}")
        query = query.eq("status", status)
    if product_id:
        query = query.eq("product_id", product_id)
    result = query.order("created_at", desc=True).execute()
    products = _product_lookup(db)
    return {"templates": [_serialize(row, products) for row in (result.data or [])]}


@router.get("/email-templates/{template_key}")
async def get_email_template(template_key: str, request: Request):
    _require_admin(request)
    db = get_supabase()
    result = db.table("mse_email_sequences").select("*").eq("id", template_key).maybe_single().execute()
    if result is None or not result.data:
        raise HTTPException(status_code=404, detail="Template not found")
    products = _product_lookup(db)
    row = result.data
    payload = _serialize(row, products)
    payload["steps"] = row.get("emails") or []
    return payload


class ApproveRequest(BaseModel):
    pass


@router.post("/email-templates/{template_key}/approve")
async def approve_email_template(template_key: str, request: Request, body: ApproveRequest = ApproveRequest()):
    actor = _require_admin(request)
    db = get_supabase()
    existing = db.table("mse_email_sequences").select("id,status").eq("id", template_key).maybe_single().execute()
    if existing is None or not existing.data:
        raise HTTPException(status_code=404, detail="Template not found")
    if existing.data["status"] not in ("pending_hitl", "loaded_unactivated"):
        raise HTTPException(status_code=409, detail=f"Cannot approve a template in status={existing.data['status']}")

    from datetime import datetime, timezone

    result = (
        db.table("mse_email_sequences")
        .update({
            "status": "activated",
            "hitl_approved_by": actor,
            "hitl_approved_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", template_key)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Approval write failed")
    return {"status": "activated", "template_key": template_key}


@router.post("/email-templates/{template_key}/retire")
async def retire_email_template(template_key: str, request: Request):
    _require_admin(request)
    db = get_supabase()
    result = db.table("mse_email_sequences").update({"status": "retired"}).eq("id", template_key).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"status": "retired", "template_key": template_key}


@router.get("/campaign-status")
async def campaign_status(request: Request):
    """One row per mse_products entry: real positioning state, plus a
    best-effort (honestly labeled) count of email sequences -- see this
    module's docstring for why sequence-to-product attribution is not
    reliable today."""
    _require_admin(request)
    db = get_supabase()
    products = db.table("mse_products").select("id,slug,name,status").execute().data or []
    positioning = db.table("mse_positioning").select("product_id,status,version").execute().data or []
    sequences = db.table("mse_email_sequences").select("product_id,status").execute().data or []

    pos_by_product: dict[str, dict] = {}
    for row in positioning:
        current = pos_by_product.get(row["product_id"])
        if current is None or row["version"] > current["version"]:
            pos_by_product[row["product_id"]] = row

    seq_product_ids = {row["product_id"] for row in sequences}
    resolved_ids = {p["id"] for p in products}

    rows = []
    for product in products:
        pos = pos_by_product.get(product["id"])
        rows.append({
            "product_id": product["id"],
            "slug": product["slug"],
            "name": product["name"],
            "product_status": product["status"],
            "positioning_status": pos["status"] if pos else "none",
            "positioning_version": pos["version"] if pos else None,
        })

    return {
        "products": rows,
        "email_sequences_total": len(sequences),
        "email_sequences_unattributable": len(seq_product_ids - resolved_ids),
        "note": (
            "email_sequences_unattributable counts sequences whose "
            "product_id does not match any mse_products row -- both real "
            "sequences today fall in this bucket (pre-date the DIST "
            "product registry). See docs/internal/email-approval-api.md."
        ),
    }


@router.get("/email-metrics")
async def email_metrics(request: Request, product_id: Optional[str] = None):
    """Sends/clicks/conversions tracking does not exist yet for lifecycle
    email (no mse_email_sends/mse_email_clicks tables) -- MKT-O3 sequences
    load into Brevo unactivated and BREVO_API_KEY has never been set, so
    nothing in this table has ever actually sent. Returns real counts of
    what IS trackable (sequences by status) and explicit nulls, not
    fabricated numbers, for what isn't."""
    _require_admin(request)
    db = get_supabase()
    query = db.table("mse_email_sequences").select("status")
    if product_id:
        query = query.eq("product_id", product_id)
    rows = query.execute().data or []
    from collections import Counter

    counts = Counter(row["status"] for row in rows)
    return {
        "sequences_by_status": dict(counts),
        "sends": None,
        "clicks": None,
        "unsubscribes": None,
        "conversions": None,
        "revenue": None,
        "note": (
            "sends/clicks/unsubscribes/conversions/revenue are null, not "
            "zero -- no send/click tracking table exists yet for "
            "lifecycle email (MKT-O3/Brevo has never sent a real email; "
            "BREVO_API_KEY is unset). See docs/internal/email-approval-api.md."
        ),
    }
