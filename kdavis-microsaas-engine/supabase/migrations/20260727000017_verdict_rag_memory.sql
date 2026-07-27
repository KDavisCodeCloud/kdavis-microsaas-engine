-- Migration 017: Verdict RAG memory layer
--
-- Gives Verdict access to this factory's own evaluation history at scoring
-- time: what was scored, what was predicted, and (once available) what
-- actually happened after launch. Retrieval is a soft input, never a gate
-- -- if it fails or returns nothing, Verdict runs on its own scoring alone
-- (see rag/retriever.py).
--
-- RLS pattern matches the real, currently-correct convention in this repo
-- (fixed 2026-07-16 in migration 009, NOT the broken current_setting('app.role')
-- pattern migration 002 originally shipped with): admin-only read via
-- auth.jwt() -> 'app_metadata' ->> 'role', which is server-side-only and not
-- client-editable, unlike raw_user_meta_data.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS mse_rag_outcomes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_name TEXT NOT NULL,
  vertical TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('BUILD','CONDITIONAL','DO_NOT_BUILD')),
  confidence_score INTEGER,
  projected_mrr_floor INTEGER,
  primary_risk_flag TEXT,
  competitor_signals JSONB,
  review_pain_themes JSONB,
  rationale TEXT,
  outcome_actual TEXT DEFAULT NULL,
  actual_mrr_at_90_days INTEGER DEFAULT NULL,
  actual_mrr_at_180_days INTEGER DEFAULT NULL,
  verdict_accuracy TEXT DEFAULT NULL CHECK (verdict_accuracy IN ('ACCURATE','OVER','UNDER') OR verdict_accuracy IS NULL),
  embedding VECTOR(1536),
  opportunity_id UUID REFERENCES opportunity_pipeline(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mse_rag_retrieval_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  query_product_name TEXT NOT NULL,
  query_embedding VECTOR(1536),
  retrieved_outcome_ids UUID[],
  retrieval_score FLOAT[],
  used_in_verdict BOOLEAN DEFAULT true,
  error TEXT DEFAULT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Cosine-similarity search on mse_rag_outcomes.embedding, filtered to rows
-- that actually have one (unembedded rows are skipped silently by
-- rag/retriever.py, not an error condition).
CREATE INDEX IF NOT EXISTS mse_rag_outcomes_embedding_idx
  ON mse_rag_outcomes USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

ALTER TABLE mse_rag_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE mse_rag_retrieval_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS mse_rag_outcomes_admin_access ON mse_rag_outcomes;
CREATE POLICY mse_rag_outcomes_admin_access ON mse_rag_outcomes
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

DROP POLICY IF EXISTS mse_rag_retrieval_log_admin_access ON mse_rag_retrieval_log;
CREATE POLICY mse_rag_retrieval_log_admin_access ON mse_rag_retrieval_log
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
