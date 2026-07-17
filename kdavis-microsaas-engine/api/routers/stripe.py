import os
import stripe
from fastapi import APIRouter, Request, HTTPException
from core.supabase_client import get_supabase

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

TIER_MAP = {
    "starter": "starter",
    "growth": "growth",
    "scale": "scale",
}


def _resolve_tier(subscription: stripe.Subscription) -> str:
    """Extract tier from Stripe price metadata, fallback to starter."""
    try:
        price = subscription["items"]["data"][0]["price"]
        return TIER_MAP.get(price.get("metadata", {}).get("tier", ""), "starter")
    except (KeyError, IndexError):
        return "starter"


@router.post("/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    secret = os.environ["STRIPE_WEBHOOK_SECRET"]

    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    db = get_supabase()
    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "customer.subscription.created":
        _handle_subscription_created(db, data)

    elif event_type == "customer.subscription.updated":
        _handle_subscription_updated(db, data)

    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(db, data)

    elif event_type == "invoice.payment_failed":
        _handle_payment_failed(db, data)

    return {"received": True}


def _handle_subscription_created(db, subscription):
    customer_id = subscription["customer"]
    subscription_id = subscription["id"]
    tier = _resolve_tier(subscription)

    # Supabase Auth user ID must be stored in Stripe customer metadata at checkout
    customer = stripe.Customer.retrieve(customer_id)
    supabase_user_id = customer.get("metadata", {}).get("supabase_user_id")
    if not supabase_user_id:
        return  # Can't link without auth user ID — checkout flow must set this

    db.table("tenants").upsert({
        "id": supabase_user_id,
        "name": customer.get("name") or customer.get("email", ""),
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription_id,
        "tier": tier,
        "status": "active",
    }, on_conflict="id").execute()


def _handle_subscription_updated(db, subscription):
    subscription_id = subscription["id"]
    tier = _resolve_tier(subscription)
    status = "active" if subscription["status"] == "active" else "paused"

    db.table("tenants").update({
        "tier": tier,
        "status": status,
        "stripe_subscription_id": subscription_id,
    }).eq("stripe_subscription_id", subscription_id).execute()


def _handle_subscription_deleted(db, subscription):
    subscription_id = subscription["id"]

    db.table("tenants").update({
        "status": "churned",
    }).eq("stripe_subscription_id", subscription_id).execute()

    # Suppress any active re-engagement sequences so churned users don't get drip emails.
    # maybe_single().execute() returns bare None (not a Response with
    # .data=None) when zero rows match — a subscription_id with no matching
    # tenant is an entirely normal case here, not an error; guard against it
    # explicitly rather than crashing the whole webhook.
    tenant_result = db.table("tenants").select("id").eq(
        "stripe_subscription_id", subscription_id
    ).maybe_single().execute()

    if tenant_result is not None and tenant_result.data:
        db.table("retention_sequences").update({
            "status": "suppressed",
        }).eq("tenant_id", tenant_result.data["id"]).eq("status", "active").execute()


def _handle_payment_failed(db, invoice):
    customer_id = invoice["customer"]
    subscription_id = invoice.get("subscription")

    tenant_result = db.table("tenants").select("id").eq(
        "stripe_customer_id", customer_id
    ).maybe_single().execute()

    if tenant_result is None or not tenant_result.data:
        return

    tenant_id = tenant_result.data["id"]

    # Log the failure event
    db.table("usage_events").insert({
        "tenant_id": tenant_id,
        "event_type": "payment_failed",
        "metadata": {
            "invoice_id": invoice["id"],
            "amount_due": invoice.get("amount_due"),
            "subscription_id": subscription_id,
        },
    }).execute()

    # Activate pre-billing re-engagement sequence if not already running
    existing_result = db.table("retention_sequences").select("id").eq(
        "tenant_id", tenant_id
    ).eq("sequence_type", "prebilling").eq("status", "active").maybe_single().execute()

    if existing_result is None or not existing_result.data:
        db.table("retention_sequences").insert({
            "tenant_id": tenant_id,
            "sequence_type": "prebilling",
            "status": "active",
            "current_step": 0,
        }).execute()
