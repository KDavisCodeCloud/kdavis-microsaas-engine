-- Migration 024: Self-hosted lead finder — replaces Apollo.io/Hunter.io as
-- the lead source for every MSE product. Apollo's Free plan has no API
-- access (see migration 20260814000023's own comment) and Hunter.io is a
-- paid per-lookup API; this pulls public leads (Google Custom Search
-- results + public professional license/registry databases) and finds +
-- SMTP-verifies their email itself, at zero ongoing cost.
--
-- mse_leads is a NEW table, not a repurposing of mse_apollo_leads: that
-- table's campaign_build_id is NOT NULL/FK'd to a MKT-ORCH campaign run
-- (same reasoning migration 023 already used for mse_linkedin_leads) --
-- lead-finder runs aren't tied to a campaign build either.

CREATE TABLE IF NOT EXISTS mse_leads (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id         UUID NOT NULL,
  first_name         TEXT,
  last_name          TEXT,
  title              TEXT,
  company            TEXT,
  domain             TEXT,
  email              TEXT,
  email_status       TEXT NOT NULL DEFAULT 'unverified'
                       CHECK (email_status IN ('verified', 'unverified', 'catch_all', 'invalid', 'bounced')),
  linkedin_url       TEXT,
  -- linkedin_manual/linkedin_engager are listed here for schema
  -- completeness per the task spec, but mkt_lead_finder.py itself only
  -- ever writes google_search/real_estate_db rows -- LinkedIn-collected
  -- leads still go through mse_linkedin_leads (mkt_li_intake.py),
  -- unchanged. Two separate tables, one shared vocabulary.
  source             TEXT NOT NULL
                       CHECK (source IN ('google_search', 'real_estate_db', 'linkedin_manual', 'linkedin_engager')),
  location           TEXT,
  -- Two-stage lifecycle distinct from mse_apollo_leads/mse_linkedin_leads'
  -- simpler status models: pending_dm (no sequence drafted yet) ->
  -- pending_email (MKT-O2 drafted a sequence, advances the lead here,
  -- awaiting MKT-O5's send) -> contacted -> converted/unsubscribed/bounced.
  status             TEXT NOT NULL DEFAULT 'pending_dm'
                       CHECK (status IN ('pending_dm', 'pending_email', 'contacted', 'converted', 'unsubscribed', 'bounced')),
  confidence_score   NUMERIC,
  notes              TEXT,
  tenant_id          UUID,
  created_at         TIMESTAMPTZ DEFAULT NOW(),
  last_contacted_at  TIMESTAMPTZ
);

-- Dedup backing the "same linkedin_url not added twice" requirement --
-- app-level check in mkt_lead_finder.py (mirroring mkt_li_intake.py's
-- existing pattern) plus a real constraint, same belt-and-suspenders
-- precedent as mse_linkedin_leads.linkedin_url. Partial (WHERE ... IS NOT
-- NULL) since real_estate_db leads often have no linkedin_url at all --
-- a plain UNIQUE index would still allow multiple NULLs in Postgres, but
-- being explicit here documents that on purpose.
CREATE UNIQUE INDEX IF NOT EXISTS idx_mse_leads_linkedin_url ON mse_leads(linkedin_url) WHERE linkedin_url IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mse_leads_email ON mse_leads(email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mse_leads_status_source ON mse_leads(status, source, created_at);
CREATE INDEX IF NOT EXISTS idx_mse_leads_product ON mse_leads(product_id);

ALTER TABLE mse_leads ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_leads_admin_access ON mse_leads
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');


CREATE TABLE IF NOT EXISTS mse_icp_configs (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id         UUID NOT NULL UNIQUE,
  job_titles         JSONB NOT NULL DEFAULT '[]',
  locations          JSONB NOT NULL DEFAULT '[]',
  industries         JSONB NOT NULL DEFAULT '[]',
  vertical           TEXT,
  search_templates   JSONB NOT NULL DEFAULT '[]',
  exclude_domains    JSONB NOT NULL DEFAULT '[]',
  target_count       INTEGER DEFAULT 100,
  tenant_id          UUID,
  created_at         TIMESTAMPTZ DEFAULT NOW(),
  updated_at         TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE mse_icp_configs ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_icp_configs_admin_access ON mse_icp_configs
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');


-- One row per (domain, pattern) -- core/email_patterns.py increments
-- success_count/attempt_count as verified sends accumulate, so the most
-- successful pattern for a domain floats to the top of success_rate over
-- time, without ever paying a per-lookup API for something already known.
CREATE TABLE IF NOT EXISTS mse_email_patterns (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  domain             TEXT NOT NULL,
  pattern            TEXT NOT NULL,
  success_count      INTEGER NOT NULL DEFAULT 0,
  attempt_count      INTEGER NOT NULL DEFAULT 0,
  success_rate       NUMERIC,
  last_verified_at   TIMESTAMPTZ,
  tenant_id          UUID,
  created_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mse_email_patterns_domain_pattern ON mse_email_patterns(domain, pattern);

ALTER TABLE mse_email_patterns ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_email_patterns_admin_access ON mse_email_patterns
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');


CREATE TABLE IF NOT EXISTS mse_lead_finder_runs (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id           UUID NOT NULL,
  started_at           TIMESTAMPTZ DEFAULT NOW(),
  completed_at         TIMESTAMPTZ,
  leads_found          INTEGER DEFAULT 0,
  leads_verified       INTEGER DEFAULT 0,
  leads_deduplicated   INTEGER DEFAULT 0,
  sources_used         JSONB DEFAULT '[]',
  status               TEXT NOT NULL DEFAULT 'running'
                         CHECK (status IN ('running', 'complete', 'failed')),
  error_message        TEXT,
  tenant_id            UUID
);
CREATE INDEX IF NOT EXISTS idx_mse_lead_finder_runs_product ON mse_lead_finder_runs(product_id, started_at DESC);

ALTER TABLE mse_lead_finder_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_lead_finder_runs_admin_access ON mse_lead_finder_runs
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');


-- MKT-ORCH's own per-agent status tracking (apollo_status/dm_sequence_status/
-- etc., migration 20260709000005) gets a new column rather than reusing
-- apollo_status -- a Google/license-database scraper writing to a column
-- literally named "apollo_status" would be permanently misleading in the
-- data, and apollo_status stays available untouched for if MKT-O1 is ever
-- explicitly re-enabled (see mkt_orch_campaign_orchestrator.py's override).
ALTER TABLE campaign_builds ADD COLUMN IF NOT EXISTS lead_finder_status TEXT DEFAULT 'pending';


-- mse_dm_sequences (generalized once already in migration 023 for
-- LinkedIn) needs a third lead-reference shape for lead_finder-sourced
-- sequences.
ALTER TABLE mse_dm_sequences DROP CONSTRAINT IF EXISTS mse_dm_sequences_lead_source_check;
ALTER TABLE mse_dm_sequences ADD CONSTRAINT mse_dm_sequences_lead_source_check
  CHECK (lead_source IN ('apollo', 'linkedin_manual', 'linkedin_engager', 'lead_finder'));

ALTER TABLE mse_dm_sequences ADD COLUMN IF NOT EXISTS lead_finder_lead_id UUID REFERENCES mse_leads(id);

ALTER TABLE mse_dm_sequences DROP CONSTRAINT IF EXISTS mse_dm_sequences_one_lead_ref;
ALTER TABLE mse_dm_sequences ADD CONSTRAINT mse_dm_sequences_one_lead_ref CHECK (
  (lead_id IS NOT NULL)::int + (linkedin_lead_id IS NOT NULL)::int + (lead_finder_lead_id IS NOT NULL)::int = 1
);

CREATE INDEX IF NOT EXISTS idx_mse_dm_sequences_lead_finder_lead ON mse_dm_sequences(lead_finder_lead_id);
