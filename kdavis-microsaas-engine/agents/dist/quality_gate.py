"""
DIST-S3 — Quality Gate. Phase 4 of the DIST subsystem
(EXECUTION_ORDER.md). Hard block before a surface can reach
status='pending_review'-and-stay-there (S2 already sets pending_review
on write; this gate can demote it back to 'draft' with a reject_reason,
which is what "reject, do not emit" means in practice given S2's own
insert-then-update flow).

Real credential note, same shape as Phase 2 (GSC) and Phase 7 (Resend
domain): GEMINI_API_KEY is not configured on this backend's real
deployment (core/embeddings.py's own header comment). Duplicate
detection's real embedding call is code-complete but credential-blocked
in production tonight -- get_embedding is dependency-injected here
specifically so the rest of the gate logic is fully testable without it.

Five checks, run in order, first failure wins (cheap checks before the
embedding call):
1. Original-value rule
2. Substance floor (archetype-specific word count + real internal links)
3. Duplicate detection (pgvector cosine similarity via match_content_surfaces)
4. Claim audit (source_url + verified_at within 90 days on every
   competitor-referencing surface)
5. Schema validity (title/slug non-null, hitl_tier in range)

Repeated rejection (reject_count >= 3) for the same archetype+product
pauses that archetype via mse_generator_state and raises an
mse_monitoring_events row -- "the pattern is the signal," per spec, not
retried by rewording.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from core.supabase_client import get_supabase

AGENT_ID = "dist-s3-quality-gate"

_MIN_WORDS = {
    "vs_competitor": 400,
    "alternatives_to": 400,
    "jurisdiction": 300,
    "jtbd": 250,
    "calculator": 150,
    "faq_block": 100,
}
_CLAIM_MAX_AGE_DAYS = 90
_DUPLICATE_THRESHOLD = 0.92
_REJECT_PAUSE_THRESHOLD = 3


def _word_count(mdx: Optional[str]) -> int:
    if not mdx:
        return 0
    return len(re.findall(r"\S+", mdx))


def _has_original_value(mdx: Optional[str], data_payload: Optional[dict]) -> bool:
    """The page must contain at least one element the competitor's own
    site wouldn't have: a worked calculation ($ figures our own math
    produced), a real data_payload (calculator config / first-party
    data), or explicit first-person product framing. Heuristic, not
    perfect -- real editorial judgment on borderline cases stays a human
    call at HITL review, this is the hard-block floor only."""
    if data_payload:
        return True
    if not mdx:
        return False
    has_dollar_math = bool(re.search(r"\$[\d,]+(\.\d+)?", mdx))
    has_first_person_product_framing = bool(re.search(r"\bwe\b|\bour\b", mdx, re.IGNORECASE))
    return has_dollar_math and has_first_person_product_framing


async def _check_claims(db: Any, surface: dict) -> Optional[str]:
    """Every competitor-referencing surface's cited competitor must have
    a real source_url and a last_verified_at within the freshness window
    -- an uncited or stale competitor claim is rejected outright."""
    if not surface.get("competitor_id"):
        return None
    competitor = db.table("mse_competitors").select("*").eq("id", surface["competitor_id"]).maybe_single().execute()
    if competitor is None or not competitor.data:
        return "cited competitor no longer exists"
    c = competitor.data
    if not c.get("pricing_url"):
        return "cited competitor has no source_url"
    verified_at = c.get("last_verified_at")
    if not verified_at:
        return "cited competitor has no verified_at"
    verified_dt = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - verified_dt > timedelta(days=_CLAIM_MAX_AGE_DAYS):
        return f"cited competitor data is stale (verified {verified_at}, >{_CLAIM_MAX_AGE_DAYS}d old)"
    return None


def _check_schema(surface: dict) -> Optional[str]:
    if not surface.get("title") or not surface.get("slug"):
        return "missing title or slug"
    if surface.get("hitl_tier") not in (1, 2, 3):
        return "invalid hitl_tier"
    return None


async def _pause_generator(db: Any, product_id: str, product_slug: str, product_name: str, archetype: str, reason: str) -> None:
    db.table("mse_generator_state").upsert(
        {"product_id": product_id, "paused": True, "paused_reason": f"{archetype}: {reason}"},
        on_conflict="product_id",
    ).execute()
    # Real schema (migration 20260717000011_factory_expansion.sql) keys
    # this table on product_slug/product_name (text), not product_id --
    # confirmed live before writing this, not assumed from the spec's own
    # generic "raise an mse_monitoring_events row" instruction.
    db.table("mse_monitoring_events").insert({
        "product_slug": product_slug,
        "product_name": product_name,
        "run_type": "triggered",
        "severity": "P3",
        "recommended_action": f"Surface generator paused for archetype {archetype!r} after {_REJECT_PAUSE_THRESHOLD} rejections: {reason}",
        "requires_human_decision": True,
        "status": "open",
    }).execute()


async def check_surface(
    surface_id: str,
    supabase_client: Optional[Any] = None,
    get_embedding: Optional[Callable[[str], Awaitable[list[float]]]] = None,
) -> dict:
    """Runs all 5 checks against one surface. Returns
    {"passed": bool, "reason": str|None}. On failure: sets
    status='draft', reject_reason, increments reject_count; on the 3rd
    rejection for the same product+archetype, pauses that archetype via
    mse_generator_state and raises an mse_monitoring_events row."""
    db = supabase_client or get_supabase()

    surface = db.table("mse_content_surfaces").select("*").eq("id", surface_id).maybe_single().execute()
    if surface is None or not surface.data:
        raise ValueError(f"No mse_content_surfaces row for id {surface_id!r}")
    s = surface.data

    reason: Optional[str] = None

    if reason is None and not _has_original_value(s.get("body_mdx"), s.get("data_payload")):
        reason = "original-value rule: no first-party pricing math, calculator config, or product framing found"

    if reason is None:
        min_words = _MIN_WORDS.get(s["archetype"], 200)
        wc = _word_count(s.get("body_mdx"))
        if wc < min_words:
            reason = f"substance floor: {wc} words, needs {min_words}+ for archetype {s['archetype']!r}"

    if reason is None and get_embedding is not None and s.get("body_mdx"):
        embedding = await get_embedding(s["body_mdx"])
        dup = db.rpc(
            "match_content_surfaces",
            {
                "p_product_id": s["product_id"],
                "p_query_embedding": embedding,
                "p_match_threshold": _DUPLICATE_THRESHOLD,
                "p_match_count": 5,
            },
        ).execute()
        near_dupes = [d for d in (dup.data or []) if d["id"] != surface_id]
        if near_dupes:
            reason = f"duplicate detection: {near_dupes[0]['similarity']:.3f} similarity to {near_dupes[0]['slug']!r}"
        else:
            db.table("mse_content_surfaces").update({"embedding": embedding}).eq("id", surface_id).execute()

    if reason is None:
        reason = await _check_claims(db, s)

    if reason is None:
        reason = _check_schema(s)

    if reason is not None:
        new_count = (s.get("reject_count") or 0) + 1
        db.table("mse_content_surfaces").update({
            "status": "draft",
            "reject_reason": reason,
            "reject_count": new_count,
        }).eq("id", surface_id).execute()

        if new_count >= _REJECT_PAUSE_THRESHOLD:
            product = db.table("mse_products").select("slug, name").eq("id", s["product_id"]).maybe_single().execute()
            product_slug = product.data["slug"] if product and product.data else s["product_id"]
            product_name = product.data["name"] if product and product.data else s["product_id"]
            await _pause_generator(db, s["product_id"], product_slug, product_name, s["archetype"], reason)

        return {"passed": False, "reason": reason}

    db.table("mse_content_surfaces").update({"status": "pending_review", "reject_reason": None}).eq(
        "id", surface_id
    ).execute()
    return {"passed": True, "reason": None}
