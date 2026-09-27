-- Lead yield tuning (2026-09-27): mse_lead_finder_runs previously only
-- recorded the END of the funnel (leads_found = post-dedup-post-verify
-- count, leads_verified, leads_deduplicated) -- the middle of the funnel
-- (queries actually fired, raw Brave results returned, how many were
-- dropped by domain-exclude/robots.txt/fetch-failure before ever
-- becoming a RawLead, how many had a discoverable email at all before
-- SMTP verification) was computed in memory (agents/marketing/
-- mkt_lead_finder.py's find_leads, scrapers/brave_search.py's
-- BraveSearchScraper.stats) and then discarded -- there was no way to
-- tell, after the fact, whether a run's collapse (e.g. 37 queries -> 3
-- leads) happened at the search/filter stage or the email-verification
-- stage without adding print statements and re-running.

ALTER TABLE mse_lead_finder_runs
  ADD COLUMN IF NOT EXISTS funnel_stats JSONB;

COMMENT ON COLUMN mse_lead_finder_runs.funnel_stats IS
  'Per-stage counts for this run: queries_fired, raw_results_returned, dropped_no_link, dropped_excluded_domain, dropped_robots_disallowed, dropped_fetch_failed, passed_scrape_filters, after_dedup, email_found_pre_verify, email_found_post_verify, email_verified. See agents/marketing/mkt_lead_finder.py::run_lead_finder_for_product.';
