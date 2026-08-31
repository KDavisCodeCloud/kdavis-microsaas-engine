"""
DIST-P1 — Positioning Researcher. Phase 0 of the DIST subsystem
(EXECUTION_ORDER.md). Given a product, enumerates the real substitute
set a buyer chooses between if this product didn't exist -- explicitly
including free/open-source options and a do_nothing baseline -- and
drafts an mse_positioning row. Never writes status='approved'; the DB
itself blocks that at the trigger level (migration 029) regardless of
what this agent attempts.

Real, sourced research via core.llm_router.analyze_with_web_search --
not invented competitor names or prices. A row that can't find 3 real
substitutes plus evidence should come back thin/draft rather than
fabricate to hit the shape requirement; DIST-P2 (wedge_validator.py)
is the adversarial check on top of this, not this agent policing itself.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Optional

from core.llm_router import analyze_with_web_search
from core.supabase_client import get_supabase

AGENT_ID = "dist-p1-positioning-researcher"

SYSTEM_PROMPT = """You are a positioning researcher for a micro-SaaS factory. Your \
one job: given a product idea, find the REAL substitute set -- everything a real \
buyer would do or use instead if this product didn't exist. This is not a \
competitor list. It is the actual consideration set of a specific buyer.

Hard requirements:
1. Search explicitly for free and open-source tools in this space, not just paid \
competitors. Free tools are very often the real substitute, not the paid category \
leader.
2. Search explicitly for how the buyer solves this today with NO software at all \
(spreadsheets, manual process, doing nothing, an in-house build, hiring someone).
3. Every entry needs a real source URL and today's verification date -- if you \
cannot find a real source for a claim, do not include the claim.
4. Return AT LEAST 3 substitute entries, and EXACTLY ONE of them must have \
kind = "do_nothing" (price_to_buyer: 0, monetization: null, switching_cost_hours: 0, \
source_url: null).

Return ONLY a single JSON object with this exact shape, nothing else, no markdown \
fences:
{
  "icp": "specific buyer description, not a segment",
  "trigger_event": "what makes this buyer look TODAY, not eventually",
  "substitute_set": [
    {"name": str, "kind": "free_tool"|"paid_tool"|"do_nothing"|"manual_process"|"in_house_build"|"agency_service",
     "price_to_buyer": number, "monetization": str|null, "switching_cost_hours": number,
     "source_url": str|null, "verified_at": "YYYY-MM-DD"}
  ],
  "wedge": "what the substitute set structurally cannot do",
  "wedge_type": "structural"|"temporary",
  "wedge_evidence": {"claims": [{"claim": str, "source_url": str, "verified_at": "YYYY-MM-DD"}]},
  "price_rationale": "argued against the CHEAPEST real substitute, not the most expensive",
  "kill_criteria": "concrete, numeric conditions that would stop funding this"
}

wedge_type = "structural" only if closing the gap would break a substitute's own \
revenue model or require abandoning a customer segment they currently serve. If any \
substitute could plausibly ship the fix in a normal roadmap quarter, wedge_type must \
be "temporary" -- do not inflate this."""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in DIST-P1 output: {text[:300]}")
    return json.loads(match.group(0))


async def research_positioning(product_slug: str, context: str, supabase_client: Optional[Any] = None) -> dict:
    """Runs DIST-P1 for one product and inserts a draft mse_positioning row
    (status='draft'). `context` is a short brief of what the product does --
    P1 has no other source of truth about the product itself, only about
    the market around it. Returns the inserted row. Raises if the product
    slug isn't in mse_products, or if the output doesn't parse into the
    required shape (never silently inserts a malformed row)."""
    db = supabase_client or get_supabase()

    # maybe_single().execute() returns bare None (not a Response with
    # .data=None) when zero rows match -- real, documented quirk of this
    # repo's supabase-py version (see api/routers/stripe.py's own comment
    # on the same behavior). Guard explicitly rather than assume an object.
    product = db.table("mse_products").select("id, name").eq("slug", product_slug).maybe_single().execute()
    if product is None or not product.data:
        raise ValueError(f"No mse_products row for slug {product_slug!r}")
    product_id = product.data["id"]

    user_prompt = (
        f"Product: {product.data['name']}\n"
        f"What it does: {context}\n\n"
        f"Today's date for verification purposes: {date.today().isoformat()}\n\n"
        "Research the real substitute set for this product's actual buyer and return the JSON object specified."
    )

    raw = analyze_with_web_search(SYSTEM_PROMPT, user_prompt, max_uses=12, max_tokens=6000)
    parsed = _extract_json(raw)

    existing = (
        db.table("mse_positioning")
        .select("version")
        .eq("product_id", product_id)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    next_version = (existing.data[0]["version"] + 1) if existing.data else 1

    row = {
        "product_id": product_id,
        "version": next_version,
        "icp": parsed["icp"],
        "trigger_event": parsed["trigger_event"],
        "substitute_set": parsed["substitute_set"],
        "wedge": parsed["wedge"],
        "wedge_type": parsed["wedge_type"],
        "wedge_evidence": parsed["wedge_evidence"],
        "price_rationale": parsed["price_rationale"],
        "kill_criteria": parsed["kill_criteria"],
        "status": "draft",
    }
    result = db.table("mse_positioning").insert(row).execute()
    return result.data[0]
