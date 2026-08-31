"""
DIST-Phase-2 — Indexation Monitor (2026-08-30).

DecodedSix already demonstrated the failure this exists to prevent: 44
pages crawled-not-indexed. Generating pages Google declines to index is
worse than generating zero -- it burns crawl budget and signals thin
content sitewide. Phase 4's surface generator reads mse_generator_state
before planning new surfaces for a product; this module is what sets it.

Real, confirmed limit as of 2026-08-30: no Google Search Console OAuth
credentials exist anywhere in this repo or its Railway env vars (only
Google Custom Search API keys exist, for agents/marketing's lead finder,
an unrelated product). _default_fetch_gsc_data raises a clear error
rather than fabricate a working connection -- provisioning real GSC
access is Kelvin's own step. sync_indexation/check_stale_and_alert both
accept dependency-injected callables/clients (same pattern as
agents/factory/brief_generator.py's generate_build_brief) specifically so
the real sync/alert/circuit-breaker logic is fully testable today without
that credential existing yet.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from core.supabase_client import get_supabase

AGENT_ID = "dist-indexation-monitor"
STALE_DAYS = 21
CIRCUIT_BREAKER_THRESHOLD = 3


class GSCAccessError(Exception):
    """Raised when a product's GSC property can't be read -- no
    credentials, no verified property, or a real API-reported access
    failure. Callers treat this as a clean skip, not a crash."""


def _default_fetch_gsc_data(property_url: str) -> list[dict[str, Any]]:
    """Real GSC Search Analytics + URL Inspection call. Not implemented
    against a real API yet because no OAuth credentials exist to call it
    with -- raises GSCAccessError rather than silently returning empty
    data, so a real caller can tell "not configured" apart from "zero
    pages found"."""
    creds = os.environ.get("GSC_OAUTH_CREDENTIALS_JSON")
    if not creds:
        raise GSCAccessError(
            "GSC OAuth credentials are not configured (GSC_OAUTH_CREDENTIALS_JSON unset) -- "
            "provision Google Search Console API access before indexation sync can run for real."
        )
    raise NotImplementedError(
        "GSC_OAUTH_CREDENTIALS_JSON is set but the real Search Analytics + URL Inspection "
        "API call is not implemented yet -- inject fetch_gsc_data for testing, or finish this "
        "function once real credentials exist to develop against."
    )


def sync_indexation(
    product_id: str,
    verified_property_url: Optional[str],
    supabase_client: Optional[Any] = None,
    fetch_gsc_data: Callable[[str], list[dict[str, Any]]] = _default_fetch_gsc_data,
) -> dict[str, Any]:
    """Upserts GSC coverage data for one product's verified property on
    (product_id, url). Handles a property with zero verified access --
    no verified_property_url, or fetch_gsc_data raising GSCAccessError --
    without crashing the run: upserts nothing, returns a clear
    skipped_reason instead. Deliberately omits first_seen_at from the
    upsert payload so repeat syncs never bump it -- the 21-day staleness
    check below depends on that column reflecting the true first sync,
    not the most recent one."""
    db = supabase_client or get_supabase()

    if not verified_property_url:
        return {"synced": 0, "skipped_reason": "no_verified_property"}

    try:
        rows = fetch_gsc_data(verified_property_url)
    except GSCAccessError as exc:
        return {"synced": 0, "skipped_reason": str(exc)}

    checked_at = datetime.now(timezone.utc).isoformat()
    synced = 0
    for row in rows:
        db.table("mse_indexation").upsert(
            {
                "product_id": product_id,
                "url": row["url"],
                "surface_slug": row.get("surface_slug"),
                "indexed": row.get("indexed"),
                "coverage_state": row.get("coverage_state"),
                "impressions": row.get("impressions", 0),
                "clicks": row.get("clicks", 0),
                "avg_position": row.get("avg_position"),
                "checked_at": checked_at,
            },
            on_conflict="product_id,url",
        ).execute()
        synced += 1

    return {"synced": synced, "skipped_reason": None}


def check_stale_and_alert(
    product_id: str,
    product_slug: str,
    product_name: str,
    supabase_client: Optional[Any] = None,
) -> dict[str, Any]:
    """21-day rule: any mse_indexation row with indexed=false and
    first_seen_at older than STALE_DAYS raises one mse_monitoring_events
    row per URL -- skipped for a URL that already has an open event of
    this kind, so a weekly run doesn't re-alert the same page forever.
    CIRCUIT_BREAKER_THRESHOLD or more currently-stale pages for one
    product pauses mse_generator_state for it; this module never
    unpauses one -- that's a human review action, by design (the
    circuit-breaker's whole point is that the pattern itself is the
    signal, not something a retry should paper over)."""
    db = supabase_client or get_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).isoformat()

    stale_response = (
        db.table("mse_indexation")
        .select("*")
        .eq("product_id", product_id)
        .eq("indexed", False)
        .lt("first_seen_at", cutoff)
        .execute()
    )
    stale_rows = stale_response.data or []

    alerts_raised = 0
    for row in stale_rows:
        existing = (
            db.table("mse_monitoring_events")
            .select("id")
            .eq("product_slug", product_slug)
            .eq("context", row["url"])
            .eq("status", "open")
            .execute()
        )
        if existing.data:
            continue
        db.table("mse_monitoring_events").insert(
            {
                "product_slug": product_slug,
                "product_name": product_name,
                "run_type": "nightly",
                "severity": "P3",
                "triggered_thresholds": [
                    {"metric": "days_since_first_seen", "value": STALE_DAYS, "threshold": STALE_DAYS}
                ],
                "recommended_action": (
                    f"{row['url']} has not been indexed by Google after {STALE_DAYS}+ days -- "
                    "review content quality or de-list this surface."
                ),
                "requires_human_decision": True,
                "context": row["url"],
                "status": "open",
            }
        ).execute()
        alerts_raised += 1

    stale_count = len(stale_rows)
    generator_paused = stale_count >= CIRCUIT_BREAKER_THRESHOLD
    if generator_paused:
        db.table("mse_generator_state").upsert(
            {
                "product_id": product_id,
                "paused": True,
                "paused_reason": (
                    f"{stale_count} pages crawled-not-indexed for {STALE_DAYS}+ days -- "
                    "circuit breaker on slop, review required to resume."
                ),
                "paused_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="product_id",
        ).execute()

    return {"alerts_raised": alerts_raised, "stale_count": stale_count, "generator_paused": generator_paused}
