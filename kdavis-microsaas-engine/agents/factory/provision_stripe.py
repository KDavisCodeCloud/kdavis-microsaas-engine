"""
Factory Stripe provisioning — Phase 6c of the MSE build/deploy pipeline.

ADR-006 requires a genuinely dedicated Stripe account per product, and
creating a real, independent Stripe account isn't automatable via any API
— it needs an actual business/banking signup flow. Decided 2026-07-16:
Kelvin does that one manual step per product; this module takes over from
there — given the resulting account's secret key, it creates the tier
Products/Prices and registers the production webhook.
"""
from typing import Any, Optional

import stripe

AGENT_ID = "factory-provision-stripe"

WEBHOOK_EVENTS = [
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
]


def create_tier_products(
    product_name: str, tier_structure: dict, api_key: str, stripe_module: Optional[Any] = None,
) -> dict:
    """Creates one Stripe Product + monthly Price per tier (e.g.
    {"starter": 29, "growth": 59, "scale": 99}). Returns
    {tier_name: {"product_id":..., "price_id":...}}.

    Price metadata carries "tier" — api/routers/stripe.py's _resolve_tier
    (copied into every scaffolded product) reads exactly that key. If it
    doesn't match, every subscriber silently defaults to "starter" — a
    real gap found and left unresolved on kdavis-microsaas-engine's own
    Stripe account 2026-07-15 (no real Price IDs existed yet to check
    against). Don't repeat it here; this is why tier metadata is set
    unconditionally below, not left to a manual dashboard step."""
    if not tier_structure:
        raise ValueError("tier_structure is empty — refusing to provision a product with no billable tiers")

    s = stripe_module or stripe
    s.api_key = api_key

    result = {}
    for tier_name, price_dollars in tier_structure.items():
        product = s.Product.create(name=f"{product_name} — {tier_name.title()}")
        price = s.Price.create(
            product=product["id"],
            unit_amount=int(round(float(price_dollars) * 100)),
            currency="usd",
            recurring={"interval": "month"},
            metadata={"tier": tier_name},
        )
        result[tier_name] = {"product_id": product["id"], "price_id": price["id"]}
    return result


def create_webhook(backend_url: str, api_key: str, stripe_module: Optional[Any] = None) -> dict:
    """Registers the production webhook endpoint. Returns
    {"id": str, "secret": "whsec_..."} — the secret is only ever returned
    once, at creation time; the caller must persist it immediately."""
    s = stripe_module or stripe
    s.api_key = api_key

    endpoint = s.WebhookEndpoint.create(
        url=f"{backend_url.rstrip('/')}/webhooks/stripe",
        enabled_events=WEBHOOK_EVENTS,
        description="Production webhook — provisioned by factory pipeline",
    )
    return {"id": endpoint["id"], "secret": endpoint["secret"]}


def provision_stripe(
    product_name: str, tier_structure: dict, backend_url: str, api_key: str,
    stripe_module: Optional[Any] = None,
) -> dict:
    """Full 6c flow: create tier Products/Prices, register the webhook.
    Raises on any failure at any stage — never fails silently."""
    tiers = create_tier_products(product_name, tier_structure, api_key, stripe_module=stripe_module)
    webhook = create_webhook(backend_url, api_key, stripe_module=stripe_module)
    return {"tiers": tiers, "webhook_id": webhook["id"], "webhook_secret": webhook["secret"]}
