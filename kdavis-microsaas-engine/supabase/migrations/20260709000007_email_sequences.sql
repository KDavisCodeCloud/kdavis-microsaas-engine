-- Migration 007: Email sequences (MKT-O3 output)
--
-- Same category as mse_apollo_leads/mse_dm_sequences
-- (20260709000006_outreach_engine.sql): internal factory tooling, admin
-- RLS via current_setting('app.role') rather than auth.uid()-per-customer.
-- tenant_id included (nullable) per CLAUDE.md's blanket rule, not used as
-- the RLS key here — matches campaign_builds/mse_dm_sequences precedent.
--
-- One row per email in the sequence (not per-lead like mse_dm_sequences —
-- email is a bulk drip sequence sent to the product's list via merge tags,
-- not a 1:1 personalized DM). Never sent by this agent: every row lands
-- with status='pending_hitl', same gate as mse_dm_sequences. A human
-- approves in the HITL queue; approval unlocks the separate sender, which
-- doesn't exist yet (same precedent as MKT-O2).

CREATE TABLE IF NOT EXISTS mse_email_sequences (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id         UUID NOT NULL,
  campaign_build_id  UUID NOT NULL REFERENCES campaign_builds(id),
  sequence_order     INT NOT NULL,
  send_offset_days   INT NOT NULL DEFAULT 0,
  subject            TEXT NOT NULL,
  body               TEXT NOT NULL,
  status             TEXT DEFAULT 'pending_hitl',
  hitl_approved_by   TEXT,
  hitl_approved_at   TIMESTAMPTZ,
  tenant_id          UUID,
  created_at         TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (campaign_build_id, sequence_order)
);
CREATE INDEX IF NOT EXISTS idx_mse_email_sequences_campaign_build ON mse_email_sequences(campaign_build_id);
CREATE INDEX IF NOT EXISTS idx_mse_email_sequences_product ON mse_email_sequences(product_id);

ALTER TABLE mse_email_sequences ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_email_sequences_admin_access ON mse_email_sequences
  USING (current_setting('app.role', true) = 'admin');
