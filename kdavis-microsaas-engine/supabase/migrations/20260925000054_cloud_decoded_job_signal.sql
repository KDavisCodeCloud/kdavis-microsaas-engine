-- Migration 054: Cloud Decoded job-signal ICP -- a second job-posting-
-- signal branch alongside the existing infra-consulting one (migration
-- 045), routed by title (architect/design/contract -> consulting;
-- ongoing-ops -> cloud-decoded; ambiguous/both -> consulting -- Kelvin's
-- own routing spec, 2026-09-25). Rides the SAME mse_leads -> MKT-O2 ->
-- HITL machinery as every other lead_source, distinguished by a new
-- source/lead_source value rather than a new table -- same reasoning
-- migration 045 already used for job_posting_signal itself.
--
-- Does NOT touch the existing 'job_posting_signal' rows, queries, or
-- mse_icp_configs row for thdagentic-consulting -- purely additive.
--
-- Sourcing is Brave Search (scrapers/brave_search.py), same as every
-- other lead-sourcing consumer since the 2026-09-18 switch -- the
-- original infra-consulting task spec said "Google Custom Search" but
-- that source was replaced platform-wide per Kelvin's own explicit
-- instruction that day ("one Brave key for all lead sourcing, no
-- exceptions"); this branch follows the same rule, not the stale wording.

ALTER TABLE mse_leads DROP CONSTRAINT IF EXISTS mse_leads_source_check;
ALTER TABLE mse_leads ADD CONSTRAINT mse_leads_source_check
  CHECK (source IN ('google_search', 'brave_search', 'real_estate_db', 'linkedin_manual', 'linkedin_engager', 'job_posting_signal', 'cloud_decoded_job_signal'));

ALTER TABLE mse_dm_sequences DROP CONSTRAINT IF EXISTS mse_dm_sequences_lead_source_check;
ALTER TABLE mse_dm_sequences ADD CONSTRAINT mse_dm_sequences_lead_source_check
  CHECK (lead_source IN ('apollo', 'linkedin_manual', 'linkedin_engager', 'lead_finder', 'job_posting_signal', 'cloud_decoded_job_signal'));

-- New capture fields -- Kelvin's spec: "Capture the JD text — extract
-- stack keywords ... into the lead row for personalization." Generic
-- columns (not cloud-decoded-specific names), same idiom as
-- job_posting_url/title/date on the same table: nullable, only ever
-- populated for job-posting-signal-shaped sources. Only
-- cloud_decoded_job_signal populates them today; job_posting_signal
-- (consulting) is deliberately left untouched -- "do not touch the
-- consulting branch's queries or sequence."
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS job_posting_description TEXT;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS job_posting_stack_keywords JSONB NOT NULL DEFAULT '[]';
