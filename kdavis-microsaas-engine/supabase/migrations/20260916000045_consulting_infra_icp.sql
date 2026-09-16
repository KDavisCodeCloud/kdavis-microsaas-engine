-- Migration 045 — Cloud/AI infrastructure consulting ICP + cold outreach
-- for THD Agentic Systems Consulting (mse_products.slug = 'thdagentic-consulting').
--
-- Distinct from thd_consulting_leads (migration 044, Kelvin's separate SMB
-- IT-security-implementation service line, "thd_consulting" text label,
-- owner/office-manager contacts) -- this is a SECOND, higher-tier ICP for
-- the SAME "THD Consulting" business, targeting CTOs/VPs Eng at funded
-- tech startups for cloud/AI infrastructure work. Kelvin's explicit call
-- (2026-09-16): do not touch or weaken the existing SMB security-hygiene
-- ICP/pipeline; this rides the EXISTING mse_leads -> MKT-O2 -> MKT-09 HITL
-- -> MKT-O5 machinery instead (via mse_products.slug='thdagentic-consulting',
-- a real product row that already existed with no mse_icp_configs row yet
-- -- see below), since Kelvin named MKT-O2 and mse_leads specifically for
-- this ICP's DM sequence and lead source.
--
-- 'job_posting_signal' sourcing is Google Custom Search only (same ToS-safe
-- pattern as scrapers/google_search.py's existing docstring) -- explicitly
-- NOT LinkedIn Jobs or Indeed scraping, per Kelvin's own confirmation not to
-- override the existing no-scraping compliance rule
-- (agents/marketing/mkt_lead_finder.py / thd_lead_scout.py's module
-- docstrings, and kdavis-agentic-platform's api/routes/outreach.py
-- compliance boundary).

-- New CHECK values -- 'job_posting_signal' for both tables sharing this
-- vocabulary, same DROP/ADD CONSTRAINT pattern migration 024 already used.
ALTER TABLE mse_leads DROP CONSTRAINT IF EXISTS mse_leads_source_check;
ALTER TABLE mse_leads ADD CONSTRAINT mse_leads_source_check
  CHECK (source IN ('google_search', 'real_estate_db', 'linkedin_manual', 'linkedin_engager', 'job_posting_signal'));

-- Job-posting-specific fields on mse_leads, needed for MKT-O2's
-- "I noticed you're hiring for X" personalization (Task 4's own spec) --
-- nullable, only ever populated for source='job_posting_signal' rows.
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS job_posting_url TEXT;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS job_posting_title TEXT;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS job_posting_date DATE;

ALTER TABLE mse_dm_sequences DROP CONSTRAINT IF EXISTS mse_dm_sequences_lead_source_check;
ALTER TABLE mse_dm_sequences ADD CONSTRAINT mse_dm_sequences_lead_source_check
  CHECK (lead_source IN ('apollo', 'linkedin_manual', 'linkedin_engager', 'lead_finder', 'job_posting_signal'));

-- Third touch -- only the infra-consulting sequence uses this (DM3, sent 5
-- days after DM2 only if no reply); every other lead_source's rows leave
-- this NULL. Nullable rather than a separate table since it's one extra
-- column on an already-per-sequence-row table, not a new one-to-many shape.
ALTER TABLE mse_dm_sequences ADD COLUMN IF NOT EXISTS touch_3 TEXT;
ALTER TABLE mse_dm_sequences ADD COLUMN IF NOT EXISTS touch_3_sent_at TIMESTAMPTZ;

-- Generic company-size + exclusion-criteria columns on mse_icp_configs --
-- no product using this table had a company-size concept before (only
-- thd_consulting_leads' own separate employee_count_estimate did); added
-- here as reusable columns, not infra-consulting-specific ones, since a
-- future product's ICP could need the same shape.
ALTER TABLE mse_icp_configs ADD COLUMN IF NOT EXISTS min_company_size INTEGER;
ALTER TABLE mse_icp_configs ADD COLUMN IF NOT EXISTS max_company_size INTEGER;
ALTER TABLE mse_icp_configs ADD COLUMN IF NOT EXISTS exclude_criteria JSONB NOT NULL DEFAULT '[]';

-- The actual ICP row. mse_products.slug='thdagentic-consulting' already
-- existed (created 2026-08-31) but had no mse_icp_configs row at all yet --
-- an earlier stage-gate migration (20260914221640) tried to set its
-- selling_stage via INSERT...SELECT...WHERE slug=... but no row exists
-- live today (checked directly), so this is a real INSERT, not an update.
-- job_titles/search_templates below are the real search surface Kelvin
-- specified; exclude_criteria captures the non-domain exclusions (company
-- size ceiling is its own column above; "already using Palantir/Databricks
-- at scale" and "no tech presence" aren't mechanically filterable from a
-- search result alone, so they're recorded here for the DM-writing/lead-
-- review step to apply, not silently invented as a search filter that
-- can't actually detect either condition).
INSERT INTO mse_icp_configs (
  product_id, job_titles, locations, industries, search_templates,
  exclude_domains, target_count, min_company_size, max_company_size,
  exclude_criteria, selling_stage
)
SELECT
  id,
  '["CTO", "VP Engineering", "Engineering Director", "Head of Platform", "Founder", "Founder+CTO"]'::jsonb,
  '["United States"]'::jsonb,
  '[]'::jsonb,
  '[
    "\"{title}\" hiring \"cloud architect\" {location}",
    "\"{title}\" hiring \"platform engineer\" {location}",
    "\"{title}\" hiring \"AI infrastructure\" {location}",
    "\"{title}\" hiring \"MLOps engineer\" {location}",
    "\"{title}\" \"cloud migration\" {location}"
  ]'::jsonb,
  '[]'::jsonb,
  100,
  20,
  200,
  '["over_500_employees", "no_tech_presence", "palantir_or_databricks_at_scale"]'::jsonb,
  'active'
FROM mse_products
WHERE slug = 'thdagentic-consulting'
ON CONFLICT (product_id) DO UPDATE SET
  job_titles = EXCLUDED.job_titles,
  search_templates = EXCLUDED.search_templates,
  min_company_size = EXCLUDED.min_company_size,
  max_company_size = EXCLUDED.max_company_size,
  exclude_criteria = EXCLUDED.exclude_criteria,
  selling_stage = EXCLUDED.selling_stage,
  updated_at = NOW();
