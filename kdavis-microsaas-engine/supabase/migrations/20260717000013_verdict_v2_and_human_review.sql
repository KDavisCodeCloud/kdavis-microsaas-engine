-- Migration 013: Verdict Agent v2.0 support + human review/tuning loop
--
-- Adds storage for the full v2.0 structured research output (competitor
-- findings, comp set, TAM funnel, MRR math breakdown) — the old
-- deterministic gate-checker never stored anything beyond a status and a
-- one-line rejection_reason, so none of its reasoning was ever
-- recoverable for review.
--
-- Also adds a human review layer that is DISTINCT from the agent's own
-- verdict/status: Kelvin approves/rejects/comments on each opportunity
-- from the dashboard, independent of what the agent decided. Comparing
-- human_review_status against the agent's own status is the actual
-- tuning signal for prompt v2.1+ — conflating the two into one field
-- would destroy that signal.

ALTER TABLE opportunity_pipeline
  ADD COLUMN IF NOT EXISTS verdict_v2_output jsonb,
  ADD COLUMN IF NOT EXISTS human_review_status text NOT NULL DEFAULT 'pending'
    CHECK (human_review_status IN ('pending', 'approved', 'rejected')),
  ADD COLUMN IF NOT EXISTS human_review_comment text,
  ADD COLUMN IF NOT EXISTS human_reviewed_by text,
  ADD COLUMN IF NOT EXISTS human_reviewed_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_opportunity_pipeline_human_review ON opportunity_pipeline(human_review_status);
