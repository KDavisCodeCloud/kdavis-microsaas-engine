"""
SUP-K1 — Support Gap Analyzer. Phase 7 of the DIST subsystem
(EXECUTION_ORDER.md). Weekly. Groups resolved/answered tickets by
`classification`; any key with 3+ occurrences in the trailing 30 days
gets a real faq_block draft written into mse_content_surfaces (Phase 4's
table -- built in a separate fork later tonight, may not exist yet when
this runs; handled explicitly below, not assumed present).

"This is the actual scaling mechanism. Support load doesn't fall because
the bot gets better — it falls because recurring questions stop being
askable." (spec, 7.3) Ticket volume is a product-quality metric, not
something to automate away by hiding it.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.llm_router import analyze
from core.supabase_client import get_supabase

AGENT_ID = "sup-k1-gap-analyzer"
_OCCURRENCE_THRESHOLD = 3
_LOOKBACK_DAYS = 30

SYSTEM_PROMPT = """You are writing a short FAQ entry for a product's support help center, \
based on a recurring question real customers have asked. Given the classification key and \
2-3 real example tickets, write:

{"title": "a short, search-friendly question as a real customer would type it", \
"body_mdx": "a direct, 2-4 sentence answer in plain markdown, no headers needed"}

Return ONLY the JSON object."""


def _mse_content_surfaces_exists(db) -> bool:
    try:
        db.table("mse_content_surfaces").select("id").limit(1).execute()
        return True
    except Exception:
        return False


async def analyze_gaps(product_id: str, supabase_client=None, llm_analyze=analyze) -> dict[str, Any]:
    """Returns {"faq_drafts_written": [...], "docs_gaps_flagged": [...],
    "surfaces_table_available": bool} -- the last field tells the caller
    honestly whether faq_block drafts could actually be written this run
    (Phase 4's table may not exist yet)."""
    db = supabase_client if supabase_client is not None else get_supabase()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)).isoformat()
    tickets = (
        db.table("mse_support_tickets")
        .select("id,classification,subject,body")
        .eq("product_id", product_id)
        .gte("created_at", cutoff)
        .execute()
        .data
        or []
    )

    counts = Counter(t["classification"] for t in tickets if t.get("classification"))
    recurring = [key for key, n in counts.items() if n >= _OCCURRENCE_THRESHOLD]

    surfaces_available = _mse_content_surfaces_exists(db)
    faq_drafts_written: list[dict[str, Any]] = []
    docs_gaps_flagged: list[str] = []

    for key in recurring:
        examples = [t for t in tickets if t.get("classification") == key][:3]
        example_text = "\n\n".join(f"Subject: {e.get('subject') or '(none)'}\nBody: {e['body']}" for e in examples)
        user_prompt = f"classification: {key}\n\nExample tickets:\n{example_text}"
        raw = llm_analyze(SYSTEM_PROMPT, user_prompt, max_tokens=400)

        import json
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            docs_gaps_flagged.append(key)
            continue
        parsed = json.loads(match.group(0))

        if surfaces_available:
            slug = f"faq-{key}".replace("_", "-")
            row = {
                "product_id": product_id,
                "archetype": "faq_block",
                "slug": slug,
                "title": parsed["title"],
                "body_mdx": parsed["body_mdx"],
                "hitl_tier": 1,
                "status": "draft",
            }
            inserted = db.table("mse_content_surfaces").upsert(row, on_conflict="product_id,slug").execute()
            faq_drafts_written.append(inserted.data[0] if inserted.data else row)
        else:
            docs_gaps_flagged.append(key)

    return {
        "faq_drafts_written": faq_drafts_written,
        "docs_gaps_flagged": docs_gaps_flagged,
        "surfaces_table_available": surfaces_available,
        "recurring_classifications": recurring,
    }
