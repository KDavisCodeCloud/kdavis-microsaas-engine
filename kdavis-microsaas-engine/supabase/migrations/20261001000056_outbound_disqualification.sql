-- Outbound loop closure (2026-10-01): recordable rejection/disqualification
-- reasons.
--
-- Both halves of the HITL reject path were previously reason-less:
--   * api/routers/outreach.py's reject_dm_sequence set status='rejected_hitl'
--     and nothing else -- so "why was this rejected" was unrecoverable.
--   * mse_leads had no terminal "this lead is not workable" state at all;
--     its status CHECK (migration 20260814000024) allowed only
--     pending_dm / pending_email / contacted / converted / unsubscribed /
--     bounced. A junk lead could only be left sitting at pending_dm
--     forever, where the enrollment engine would keep re-reading it.
--
-- Driven by two real cleanups on 2026-10-01:
--   * 5 pending_hitl sequences drafted BEFORE the job_posting_title fix,
--     whose copy can assert a role the company is not actually hiring for.
--   * 61 lead rows whose title/company/domain/email are all NULL --
--     scraped as bare LinkedIn profile URLs, so they can never be
--     qualified (no title) nor emailed (no domain).

ALTER TABLE mse_dm_sequences
  ADD COLUMN IF NOT EXISTS rejection_reason TEXT;

COMMENT ON COLUMN mse_dm_sequences.rejection_reason IS
  'Why this sequence was rejected at HITL. Set alongside status=''rejected_hitl''. NULL for every non-rejected row.';

ALTER TABLE mse_leads
  ADD COLUMN IF NOT EXISTS disqualified_reason TEXT;

COMMENT ON COLUMN mse_leads.disqualified_reason IS
  'Why this lead is permanently not workable, e.g. ''no_structured_data'' (no title/company/domain/email captured) or ''aggregator_domain'' (company resolved to a job board). Set alongside status=''disqualified''.';

-- Widen the status CHECK to admit the new terminal state. Drop-then-add
-- because the original constraint is unnamed-by-convention in migration
-- 20260814000024; this names it so future migrations can target it.
ALTER TABLE mse_leads DROP CONSTRAINT IF EXISTS mse_leads_status_check;
ALTER TABLE mse_leads ADD CONSTRAINT mse_leads_status_check
  CHECK (status IN (
    'pending_dm', 'pending_email', 'contacted', 'converted',
    'unsubscribed', 'bounced', 'disqualified'
  ));

CREATE INDEX IF NOT EXISTS idx_mse_leads_disqualified
  ON mse_leads (status) WHERE status = 'disqualified';
