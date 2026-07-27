"""
rag/embedder.py — embeds a structured Verdict evaluation and stores the
vector onto mse_rag_outcomes.embedding.

No embedding provider exists anywhere in this codebase as of 2026-07-27 --
core/llm_router.py wraps Anthropic only (scrape/analyze/analyze_with_web_search),
and Anthropic does not offer a text-embedding endpoint. There is no
OPENAI_API_KEY, VOYAGE_API_KEY, or any other embedding-provider key in .env.
The RAG spec asks for 1536-dim vectors specifically, which matches OpenAI's
text-embedding-3-small at its default dimension -- used here as the most
direct fit, gated behind a NEW env var (OPENAI_API_KEY) that does not exist
yet. Until Kelvin adds it, every call below fails gracefully exactly per
spec ("on embedding failure, log the error... return gracefully, do not
raise") -- this is a real, currently-unmet infrastructure dependency, not
a bug in this file.
"""
import os
import logging
from typing import Optional

from core.supabase_client import get_supabase

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"  # 1536 dimensions at default setting
EMBEDDING_DIM = 1536


def _build_embedding_text(structured_input: dict) -> str:
    """
    Concatenates every field into ONE structured text block before embedding
    -- per spec, never embed fields separately (that would produce multiple
    vectors per outcome with no single similarity space to compare against).
    """
    parts = [
        f"Product: {structured_input.get('product_name', '')}",
        f"Vertical: {structured_input.get('vertical', '')}",
        f"Target buyer: {structured_input.get('target_buyer', '')}",
        f"Pain themes: {', '.join(structured_input.get('pain_themes', []) or [])}",
        f"Competitors: {', '.join(structured_input.get('competitor_names', []) or [])}",
        f"Verdict: {structured_input.get('verdict', '')}",
        f"Rationale: {structured_input.get('rationale', '')}",
    ]
    return "\n".join(parts)


def _embed_text(text: str) -> Optional[list[float]]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log.warning("[rag.embedder] OPENAI_API_KEY not set — embedding skipped, RAG runs with no vector for this row")
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
        return resp.data[0].embedding
    except Exception as e:
        log.warning("[rag.embedder] embedding call failed: %s", e)
        return None


def embed_and_store(outcome_id: str, structured_input: dict) -> bool:
    """
    Embeds the given structured input and writes it onto the matching
    mse_rag_outcomes row. Returns True on success, False on any failure --
    never raises, matching the "never block the pipeline" rule. Caller
    (embed_and_insert / outcome_writer) is responsible for deciding whether
    a False return needs a mse_rag_retrieval_log entry.
    """
    text = _build_embedding_text(structured_input)
    vector = _embed_text(text)
    if vector is None:
        return False
    if len(vector) != EMBEDDING_DIM:
        log.warning("[rag.embedder] embedding dim mismatch: got %d, expected %d", len(vector), EMBEDDING_DIM)
        return False

    db = get_supabase()
    try:
        db.table("mse_rag_outcomes").update({"embedding": vector}).eq("id", outcome_id).execute()
        return True
    except Exception as e:
        log.warning("[rag.embedder] failed to write embedding to mse_rag_outcomes id=%s: %s", outcome_id, e)
        return False


def embed_and_insert(structured_input: dict, extra_fields: Optional[dict] = None) -> Optional[str]:
    """
    Inserts a new mse_rag_outcomes row (embedding included in the same
    write, per spec's "same DB transaction as the outcome row insert" --
    Supabase's REST layer has no explicit multi-statement transaction, so
    this computes the embedding FIRST and inserts once with the vector
    already attached, which is the closest equivalent achievable through
    the same client this codebase already uses everywhere else (Supabase
    admin client, not a raw Postgres connection).

    Returns the new row's id, or None if the insert itself failed (an
    embedding failure alone does NOT block the insert — the row is still
    written with embedding=NULL, just invisible to similarity search until
    a real key exists and this is re-run).
    """
    text = _build_embedding_text(structured_input)
    vector = _embed_text(text)

    row = {
        "product_name":       structured_input.get("product_name", ""),
        "vertical":            structured_input.get("vertical", ""),
        "verdict":             structured_input.get("verdict", "DO_NOT_BUILD"),
        "confidence_score":    structured_input.get("confidence_score"),
        "projected_mrr_floor": structured_input.get("projected_mrr_floor"),
        "primary_risk_flag":   structured_input.get("primary_risk_flag"),
        "competitor_signals":  structured_input.get("competitor_signals"),
        "review_pain_themes":  structured_input.get("pain_themes"),
        "rationale":           structured_input.get("rationale"),
        "embedding":           vector,
        **(extra_fields or {}),
    }

    db = get_supabase()
    try:
        result = db.table("mse_rag_outcomes").insert(row).execute()
        new_id = result.data[0]["id"]
        if vector is None:
            db.table("mse_rag_retrieval_log").insert({
                "query_product_name": structured_input.get("product_name", ""),
                "retrieved_outcome_ids": [],
                "retrieval_score": [],
                "used_in_verdict": False,
                "error": "embedding skipped: no OPENAI_API_KEY configured (or embedding call failed)",
            }).execute()
        return new_id
    except Exception as e:
        log.warning("[rag.embedder] mse_rag_outcomes insert failed: %s", e)
        return None
