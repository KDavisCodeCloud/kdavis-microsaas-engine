-- Migration 051: mse_email_sequences traceability columns.
--
-- Added for the 2026-09-21 email-campaign-system build (Cloud Decoded's
-- sibling build locked the same requirement: every generated marketing
-- claim must be traceable to a source, never invented). This repo's real
-- lifecycle-email table is mse_email_sequences (migration 007) -- there is
-- no separate "campaign generator" module yet and no mse_positioning ->
-- campaign_builds FK to gate on (see the 2026-09-21 session's GAPS entry
-- for why that gate was NOT wired this session), so this migration only
-- adds the columns needed to record provenance on whatever gets generated
-- going forward. Additive, idempotent, no backfill of the 2 existing
-- pending_hitl rows (both pre-date mse_positioning and have no resolvable
-- source to backfill).

ALTER TABLE mse_email_sequences
  ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'generated'
    CHECK (origin IN ('generated', 'adapted_from_script'));

-- Adds a 'retired' terminal status for the new CEO-dashboard-facing
-- approval API (api/routers/marketing_internal.py) -- the original
-- migration 007 CHECK constraint had no way to take a sequence out of
-- rotation short of leaving it 'pending_hitl' forever or force-marking it
-- 'failed' (semantically wrong -- retiring isn't a failure). Postgres
-- has no ALTER CONSTRAINT for CHECK, so drop-and-recreate under the
-- original constraint's auto-generated name.
ALTER TABLE mse_email_sequences DROP CONSTRAINT IF EXISTS mse_email_sequences_status_check;
ALTER TABLE mse_email_sequences
  ADD CONSTRAINT mse_email_sequences_status_check
  CHECK (status IN ('pending_hitl', 'loaded_unactivated', 'activated', 'failed', 'retired'));

ALTER TABLE mse_email_sequences
  ADD COLUMN IF NOT EXISTS source_script TEXT;

ALTER TABLE mse_email_sequences
  ADD COLUMN IF NOT EXISTS grounding_sources JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN mse_email_sequences.grounding_sources IS
  'Array of {claim, source_type, source_id} -- every factual claim in the '
  'generated emails.jsonb blob should map to an approved mse_positioning '
  'row or an mse_research_reports row. Not enforced as a DB constraint '
  '(the shape of emails.jsonb varies too much to check structurally) -- '
  'application-layer discipline, same trust level as this table''s '
  'existing emails column.';
