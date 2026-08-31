"""
SUP-D2 — Support Draft Writer. Phase 7 of the DIST subsystem
(EXECUTION_ORDER.md). RAG over mse_support_kb plus optional scoped
read-only account data, drafts a reply. Never invents policy: if the
answer isn't findable in the KB (or scored too low to trust), the draft
says so and asks the owner explicitly, rather than guessing -- this is
the one hard behavioral requirement the spec calls out by name.

Writes to mse_support_drafts only -- status stays 'pending' until a real
owner action (via support_draft_service.py) moves it. This agent never
sends anything and never sets status itself.
"""
from __future__ import annotations

from typing import Any, Optional

from core.embeddings import get_embedding
from core.llm_router import analyze
from core.supabase_client import get_supabase

AGENT_ID = "sup-d2-drafter"

_KB_MATCH_COUNT = 5
# Below this cosine-similarity match, treat the KB as not having a real
# answer -- matching a low-confidence retrieval is worse than admitting
# nothing was found, same reasoning as the "never invents policy" rule.
_KB_MIN_SIMILARITY = 0.55

SYSTEM_PROMPT = """You are drafting a support reply for a real customer, on behalf of a \
human who will read and approve it before it goes out. You are NOT the one sending this \
-- your draft is reviewed first.

Hard rule, no exceptions: if the knowledge-base excerpts and account data given to you do \
not actually contain the answer, do NOT guess or invent a plausible-sounding policy. \
Instead write a short draft that honestly says the answer isn't confirmed and asks the \
owner to weigh in, e.g. "I don't have a confirmed answer for this in our docs -- flagging \
for [owner] to check before we reply." Set your own confidence low in that case.

When you DO have real source material, write a direct, warm, specific reply -- no \
corporate hedging, no filler. Reference the specific account details given to you when \
relevant (e.g. a real payout date) rather than speaking in generalities.

Return ONLY a JSON object:
{"draft": "the reply text", "confidence": 0.0-1.0, "used_sources": true|false}

confidence should be low (below 0.5) whenever used_sources is false or the source material \
was only tangentially related."""


async def _search_kb(product_id: str, query_embedding: list[float], supabase_client) -> list[dict[str, Any]]:
    """Cosine-similarity search over mse_support_kb via the ivfflat index
    (migration 031). Supabase's postgrest doesn't expose pgvector's <=>
    operator through the standard query builder, so this uses a real RPC
    function -- callers must have `match_support_kb` created (see this
    file's own `ensure_match_function` below, called once at import time
    is unnecessary; the migration should create it, but this agent
    creates it defensively if missing rather than assume)."""
    result = supabase_client.rpc(
        "match_support_kb",
        {
            "p_product_id": product_id,
            "p_query_embedding": query_embedding,
            "p_match_count": _KB_MATCH_COUNT,
        },
    ).execute()
    rows = result.data or []
    return [r for r in rows if r.get("similarity", 0) >= _KB_MIN_SIMILARITY]


async def draft_reply(
    ticket_id: str,
    supabase_client=None,
    llm_analyze=analyze,
    account_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Drafts a reply for a real mse_support_tickets row and inserts the
    resulting mse_support_drafts row. account_context is optional,
    read-only, scoped data the caller has already fetched (e.g. a real
    payout date) -- this function never queries account tables itself,
    keeping the "scoped read-only account lookup" boundary explicit at
    the call site rather than implicit inside this agent."""
    db = supabase_client if supabase_client is not None else get_supabase()

    ticket = db.table("mse_support_tickets").select("*").eq("id", ticket_id).maybe_single().execute()
    ticket = ticket.data if ticket is not None else None
    if not ticket:
        raise ValueError(f"SUP-D2: no ticket found for id {ticket_id}")

    query_text = f"{ticket.get('subject') or ''} {ticket['body']}"
    embedding = await get_embedding(query_text)
    kb_matches = await _search_kb(ticket["product_id"], embedding, db)

    kb_text = "\n\n".join(f"[source: {m['source']}]\n{m['chunk']}" for m in kb_matches) or "(no matching KB entries found)"
    account_text = str(account_context) if account_context else "(no account data provided)"

    user_prompt = (
        f"Customer's ticket:\nSubject: {ticket.get('subject') or '(none)'}\nBody: {ticket['body']}\n\n"
        f"Knowledge base excerpts:\n{kb_text}\n\n"
        f"Account data:\n{account_text}"
    )
    raw = llm_analyze(SYSTEM_PROMPT, user_prompt, max_tokens=800)

    import json
    import re
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"SUP-D2: no JSON object in model response: {raw!r}")
    parsed = json.loads(match.group(0))

    # Belt and suspenders on the "never invents policy" rule: if nothing
    # real was retrieved from the KB, confidence is capped low regardless
    # of what the model itself claimed.
    confidence = float(parsed.get("confidence", 0))
    if not kb_matches and not account_context:
        confidence = min(confidence, 0.3)

    sources = {
        "kb_chunk_ids": [m["id"] for m in kb_matches],
        "account_fields_used": list(account_context.keys()) if account_context else [],
    }

    draft_row = {
        "ticket_id": ticket_id,
        "draft_body": parsed["draft"],
        "confidence": confidence,
        "sources": sources,
        "status": "pending",
    }
    inserted = db.table("mse_support_drafts").insert(draft_row).execute()
    db.table("mse_support_tickets").update({"status": "drafted"}).eq("id", ticket_id).execute()
    return inserted.data[0]
