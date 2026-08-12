-- Migration 020: CAN-SPAM compliance guard.
--
-- MKT-O5 (agents/marketing/mkt_o5_sequence_sender.py) sends real commercial
-- email via Resend with no unsubscribe mechanism and no suppression check --
-- flagged in the 2026-08-12 marketing infrastructure audit as a real legal
-- gap, not just a build-status one: CAN-SPAM requires a working opt-out in
-- every commercial email and requires honoring it. This table is the
-- suppression list core/email_compliance.py checks before every send.
--
-- Global across every MSE product, not scoped to one product_id: someone
-- who unsubscribes from cold outreach almost certainly means "stop
-- contacting me," not "stop only for this one product" -- treating it as
-- global is the more conservative, defensible reading either way, and
-- matches how a real recipient understands the word "unsubscribe."

CREATE TABLE IF NOT EXISTS mse_email_suppressions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email          TEXT NOT NULL UNIQUE,
  reason         TEXT NOT NULL DEFAULT 'unsubscribed' CHECK (reason IN ('unsubscribed', 'bounced', 'complained', 'manual')),
  suppressed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mse_email_suppressions_email ON mse_email_suppressions(email);

ALTER TABLE mse_email_suppressions ENABLE ROW LEVEL SECURITY;

-- Service-role only -- checked/written exclusively by MKT-O5 and the
-- public unsubscribe endpoint (both use get_supabase(), never a tenant
-- JWT), same access pattern as audit_log in migration 005.
CREATE POLICY mse_email_suppressions_admin_access ON mse_email_suppressions
  USING (current_setting('app.role', true) = 'admin');
