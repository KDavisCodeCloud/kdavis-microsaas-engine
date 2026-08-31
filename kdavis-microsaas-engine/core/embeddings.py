"""
Gemini embeddings — DIST Phase 7's mse_support_kb RAG layer.

No embedding capability existed anywhere in this repo before this file
(confirmed live: no google-genai import, no GEMINI_API_KEY anywhere in
requirements.txt/.env.example/core/). The Phase 7 spec assumed "reuses
the pgvector + Gemini embedding pattern already running in nova/memory/"
as "no new infrastructure" — true for jarvis-decoded (the sibling repo
that pattern actually lives in), not true for this repo. Ported the real,
proven call shape from jarvis-decoded's providers/gemini.py::get_embedding
exactly (same model, same 768 dimensionality, matching
mse_support_kb.embedding vector(768)) rather than inventing a different
one. GEMINI_API_KEY is not yet configured on this backend's real deployment
— same open-credential-gap shape as BREVO_API_KEY/STRIPE_SECRET_KEY
elsewhere in this portfolio, flagged in the build report, not hidden.
"""
from __future__ import annotations

import os

from google import genai
from google.genai import types

_EMBEDDING_MODEL = "gemini-embedding-001"
_EMBEDDING_DIM = 768  # matches mse_support_kb.embedding vector(768) exactly

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _client


async def get_embedding(text: str) -> list[float]:
    """Used for both storing KB chunks and embedding an incoming ticket to
    query against them -- matches jarvis-decoded's research_store.py
    exactly, which uses this same single function (always
    RETRIEVAL_DOCUMENT, no separate query-time task type) for both its own
    insert and query paths. Verified against that file directly rather
    than assumed."""
    client = _get_client()
    result = await client.aio.models.embed_content(
        model=_EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(output_dimensionality=_EMBEDDING_DIM, task_type="RETRIEVAL_DOCUMENT"),
    )
    return list(result.embeddings[0].values)
