"""
rag/retriever.py — retrieves the most similar prior Verdict outcomes for a
new submission, so Verdict can calibrate its confidence score against this
factory's own history instead of scoring fresh every time.

Retrieval is a SOFT input, never a gate: if it fails or returns nothing,
Verdict runs on its own scoring alone (see agents/aggregator/agent.py's
_evaluate, which wraps this call in the same try/except-and-continue
pattern used everywhere else "never trust a single upstream signal
alone" applies in this codebase).
"""
import logging
from typing import Optional

from core.supabase_client import get_supabase
from rag.embedder import _embed_text, _build_embedding_text  # reuse, don't duplicate

log = logging.getLogger(__name__)

MAX_TOP_K = 5


def _log_retrieval(query_product_name: str, query_embedding: Optional[list], ids: list[str], scores: list[float], used: bool, error: Optional[str] = None) -> None:
    db = get_supabase()
    try:
        db.table("mse_rag_retrieval_log").insert({
            "query_product_name": query_product_name,
            "query_embedding": query_embedding,
            "retrieved_outcome_ids": ids,
            "retrieval_score": scores,
            "used_in_verdict": used,
            "error": error,
        }).execute()
    except Exception as e:
        # Logging the retrieval itself failing is not a reason to fail the
        # retrieval — this is diagnostic-only, never load-bearing.
        log.warning("[rag.retriever] failed to write mse_rag_retrieval_log: %s", e)


def _retrieve_similar_outcomes_sync(query: dict, top_k: int = 5) -> list[dict]:
    """
    Real implementation. Every call inside (Supabase-py, OpenAI's sync SDK)
    is actually synchronous I/O in this codebase -- there is no genuine
    async work happening, so this does the real work directly rather than
    through an event loop. agents/aggregator/agent.py's _evaluate (sync)
    calls this directly; retrieve_similar_outcomes below exists only to
    match the async signature the RAG spec asked for, for any future
    async caller (e.g. a FastAPI route).
    """
    top_k = min(top_k, MAX_TOP_K)
    product_name = query.get("product_name", "")

    text = _build_embedding_text(query)
    query_embedding = _embed_text(text)

    if query_embedding is None:
        _log_retrieval(product_name, None, [], [], used=False, error="no query embedding available (OPENAI_API_KEY unset or embedding call failed)")
        return []

    db = get_supabase()
    vertical = query.get("vertical", "")

    try:
        # Supabase's Python client has no native pgvector <=> operator
        # helper, so this calls a Postgres function (match_mse_rag_outcomes,
        # created alongside this file — see migration 018) via RPC rather
        # than hand-building raw SQL through the REST layer, which doesn't
        # support the <=> operator directly.
        result = db.rpc("match_mse_rag_outcomes", {
            "query_embedding": query_embedding,
            "match_vertical": vertical,
            "match_count": top_k,
        }).execute()
        rows = result.data or []

        if len(rows) < 3 and vertical:
            # Expand to all verticals if the same-vertical result set is thin.
            result_all = db.rpc("match_mse_rag_outcomes", {
                "query_embedding": query_embedding,
                "match_vertical": None,
                "match_count": top_k,
            }).execute()
            rows = result_all.data or rows

        ids = [r["id"] for r in rows]
        scores = [r.get("similarity", 0.0) for r in rows]
        _log_retrieval(product_name, query_embedding, ids, scores, used=True)
        return rows

    except Exception as e:
        log.warning("[rag.retriever] retrieval failed: %s", e)
        _log_retrieval(product_name, query_embedding, [], [], used=False, error=str(e))
        return []


async def retrieve_similar_outcomes(query: dict, top_k: int = 5) -> list[dict]:
    """Async entry point matching the RAG spec's signature -- see
    _retrieve_similar_outcomes_sync above for the real implementation and
    why this is a thin wrapper rather than genuine async I/O."""
    return _retrieve_similar_outcomes_sync(query, top_k)


def format_rag_context_block(outcomes: list[dict]) -> str:
    """
    Builds the RAG CONTEXT block for injection into Verdict's system prompt,
    BEFORE the scoring instructions per spec. Returns "" if outcomes is
    empty — no placeholder/note is emitted when retrieval returns nothing,
    per spec.
    """
    if not outcomes:
        return ""

    lines = [
        "---",
        "RAG CONTEXT — SIMILAR PRIOR EVALUATIONS",
        "The following evaluations from this factory's history are the most similar",
        "to the current submission. Use them to calibrate your confidence score.",
        "Do not treat them as rules — treat them as evidence.",
        "",
    ]
    for o in outcomes:
        actual_90 = o.get("actual_mrr_at_90_days")
        accuracy = o.get("verdict_accuracy")
        lines.append(f"Product: {o.get('product_name')} | Vertical: {o.get('vertical')}")
        lines.append(f"Verdict: {o.get('verdict')} | Confidence: {o.get('confidence_score')} | Projected MRR Floor: ${o.get('projected_mrr_floor')}")
        lines.append(f"Actual MRR at 90 days: {actual_90 if actual_90 is not None else 'not yet available'}")
        lines.append(f"Verdict accuracy: {accuracy if accuracy else 'not yet evaluated'}")
        lines.append(f"Primary risk that was flagged: {o.get('primary_risk_flag')}")
        lines.append(f"Rationale: {o.get('rationale')}")
        lines.append("")
    lines.append("---")
    return "\n".join(lines)
