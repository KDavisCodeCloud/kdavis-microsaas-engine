-- Migration 016: Verdict v3.0 price-adjusted MRR floor
--
-- v3.0 replaces the flat $4,000 MRR floor with a price-tiered one
-- ($19-29/mo -> $3,500, $39-59/mo -> $4,000, $69-99/mo -> $4,500,
-- $100+/mo -> $5,000) — a lower-priced product with a larger TAM
-- shouldn't be held to the same floor as a $100+/mo B2B product. The
-- flat mrr_floor_check constraint (migration 011) can't express a
-- per-row floor without knowing each row's own computed floor, so this
-- adds a column for it — populated by the aggregator (agents/aggregator/
-- agent.py's _price_adjusted_floor()), not left for the DB to compute,
-- since the price-tier logic lives in application code, not SQL.
--
-- Defaults to 4000 (the v2.0-era flat floor) for any row written before
-- this migration, or by any path that doesn't set it explicitly, so
-- existing data doesn't suddenly violate the constraint.

ALTER TABLE opportunity_pipeline
  ADD COLUMN IF NOT EXISTS price_adjusted_floor numeric NOT NULL DEFAULT 4000;

ALTER TABLE opportunity_pipeline DROP CONSTRAINT IF EXISTS mrr_floor_check;
ALTER TABLE opportunity_pipeline ADD CONSTRAINT mrr_floor_check
  CHECK ((conservative_mrr_potential >= price_adjusted_floor) OR (status = ANY (ARRAY['rejected', 'needs_correction'])));
