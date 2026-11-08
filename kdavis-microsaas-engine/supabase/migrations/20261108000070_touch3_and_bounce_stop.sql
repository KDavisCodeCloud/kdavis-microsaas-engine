-- 070: touch_3 sending and bounce stop (Kelvin, 2026-11-07 brief, Phase 6).
--
-- Widens the mse_dm_sequences status CHECK only; no data changes.
--   touch_2_sent     touch_2 went out and a touch_3 is still due (day 7)
--   touch_3_sending  claim state while touch_3 is in flight
--   bounced          a Resend bounce event stopped the sequence
-- Before this, touch_2 moved every sequence to sequence_complete, so the
-- approved touch_3 was never sent.

ALTER TABLE mse_dm_sequences DROP CONSTRAINT IF EXISTS mse_dm_sequences_status_check;
ALTER TABLE mse_dm_sequences ADD CONSTRAINT mse_dm_sequences_status_check CHECK (status = ANY (ARRAY[
  'awaiting_contact', 'pending_hitl', 'approved_hitl', 'approved_manual', 'rejected_hitl',
  'touch_1_sending', 'touch_1_sent', 'touch_2_sending', 'touch_2_sent', 'touch_3_sending',
  'sequence_complete', 'replied', 'suppressed', 'bounced', 'approval_expired', 'archived_wrong_icp']));
