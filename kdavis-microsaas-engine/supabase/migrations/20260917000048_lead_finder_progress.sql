-- Migration: live progress columns on mse_lead_finder_runs.
--
-- Kelvin's report (2026-09-17): after clicking "Run Lead Finder Now",
-- there is no status bar showing how long it's taking, what it's
-- currently searching for, or how long it's expected to take. Confirmed
-- by reading LeadPipelinePanel.tsx: it triggers the run and shows one
-- static message, never polls GET /marketing/leads/runs/{run_id} again.
-- Confirmed by reading run_lead_finder_for_product: it writes 'running'
-- once at the start and one final update at completion/failure -- no
-- progress at all in between, even though a real run can take hours
-- (SMTP verification throttled to one lead per 3-6 minutes, per
-- core/email_finder.py's own constants).
--
-- current_step: human-readable description of what's happening right
--   now ("Searching United States for 5 job titles" / "Verifying email
--   3/12 -- acme.com").
-- total_steps / completed_steps: a location-scraped counts as one step,
--   each deduplicated lead's email verification counts as one step.
--   total_steps starts as just the location count (all that's knowable
--   before searching) and is revised upward once the real number of
--   leads needing verification is known -- see mkt_lead_finder.py's
--   find_leads() for exactly where each write happens.
-- estimated_seconds_remaining: rough ETA, recomputed on every progress
--   write. During the verify phase this is genuinely accurate (fixed
--   per-lead delay range, known remaining count); during the search
--   phase it's a coarser upper-bound estimate.

ALTER TABLE mse_lead_finder_runs
  ADD COLUMN IF NOT EXISTS current_step TEXT,
  ADD COLUMN IF NOT EXISTS total_steps INTEGER,
  ADD COLUMN IF NOT EXISTS completed_steps INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS estimated_seconds_remaining INTEGER;
