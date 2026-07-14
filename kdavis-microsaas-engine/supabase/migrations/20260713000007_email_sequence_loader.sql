-- Migration 007: Email sequence loader — trial-nurture sequences drafted by
-- MKT-O3 and loaded into systeme.io unactivated, pending HITL approval.
--
-- Same category as mse_apollo_leads/mse_dm_sequences (20260709000006):
-- internal factory tooling, admin-access RLS precedent, tenant_id nullable
-- per CLAUDE.md's blanket rule but not used as the RLS key.

CREATE TABLE IF NOT EXISTS mse_email_sequences (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id           UUID NOT NULL,
  campaign_build_id    UUID NOT NULL REFERENCES campaign_builds(id),
  systeme_sequence_id  TEXT,
  emails               JSONB NOT NULL,
  status               TEXT DEFAULT 'pending_hitl'
                         CHECK (status IN ('pending_hitl', 'loaded_unactivated', 'activated', 'failed')),
  hitl_approved_by     TEXT,
  hitl_approved_at     TIMESTAMPTZ,
  tenant_id            UUID,
  created_at           TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mse_email_sequences_campaign_build ON mse_email_sequences(campaign_build_id);
CREATE INDEX IF NOT EXISTS idx_mse_email_sequences_product ON mse_email_sequences(product_id);

ALTER TABLE mse_email_sequences ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_email_sequences_admin_access ON mse_email_sequences
  USING (current_setting('app.role', true) = 'admin');
