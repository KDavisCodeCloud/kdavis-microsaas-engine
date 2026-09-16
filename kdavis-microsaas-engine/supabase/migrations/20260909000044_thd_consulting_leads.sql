-- Migration 044 — THD Consulting lead scout (agents/marketing/thd_lead_scout.py).
--
-- THD Consulting (Kelvin's own IT-security-implementation service line) is
-- NOT an MSE product — it has no row in mse_products, no campaign_builds
-- entry, no Stripe product in the MSE account. Deliberately NOT reusing
-- mse_leads/mse_icp_configs (supabase/migrations/20260814000024_lead_finder.sql):
-- those tables' product_id is a UUID FK'd into the MSE campaign-orchestration
-- system (mse_dm_sequences, campaign_builds.lead_finder_status, etc.) that
-- THD Consulting has no business joining. product_id here is TEXT, fixed to
-- the literal 'thd_consulting' — a namespace label, not a real product FK,
-- matching CLAUDE.md's "tenant_id or product_id on every new table" rule
-- without pretending this belongs to the MSE factory's product graph.
--
-- Sourcing is Google Custom Search (scrapers/google_search.py, official API,
-- shares the same GOOGLE_CSE_API_KEY / 100-queries-day free-tier cap as MSE
-- lead-finder runs) plus scrapers/verticals/trades.py for the construction
-- industry bucket, plus a new scrapers/company_signals.py fetch of each
-- candidate's own public site. NO LinkedIn/Indeed/Glassdoor/Yelp/Clutch
-- scraping and NO Apollo/Hunter integration — matches this repo's existing,
-- explicit "no paid third-party lead API, no LinkedIn scraping" rule
-- (agents/marketing/mkt_lead_finder.py's own module docstring) and
-- kdavis-agentic-platform/api/routes/outreach.py's compliance boundary.

CREATE TABLE IF NOT EXISTS thd_consulting_leads (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id              TEXT NOT NULL DEFAULT 'thd_consulting',
  company_name            TEXT NOT NULL,
  domain                  TEXT NOT NULL,
  industry                TEXT NOT NULL,
  employee_count_estimate TEXT,
  location                TEXT,
  website                 TEXT,
  contact_name            TEXT,
  contact_title           TEXT,
  contact_email           TEXT,
  contact_email_status    TEXT NOT NULL DEFAULT 'unverified'
                            CHECK (contact_email_status IN ('verified', 'unverified', 'catch_all', 'invalid', 'bounced')),
  contact_linkedin        TEXT,
  contact_phone           TEXT,
  signal_score            INTEGER NOT NULL CHECK (signal_score BETWEEN 1 AND 10),
  signal_breakdown        JSONB NOT NULL DEFAULT '{}',
  source                  TEXT NOT NULL CHECK (source IN ('google_search', 'trades_db')),
  status                  TEXT NOT NULL DEFAULT 'new'
                            CHECK (status IN ('new', 'contacted', 'responded', 'qualified', 'closed')),
  -- Only meaningful once status = 'closed' — the "toggle" the CEO Decoded
  -- Kanban spec asks for on the Closed column. NULL while still open.
  outcome                 TEXT CHECK (outcome IN ('won', 'lost')),
  notes                   TEXT,
  tenant_id               UUID,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Dedup at the company level (one row per company, not per contact) — same
-- belt-and-suspenders precedent as mse_leads.linkedin_url/email: app-level
-- check in thd_lead_scout.py plus a real constraint.
CREATE UNIQUE INDEX IF NOT EXISTS idx_thd_consulting_leads_domain ON thd_consulting_leads(domain);
CREATE INDEX IF NOT EXISTS idx_thd_consulting_leads_status_score ON thd_consulting_leads(status, signal_score DESC);
CREATE INDEX IF NOT EXISTS idx_thd_consulting_leads_industry ON thd_consulting_leads(industry);

ALTER TABLE thd_consulting_leads ENABLE ROW LEVEL SECURITY;

-- Same convention as mse_leads/mse_icp_configs: service-role backend calls
-- (core/supabase_client.py's get_supabase()) bypass RLS entirely, so the
-- only policy needed is for the CEO Decoded dashboard's own authenticated
-- admin session — rule 9 (CLAUDE.md): 'admin' is the only real role value.
CREATE POLICY thd_consulting_leads_admin_access ON thd_consulting_leads
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');


CREATE TABLE IF NOT EXISTS thd_consulting_scrape_runs (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id         TEXT NOT NULL DEFAULT 'thd_consulting',
  filters            JSONB NOT NULL DEFAULT '{}',
  started_at         TIMESTAMPTZ DEFAULT NOW(),
  completed_at       TIMESTAMPTZ,
  candidates_found   INTEGER DEFAULT 0,
  leads_qualified    INTEGER DEFAULT 0,
  leads_deduplicated INTEGER DEFAULT 0,
  sources_used       JSONB DEFAULT '[]',
  status             TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'complete', 'failed')),
  error_message      TEXT,
  tenant_id          UUID
);
CREATE INDEX IF NOT EXISTS idx_thd_consulting_scrape_runs_started ON thd_consulting_scrape_runs(started_at DESC);

ALTER TABLE thd_consulting_scrape_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY thd_consulting_scrape_runs_admin_access ON thd_consulting_scrape_runs
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
