-- Migration 018: match_mse_rag_outcomes RPC
--
-- PostgREST (Supabase's REST layer, which rag/retriever.py uses via the
-- same supabase-py admin client every other module in this codebase uses)
-- has no way to express the pgvector <=> operator directly through
-- .select()/.filter(). The standard pattern is a Postgres function called
-- via .rpc() instead -- match_vertical is nullable: pass NULL to search
-- across all verticals (used by rag/retriever.py when a same-vertical
-- search returns fewer than 3 rows).

CREATE OR REPLACE FUNCTION match_mse_rag_outcomes(
  query_embedding vector(1536),
  match_vertical text DEFAULT NULL,
  match_count int DEFAULT 5
)
RETURNS TABLE (
  id uuid,
  product_name text,
  vertical text,
  verdict text,
  confidence_score int,
  projected_mrr_floor int,
  primary_risk_flag text,
  rationale text,
  outcome_actual text,
  actual_mrr_at_90_days int,
  verdict_accuracy text,
  similarity float
)
LANGUAGE sql STABLE
AS $$
  SELECT
    o.id, o.product_name, o.vertical, o.verdict, o.confidence_score,
    o.projected_mrr_floor, o.primary_risk_flag, o.rationale,
    o.outcome_actual, o.actual_mrr_at_90_days, o.verdict_accuracy,
    1 - (o.embedding <=> query_embedding) as similarity
  FROM mse_rag_outcomes o
  WHERE o.embedding IS NOT NULL
    AND (match_vertical IS NULL OR o.vertical = match_vertical)
  ORDER BY o.embedding <=> query_embedding
  LIMIT match_count;
$$;
