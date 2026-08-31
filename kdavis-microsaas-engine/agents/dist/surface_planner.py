"""
DIST-S1 — Surface Planner. Phase 4 of the DIST subsystem
(EXECUTION_ORDER.md). Reads a product's APPROVED positioning brief plus
its mse_competitors registry and emits a prioritized surface plan --
draft mse_content_surfaces rows, one per archetype instance.

Design invariant (EXECUTION_ORDER.md's own words): "DIST never generates
a page for a product whose positioning brief is unapproved." Enforced
here (refuses to plan at all without an approved row) AND at the DB
layer (migration 032's publish_surface() independently re-checks this at
publish time, so this function refusing to plan is defense in depth, not
the only gate).

Refuses vs_competitor/alternatives_to archetypes specifically when
wedge_type='temporary' (a temporary wedge means we can't defensibly claim
a competitor structurally can't match us -- they might ship it next
quarter) -- jtbd/calculator/faq_block archetypes are still planned since
they don't make a competitor-comparison claim.
"""
from __future__ import annotations

from typing import Any, Optional

from core.supabase_client import get_supabase

AGENT_ID = "dist-s1-surface-planner"

_COMPETITOR_ARCHETYPES = {"vs_competitor", "alternatives_to"}
_HITL_TIER = {
    "vs_competitor": 3,
    "alternatives_to": 3,
    "jurisdiction": 3,
    "jtbd": 2,
    "calculator": 2,
    "faq_block": 1,
}


def _slugify(text: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:80] or "surface"


async def plan_surfaces(product_slug: str, supabase_client: Optional[Any] = None) -> list[dict]:
    """Plans (inserts draft mse_content_surfaces rows for) one product.
    Returns the list of inserted rows -- empty list, not an error, if the
    product has no approved positioning brief; that's Phase 0 doing its
    job, not a failure of this function. Raises only for a genuinely
    unknown product_slug."""
    db = supabase_client or get_supabase()

    product = db.table("mse_products").select("id, name").eq("slug", product_slug).maybe_single().execute()
    if product is None or not product.data:
        raise ValueError(f"No mse_products row for slug {product_slug!r}")
    product_id = product.data["id"]

    # Phase 5 hook: honor Phase 2's indexation circuit breaker (real gap
    # confirmed tonight -- mse_generator_state existed and quality_gate.py
    # writes to it on 3+ rejections, but nothing actually read it before
    # planning new surfaces). Real, existing limit of the current schema:
    # mse_generator_state.paused is one flag per product_id (upserted on
    # that conflict key), not per-archetype, even though the spec's own
    # prose says "pauses that archetype" -- pausing the whole product's
    # planning run is what the table as built actually supports; adding
    # real per-archetype granularity is a schema change out of scope
    # tonight, not silently done here.
    state = db.table("mse_generator_state").select("paused").eq("product_id", product_id).maybe_single().execute()
    if state is not None and state.data and state.data.get("paused"):
        return []

    positioning = (
        db.table("mse_positioning")
        .select("*")
        .eq("product_id", product_id)
        .eq("status", "approved")
        .maybe_single()
        .execute()
    )
    if positioning is None or not positioning.data:
        return []

    pos = positioning.data
    wedge_type = pos["wedge_type"]

    plan_items: list[dict] = []

    if wedge_type == "structural":
        competitors = (
            db.table("mse_competitors").select("id, name").eq("product_id", product_id).execute()
        )
        for c in competitors.data or []:
            plan_items.append({
                "product_id": product_id,
                "archetype": "vs_competitor",
                "slug": f"vs-{_slugify(c['name'])}",
                "title": f"vs {c['name']}",
                "competitor_id": c["id"],
                "hitl_tier": _HITL_TIER["vs_competitor"],
                "status": "draft",
            })
            plan_items.append({
                "product_id": product_id,
                "archetype": "alternatives_to",
                "slug": f"alternatives-to-{_slugify(c['name'])}",
                "title": f"Alternatives to {c['name']}",
                "competitor_id": c["id"],
                "hitl_tier": _HITL_TIER["alternatives_to"],
                "status": "draft",
            })
    # wedge_type == 'temporary': no competitor-claim archetypes planned at
    # all, per the spec's own moat_risk exclusion rule.

    trigger_events = [t.strip() for t in pos.get("trigger_event", "").split(";") if t.strip()]
    for i, trigger in enumerate(trigger_events[:10]):
        plan_items.append({
            "product_id": product_id,
            "archetype": "jtbd",
            "slug": f"jtbd-{i + 1}-{_slugify(trigger[:40])}",
            "title": trigger[:120],
            "hitl_tier": _HITL_TIER["jtbd"],
            "status": "draft",
        })

    plan_items.append({
        "product_id": product_id,
        "archetype": "calculator",
        "slug": "pricing-calculator",
        "title": f"{product.data['name']} pricing calculator",
        "hitl_tier": _HITL_TIER["calculator"],
        "data_payload": {"price_rationale_source": "mse_positioning.price_rationale"},
        "status": "draft",
    })

    inserted: list[dict] = []
    for item in plan_items:
        result = db.table("mse_content_surfaces").upsert(item, on_conflict="product_id,slug").execute()
        if result.data:
            inserted.extend(result.data)
    return inserted
