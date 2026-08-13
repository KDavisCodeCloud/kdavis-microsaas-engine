-- Migration 022: mse_social_content review workflow + RLS bug fix.
--
-- MKT-V1's Reddit/Facebook output had no review state at all (no status
-- column) and nothing downstream ever read the table -- content dead-ended
-- invisible in Postgres. This adds the same pending_review -> approved/
-- rejected -> sent workflow linkedin_content_queue already has
-- (kdavis-agentic-platform/ceo-dashboard/db/migrations/007_marketing_queues.sql),
-- surfaced in ceo-dashboard's Marketing & Sales tab.
--
-- Also fixes a real bug found while doing this: this table's RLS policy
-- used current_setting('app.role', true) = 'admin' -- the exact broken
-- GUC-based pattern migration 20260716000009_fix_admin_rls_claim.sql
-- fixed for every other admin-access table in this schema. This table
-- was created 2026-08-12, three weeks after that fix, and copy-pasted the
-- already-fixed bug back in. Never actually blocked anything in practice
-- (every real reader/writer so far has used the service-role client,
-- which bypasses RLS entirely), but it was silently wrong for any future
-- JWT-based admin read, exactly as 009 already explained.

ALTER TABLE mse_social_content
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending_review'
    CHECK (status IN ('pending_review', 'approved', 'rejected', 'sent')),
  ADD COLUMN IF NOT EXISTS hitl_notes TEXT,
  ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_mse_social_content_status ON mse_social_content(status, created_at DESC);

ALTER POLICY mse_social_content_admin_access ON mse_social_content
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
