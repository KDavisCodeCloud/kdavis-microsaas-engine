-- Migration 006: Outreach engine — Apollo leads, cold DM sequences
--
-- Same category as audit_log/mse_research_reports/campaign_builds
-- (20260709000005_marketing_engine.sql): internal factory tooling, no
-- end-customer tenant of the marketing engine itself, so RLS follows the
-- same admin-access precedent (current_setting('app.role')) rather than
-- the auth.uid()-per-customer pattern. tenant_id columns are still
-- included (nullable) per CLAUDE.md's blanket "tenant_id on every table"
-- rule, but are not used as the RLS key here — matches campaign_builds.

CREATE TABLE IF NOT EXISTS mse_apollo_leads (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_build_id  UUID NOT NULL REFERENCES campaign_builds(id),
  product_id         UUID NOT NULL,
  first_name         TEXT,
  last_name          TEXT,
  email              TEXT,
  company            TEXT,
  title              TEXT,
  linkedin_url       TEXT,
  apollo_id          TEXT,
  status             TEXT DEFAULT 'pending'
                       CHECK (status IN ('pending', 'dm_sent', 'replied', 'converted')),
  tenant_id          UUID,
  created_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mse_apollo_leads_campaign_build ON mse_apollo_leads(campaign_build_id);
CREATE INDEX IF NOT EXISTS idx_mse_apollo_leads_product ON mse_apollo_leads(product_id);

ALTER TABLE mse_apollo_leads ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_apollo_leads_admin_access ON mse_apollo_leads
  USING (current_setting('app.role', true) = 'admin');


CREATE TABLE IF NOT EXISTS mse_dm_sequences (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id            UUID NOT NULL REFERENCES mse_apollo_leads(id),
  product_id         UUID NOT NULL,
  campaign_build_id  UUID NOT NULL REFERENCES campaign_builds(id),
  touch_1            TEXT NOT NULL,
  touch_2            TEXT NOT NULL,
  status             TEXT DEFAULT 'pending_hitl',
  hitl_approved_by   TEXT,
  hitl_approved_at   TIMESTAMPTZ,
  tenant_id          UUID,
  created_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mse_dm_sequences_lead ON mse_dm_sequences(lead_id);
CREATE INDEX IF NOT EXISTS idx_mse_dm_sequences_campaign_build ON mse_dm_sequences(campaign_build_id);

ALTER TABLE mse_dm_sequences ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_dm_sequences_admin_access ON mse_dm_sequences
  USING (current_setting('app.role', true) = 'admin');
