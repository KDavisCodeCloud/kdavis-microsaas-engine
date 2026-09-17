-- Migration: add 'pending' to mse_lead_finder_runs.status's CHECK constraint.
--
-- Root cause of the CEO Decoded dashboard's "Triggering lead finder
-- failed: 500" (Kelvin, 2026-09-17, first hit via Cloud Decoded but this
-- was already broken for every product): migration 20260814000024
-- created this table with status CHECK (status IN ('running', 'complete',
-- 'failed')) -- but api/routers/leads.py's POST /marketing/leads/find
-- (the real endpoint the dashboard's "Run Lead Finder Now" button calls)
-- inserts the row with status='pending' BEFORE the background task
-- starts, specifically so a caller polling GET /marketing/leads/runs/
-- {run_id} can distinguish "queued, not started yet" from "actively
-- running" -- confirmed by reading agents/marketing/mkt_lead_finder.py's
-- run_lead_finder_for_product, which flips 'pending' -> 'running' itself
-- once the background worker actually begins (line ~286). 'pending' was
-- always the intended design, just never added to this constraint when
-- the API-route trigger path was built on top of the original
-- run_lead_finder_for_product-only design.
--
-- Confirmed via a direct reproduction against production (POST
-- /marketing/leads/find for Cloud Decoded's product_id) and the real
-- Railway traceback: postgrest.exceptions.APIError 23514, "new row for
-- relation mse_lead_finder_runs violates check constraint
-- mse_lead_finder_runs_status_check". This is why the manual button has
-- likely never worked for ANY product -- the weekly n8n cron's own path
-- (calling run_lead_finder_for_product directly, no run_id) inserts
-- status='running' immediately and never hits 'pending' at all, so it
-- was never caught until a human actually clicked the manual button.

ALTER TABLE mse_lead_finder_runs
  DROP CONSTRAINT IF EXISTS mse_lead_finder_runs_status_check;
ALTER TABLE mse_lead_finder_runs
  ADD CONSTRAINT mse_lead_finder_runs_status_check
  CHECK (status IN ('pending', 'running', 'complete', 'failed'));
