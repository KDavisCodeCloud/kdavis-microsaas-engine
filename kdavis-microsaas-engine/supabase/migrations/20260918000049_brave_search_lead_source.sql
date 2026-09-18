-- Migration 049: Brave Search replaces Google Custom Search as the lead
-- finder's open-web Source 1 (scrapers/brave_search.py). Google
-- discontinued "Search the entire web" for newly-created Programmable
-- Search Engines on 2026-01-20 -- new engines are capped at up to 50
-- specific domains, which cannot serve this scraper's open-web queries.
-- See knowledge/sops/devops/2026-09-18-brave-search-replaces-google-cse.md
-- (kdavis-agentic-platform) for the full record.
--
-- 'google_search' is kept in the CHECK, not replaced -- historical rows
-- already carry it, and scrapers/google_search.py itself is kept dormant
-- (not deleted) in case a domain-scoped CSE becomes useful again.

ALTER TABLE mse_leads DROP CONSTRAINT IF EXISTS mse_leads_source_check;
ALTER TABLE mse_leads ADD CONSTRAINT mse_leads_source_check
  CHECK (source IN ('google_search', 'brave_search', 'real_estate_db', 'linkedin_manual', 'linkedin_engager', 'job_posting_signal'));
