"""DIST Phase 8.5 -- daily per-product MRR snapshot from Stripe.

Inert without STRIPE_SECRET_KEY (confirmed unset on this backend as of
2026-08-31, same disclosed gap as every other credential-blocked DIST
piece tonight). Deliberately does NOT read the key at import time
(unlike api/routers/stripe.py's module-level stripe.api_key = ...,
which would crash any process importing this module at all if unset) --
this is a background job module, not a route registered at app startup,
so failing loudly only when actually invoked is the right shape here.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any, Optional


class StripeNotConfiguredError(RuntimeError):
    pass


def _stripe_module():
    import stripe as stripe_sdk
    return stripe_sdk


def snapshot_product_mrr(
    product_id: str,
    stripe_price_ids: list[str],
    snapshot_date: Optional[date] = None,
    supabase_client: Optional[Any] = None,
    stripe_module: Optional[Any] = None,
) -> dict:
    """Sums active Stripe subscriptions across the given price IDs for one
    product, upserts one row into mse_product_mrr. Raises
    StripeNotConfiguredError (not a bare KeyError) if STRIPE_SECRET_KEY
    isn't set -- the caller (a daily cron/job runner) can catch this
    specifically and report the job as inert rather than crash."""
    api_key = os.getenv("STRIPE_SECRET_KEY")
    if not api_key:
        raise StripeNotConfiguredError(
            "STRIPE_SECRET_KEY is not set -- product MRR snapshot cannot run."
        )

    s = stripe_module or _stripe_module()
    s.api_key = api_key

    mrr_cents = 0
    active_subs = 0
    for price_id in stripe_price_ids:
        starting_after = None
        while True:
            page = s.Subscription.list(status="active", price=price_id, limit=100, starting_after=starting_after)
            for sub in page.data:
                for item in sub["items"]["data"]:
                    if item["price"]["id"] != price_id:
                        continue
                    unit_amount = item["price"].get("unit_amount") or 0
                    quantity = item.get("quantity") or 1
                    recurring = item["price"].get("recurring") or {}
                    interval = recurring.get("interval")
                    interval_count = recurring.get("interval_count") or 1
                    monthly = unit_amount * quantity
                    if interval == "year":
                        monthly = monthly / (12 * interval_count)
                    elif interval == "week":
                        monthly = (monthly * 52) / (12 * interval_count)
                    elif interval == "day":
                        monthly = (monthly * 365) / (12 * interval_count)
                    else:
                        monthly = monthly / interval_count
                    mrr_cents += monthly
                active_subs += 1
            if not page.get("has_more") or not page.data:
                break
            starting_after = page.data[-1].id

    row = {
        "product_id": product_id,
        "snapshot_date": (snapshot_date or date.today()).isoformat(),
        "mrr_cents": round(mrr_cents),
        "active_subs": active_subs,
    }

    if supabase_client is not None:
        supabase_client.table("mse_product_mrr").upsert(row, on_conflict="product_id,snapshot_date").execute()

    return row
