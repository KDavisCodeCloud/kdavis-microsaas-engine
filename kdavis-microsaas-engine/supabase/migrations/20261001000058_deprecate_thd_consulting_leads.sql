-- Formal deprecation of thd_consulting_leads (2026-10-01, Kelvin:
-- "Deprecate thd_consulting_leads formally. Approved.").
--
-- WHY. It was a second, parallel lead model for the same business:
-- its own table, its own 1-10 signal_score, its own scrape-run table and
-- its own API router -- while consulting leads from the job-signal path
-- land in mse_leads with product_id = the thdagentic-consulting product
-- and source = 'job_posting_signal'. Two lead tables for one pipeline
-- means two HITL paths, two dedup rules and two definitions of
-- "qualified", which is exactly how the outbound loop ended up with
-- leads nobody could account for.
--
-- Scraper v2 (migration 20261001000057, agents/marketing/
-- company_first_sourcing.py) makes the duplication pointless: fit_score /
-- intent_score / score_reasons on mse_leads now carry the "does this
-- company need our service" dimension that was thd_consulting_leads'
-- only real reason to exist as a separate model.
--
-- NOT DROPPED, DELIBERATELY. Verified against microsaas-prod on
-- 2026-10-01 before writing this: thd_consulting_leads = 0 rows,
-- thd_consulting_scrape_runs = 0 rows -- agents/marketing/
-- thd_lead_scout.py's run_lead_scout has never once run in production.
-- So there is no data to migrate and nothing to lose. The tables stay
-- anyway because api/routers/thd_consulting.py and the CEO dashboard's
-- consulting page still SELECT from them; dropping the tables would 500
-- a live route for no benefit. The deprecation is enforced at the write
-- path (thd_lead_scout raises unless explicitly overridden), not by
-- removing the read surface.
--
-- Successor, for anyone reading this later:
--   mse_leads
--     WHERE product_id = '9b6c8f36-985f-4d7a-a416-c40da89e23af'  -- thdagentic-consulting
--       AND source = 'job_posting_signal'
--   written by agents/marketing/mkt_lead_finder.run_scraper_v2_scout

COMMENT ON TABLE thd_consulting_leads IS
  'DEPRECATED 2026-10-01. Superseded by mse_leads (product_id = thdagentic-consulting, source = ''job_posting_signal''), written by agents/marketing/mkt_lead_finder.run_scraper_v2_scout. Never populated in production (0 rows at deprecation). Read-only: api/routers/thd_consulting.py still selects from it; nothing writes to it. Do not add columns or build new features on this table.';

COMMENT ON TABLE thd_consulting_scrape_runs IS
  'DEPRECATED 2026-10-01 alongside thd_consulting_leads. Run history for consulting lead sourcing now lives in mse_lead_finder_runs (with per-stage funnel_stats). Never populated in production (0 rows at deprecation).';
