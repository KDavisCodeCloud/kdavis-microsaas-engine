-- Migration 014: opportunity rejection archive
--
-- Kelvin's reject button deletes the opportunity from opportunity_pipeline
-- (2026-07-17 request — the dashboard should not keep showing rejected
-- clutter). Deleting outright would destroy the Verdict v2.0 tuning
-- signal (verdict_v2_output, the agent's full research reasoning, plus
-- Kelvin's own rejection comment) the whole human-review loop exists to
-- produce. Snapshot the full row here before it's deleted, instead of a
-- soft-delete flag on opportunity_pipeline itself — the point is for the
-- opportunity to actually be gone from the working table Kelvin looks at,
-- not merely hidden by a filter.
--
-- mse_build_briefs.opportunity_id is ON DELETE SET NULL (migration 011),
-- so a brief already generated for a since-deleted opportunity is
-- unaffected — it stores its own product_name/slug/content independently.

CREATE TABLE IF NOT EXISTS opportunity_pipeline_rejections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  archived_at timestamptz NOT NULL DEFAULT now(),
  original_opportunity jsonb NOT NULL,
  rejected_by text,
  rejection_comment text
);

ALTER TABLE opportunity_pipeline_rejections ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can read rejection archive" ON opportunity_pipeline_rejections;
CREATE POLICY "Authenticated users can read rejection archive"
  ON opportunity_pipeline_rejections FOR SELECT
  TO authenticated
  USING (true);

DROP POLICY IF EXISTS "Service role can manage rejection archive" ON opportunity_pipeline_rejections;
CREATE POLICY "Service role can manage rejection archive"
  ON opportunity_pipeline_rejections FOR ALL
  TO service_role
  USING (true);
