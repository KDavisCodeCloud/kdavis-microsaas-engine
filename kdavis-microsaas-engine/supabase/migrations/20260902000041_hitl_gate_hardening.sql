-- Migration 041: HITL gate hardening (MKT-O5 sequence sender security audit,
-- 2026-09-02). Closes Finding 1 (double-send risk -- send and status update
-- were non-atomic, no guard against concurrent/overlapping sender runs) and
-- Finding 2 (no approval TTL -- an approved sequence could sit indefinitely
-- and fire whenever the hourly job next ran, with zero freshness check).

-- Finding 1: two new transient "claiming" statuses. The sender atomically
-- claims a row (UPDATE ... WHERE status='approved_hitl') before calling
-- Resend; a failed claim (zero rows affected) means a concurrent run
-- already has it, so this run skips rather than double-sending. A send
-- failure after a successful claim reverts the row back to its prior
-- approved status so it's retried on a later run rather than stuck.
-- Finding 2: hitl_approved_expires_at is set alongside hitl_approved_at
-- when a sequence is approved (7 days, see api/routers/outreach.py). The
-- sender voids -- not silently skips, not re-queues -- any approved_hitl
-- row whose expiry has passed, transitioning it to 'approval_expired', a
-- real, visible terminal status distinct from 'rejected_hitl' (a human
-- said no) and 'suppressed' (the lead opted out): this one means nobody
-- acted in time, and it needs fresh review, not silent auto-recycling.

ALTER TABLE mse_dm_sequences
  ADD COLUMN IF NOT EXISTS hitl_approved_expires_at TIMESTAMPTZ;

ALTER TABLE mse_dm_sequences DROP CONSTRAINT IF EXISTS mse_dm_sequences_status_check;
ALTER TABLE mse_dm_sequences ADD CONSTRAINT mse_dm_sequences_status_check
  CHECK (status IN (
    'pending_hitl', 'approved_hitl', 'approved_manual', 'rejected_hitl',
    'touch_1_sending', 'touch_1_sent', 'touch_2_sending', 'sequence_complete',
    'suppressed', 'approval_expired'
  ));
