"""
DIST-S2 — Surface Writer. Phase 4 of the DIST subsystem
(EXECUTION_ORDER.md). Takes one draft mse_content_surfaces row (from
DIST-S1's plan) and writes real body_mdx content for it.

Voice: senior engineer, no buzzwords, lead with the problem -- per-product
brand voice pulled from a real source where one exists (e.g. Small
Portfolio Hub's tailwind.config.ts header comment: light background, high
contrast, "Trust Blue" -- a deliberate ICP-driven departure from the
Decoded Empire base tokens). Where no real voice doc exists for a product,
falls back to this module's own SYSTEM_PROMPT default rather than
inventing a product-specific voice file that doesn't exist.

Every competitor factual claim MUST carry a source_url + verified_at from
the real mse_competitors row -- pulled programmatically and handed to the
model as already-sourced facts, not left for the model to cite from
memory. Uncited claims the model tries to add are stripped by DIST-S3
(quality_gate.py), not softened here.
"""
from __future__ import annotations

from typing import Any, Optional

from core.llm_router import analyze
from core.supabase_client import get_supabase

AGENT_ID = "dist-s2-surface-writer"

_DEFAULT_VOICE = (
    "Senior engineer voice. No buzzwords, no marketing fluff, no exclamation points. "
    "Lead with the concrete problem the reader has, then the concrete answer. "
    "Every number is real and sourced; never invent one."
)

SYSTEM_PROMPT = """You write one page of MDX content for a micro-SaaS product's \
marketing site. You are given: the product's real approved positioning brief, the \
page's archetype and title, and (if this page cites a competitor) that competitor's \
real, sourced pricing/positioning data.

Hard rules:
1. Every factual claim about a competitor must be traceable to the sourced data you \
were given -- if you were not given a source_url/verified_at for a claim, do not \
make that claim.
2. The page must include at least one piece of original value the competitor's own \
site doesn't have: real pricing math worked out for the reader, a first-party data \
point from the positioning brief, or a concrete worked example.
3. Do not restate competitor marketing copy as if it were a neutral fact.
4. Output ONLY the MDX body content, no frontmatter, no code fences around the whole \
thing, no meta-commentary about what you're doing."""


async def write_surface(surface_id: str, supabase_client: Optional[Any] = None) -> dict:
    """Generates and writes body_mdx for one draft mse_content_surfaces
    row, advancing it to status='pending_review' (still gated by
    DIST-S3's quality check before it can move further -- writing content
    does not itself pass the quality gate). Raises if the row, its
    product, or (for competitor archetypes) its competitor can't be
    found."""
    db = supabase_client or get_supabase()

    surface = db.table("mse_content_surfaces").select("*").eq("id", surface_id).maybe_single().execute()
    if surface is None or not surface.data:
        raise ValueError(f"No mse_content_surfaces row for id {surface_id!r}")
    s = surface.data

    product = db.table("mse_products").select("name, slug").eq("id", s["product_id"]).maybe_single().execute()
    if product is None or not product.data:
        raise ValueError(f"No mse_products row for id {s['product_id']!r}")

    positioning = (
        db.table("mse_positioning")
        .select("*")
        .eq("product_id", s["product_id"])
        .eq("status", "approved")
        .maybe_single()
        .execute()
    )
    if positioning is None or not positioning.data:
        raise ValueError(f"Cannot write content for {product.data['slug']!r}: no approved positioning brief")
    pos = positioning.data

    competitor_block = ""
    if s.get("competitor_id"):
        competitor = db.table("mse_competitors").select("*").eq("id", s["competitor_id"]).maybe_single().execute()
        if competitor is None or not competitor.data:
            raise ValueError(f"Surface {surface_id!r} references missing competitor {s['competitor_id']!r}")
        c = competitor.data
        competitor_block = (
            f"\nCompetitor (sourced, cite exactly this): {c['name']}\n"
            f"Pricing snapshot: {c.get('pricing_snapshot')}\n"
            f"Source URL: {c.get('pricing_url')}\n"
            f"Last verified: {c.get('last_verified_at')}\n"
        )

    voice = _DEFAULT_VOICE  # per-product override point; no product-specific
    # voice doc exists for any of tonight's 4 products beyond design-token
    # comments (checked: Small Portfolio Hub's tailwind.config.ts describes
    # visual, not content, voice) -- using the shared default honestly
    # rather than inventing a product voice file.

    user_prompt = (
        f"Product: {product.data['name']}\n"
        f"Positioning statement inputs -- ICP: {pos['icp']}\nWedge: {pos['wedge']}\n"
        f"Price rationale: {pos['price_rationale']}\n"
        f"Archetype: {s['archetype']}\nPage title: {s['title']}\n"
        f"{competitor_block}\n"
        f"Voice: {voice}\n\n"
        "Write the MDX body content for this page."
    )

    body_mdx = analyze(SYSTEM_PROMPT, user_prompt, max_tokens=3000)

    result = (
        db.table("mse_content_surfaces")
        .update({"body_mdx": body_mdx, "status": "pending_review"})
        .eq("id", surface_id)
        .execute()
    )
    return result.data[0]
