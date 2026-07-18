-- Migration 015: Verdict v2.0 RESUBMIT status
--
-- New rule (2026-07-18): when Verdict finds no COMPETITOR_EXISTS flag but
-- the submission fails on a FIXABLE error (price inconsistency between
-- MRR math and tier structure, capture rate applied to the total
-- addressable segment instead of the reachable one, a missing/unvalidated
-- GTM channel, or an MRR floor miss caused by correctable math rather
-- than market reality), Verdict now emits RESUBMIT instead of a flat
-- DO_NOT_BUILD. This needs its own status, distinct from 'rejected' —
-- 'rejected' opportunities get archived and hard-deleted by the dashboard's
-- Reject & Delete button (see api/routers/pipeline.py), which would
-- destroy a genuinely fixable opportunity instead of surfacing the
-- correction needed.
--
-- mrr_floor_check must also exempt 'needs_correction', for the same
-- reason it already exempts 'rejected': an opportunity flagged for a
-- capture-rate or price-inconsistency correction legitimately has a
-- sub-floor conservative_mrr_potential until the correction is applied
-- and it's resubmitted.

ALTER TABLE opportunity_pipeline DROP CONSTRAINT IF EXISTS opportunity_pipeline_status_check;
ALTER TABLE opportunity_pipeline ADD CONSTRAINT opportunity_pipeline_status_check
  CHECK (status = ANY (ARRAY[
    'discovered', 'validated', 'watch', 'rejected', 'needs_correction',
    'READY_TO_BUILD', 'building', 'launched', 'tracking_mrr'
  ]));

ALTER TABLE opportunity_pipeline DROP CONSTRAINT IF EXISTS mrr_floor_check;
ALTER TABLE opportunity_pipeline ADD CONSTRAINT mrr_floor_check
  CHECK ((conservative_mrr_potential >= 4000) OR (status = ANY (ARRAY['rejected', 'needs_correction'])));
