-- Migration 028: mse_build_briefs dispatch columns
-- NOVA gap-closure Phase D1 (2026-08-29). Adds the storage D1 needs for
-- "NOVA dispatches a brief to Claude, reports the result" -- new columns
-- on mse_build_briefs itself rather than a new join table, matching the
-- existing column-based state pattern already on this table
-- (activated_monitoring/monitoring_trigger/etc, migration 011): briefs
-- are one-shot per opportunity today, so a new table would be overhead
-- without a concrete need for it yet.

ALTER TABLE mse_build_briefs
  ADD COLUMN IF NOT EXISTS dispatch_status text NOT NULL DEFAULT 'not_dispatched'
    CHECK (dispatch_status IN ('not_dispatched', 'dispatched', 'failed')),
  ADD COLUMN IF NOT EXISTS dispatched_at timestamptz,
  ADD COLUMN IF NOT EXISTS dispatch_result jsonb,
  ADD COLUMN IF NOT EXISTS dispatched_by text;

CREATE INDEX IF NOT EXISTS idx_build_briefs_dispatch_status ON mse_build_briefs(dispatch_status);
