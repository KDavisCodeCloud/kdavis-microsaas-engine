-- Migration 030: DIST Phase 2 — Indexation Monitor
-- DecodedSix demonstrated the failure this prevents: 44 pages crawled-not-
-- indexed. Generating pages Google declines to index is worse than
-- generating zero -- it burns crawl budget and signals thin content
-- sitewide. This table plus the circuit-breaker columns on mse_products
-- give Phase 4's surface generator a real, checkable "should I keep
-- generating for this product" signal.
--
-- Additive only (CREATE TABLE / CREATE INDEX / ALTER ... ADD COLUMN with
-- a default, no existing column touched) -- matches tonight's guard that
-- nothing alters a live table's existing shape. mse_products was just
-- created by Phase 0 (migration 029) in this same overnight run; adding
-- columns to it here is safe since nothing has read/written it yet
-- outside that same run.

CREATE TABLE IF NOT EXISTS mse_indexation (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id     uuid NOT NULL REFERENCES mse_products(id) ON DELETE CASCADE,
  url            text NOT NULL,
  surface_slug   text,
  indexed        boolean,
  coverage_state text,                   -- GSC verdict string
  impressions    int DEFAULT 0,
  clicks         int DEFAULT 0,
  avg_position   numeric,
  first_seen_at  timestamptz NOT NULL DEFAULT now(),
  checked_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (product_id, url)
);

ALTER TABLE mse_indexation ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_indexation_service_all ON mse_indexation
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY mse_indexation_authenticated_read ON mse_indexation
  FOR SELECT TO authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_mse_indexation_product ON mse_indexation(product_id);
CREATE INDEX IF NOT EXISTS idx_mse_indexation_stale
  ON mse_indexation(product_id, first_seen_at)
  WHERE indexed = false;

-- Circuit breaker: Phase 4's DIST-S1 planner reads this before emitting a
-- surface plan for a product. Three or more 21-day-stale not-indexed
-- pages in one product flips this true; a human reviewing and clearing it
-- (application-level, not enforced here) is what turns it back off.
-- Deliberately a separate new table rather than ALTER-ing mse_products
-- (even though that table was only just created earlier tonight by
-- Phase 0) -- tonight's guard is "additive only, no ALTER on any existing
-- table," and by the time this migration runs mse_products already is
-- one. A second table costs nothing and keeps the guard literal, not
-- just spiritually honored.
CREATE TABLE IF NOT EXISTS mse_generator_state (
  product_id     uuid PRIMARY KEY REFERENCES mse_products(id) ON DELETE CASCADE,
  paused         boolean NOT NULL DEFAULT false,
  paused_reason  text,
  paused_at      timestamptz
);

ALTER TABLE mse_generator_state ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_generator_state_service_all ON mse_generator_state
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY mse_generator_state_authenticated_read ON mse_generator_state
  FOR SELECT TO authenticated USING (true);
