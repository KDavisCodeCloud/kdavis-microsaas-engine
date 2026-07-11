-- Migration 005: Marketing engine — audit_log, research reports, campaign builds
--
-- These are internal factory tooling tables — same category as
-- opportunity_pipeline (see 20260703000002_opportunity_pipeline.sql), not
-- per-customer tenant data. There is no end-customer "tenant" of the
-- marketing engine itself, so RLS here follows opportunity_pipeline's
-- existing admin-access precedent (current_setting('app.role')) rather
-- than the auth.uid()-per-customer pattern in 20260703000003_rls_auth_uid.sql.
-- tenant_id columns are still included (nullable) per CLAUDE.md's blanket
-- "tenant_id on every table" rule and the task spec's campaign_builds shape,
-- but are not used as the RLS key here.

CREATE TABLE audit_log (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id    TEXT NOT NULL,
  action      TEXT NOT NULL,
  outcome     TEXT NOT NULL CHECK (outcome IN ('win', 'lose')),
  product_id  UUID,
  tenant_id   UUID,
  metadata    JSONB DEFAULT '{}',
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_audit_log_agent_created ON audit_log(agent_id, created_at DESC);

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY audit_log_admin_access ON audit_log
  USING (current_setting('app.role', true) = 'admin');


CREATE TABLE mse_research_reports (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id   UUID NOT NULL,
  cycle_date   DATE NOT NULL,
  report_json  JSONB NOT NULL,
  tenant_id    UUID,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (product_id, cycle_date)
);
CREATE INDEX idx_mse_research_reports_product ON mse_research_reports(product_id, cycle_date DESC);

ALTER TABLE mse_research_reports ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_research_reports_admin_access ON mse_research_reports
  USING (current_setting('app.role', true) = 'admin');


-- Schema exactly as specified in Terminal 5's task (adds tenant_id vs. the
-- Campaign-Orchestrator-and-Strategy-Specs.md draft, which has
-- campaign_live_at/overall_status instead — this version supersedes that
-- draft for what's actually built).
CREATE TABLE campaign_builds (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id             UUID NOT NULL,
  research_opp_id        UUID NOT NULL,
  triggered_at           TIMESTAMPTZ DEFAULT now(),
  apollo_status          TEXT DEFAULT 'pending',
  dm_sequence_status     TEXT DEFAULT 'pending',
  email_sequence_status  TEXT DEFAULT 'pending',
  seo_factory_status     TEXT DEFAULT 'pending',
  social_status          TEXT DEFAULT 'pending',
  tenant_id              UUID  -- required per CLAUDE.md
);
CREATE INDEX idx_campaign_builds_product ON campaign_builds(product_id);

ALTER TABLE campaign_builds ENABLE ROW LEVEL SECURITY;

CREATE POLICY campaign_builds_admin_access ON campaign_builds
  USING (current_setting('app.role', true) = 'admin');
