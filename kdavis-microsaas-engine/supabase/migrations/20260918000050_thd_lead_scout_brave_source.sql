-- Migration 050: THD Lead Scout (agents/marketing/thd_lead_scout.py) also
-- switches from Google Custom Search to Brave Search -- same reason as
-- migration 049 (mkt_lead_finder.py): Google discontinued "Search the
-- entire web" for newly-created Programmable Search Engines on
-- 2026-01-20. Kelvin's explicit instruction (2026-09-18): one Brave key
-- for all lead sourcing, no exceptions.
--
-- 'google_search' is kept in the CHECK, not replaced -- historical rows
-- already carry it.

ALTER TABLE thd_consulting_leads DROP CONSTRAINT IF EXISTS thd_consulting_leads_source_check;
ALTER TABLE thd_consulting_leads ADD CONSTRAINT thd_consulting_leads_source_check
  CHECK (source IN ('google_search', 'brave_search', 'trades_db'));
