-- 075: lead-source expansion, Phases 5-6 -- buyer resolver + targeting fields
-- (Kelvin's lead-sources brief, 2026-11-15).
--
-- NULLable columns on mse_leads only; nothing dropped, altered or rewritten.
-- mse_leads keeps its existing RLS (service-role first, product-scoped).
--
--   buyer_suggestion   the resolver's best candidate {name, title, source,
--                      confidence, evidence, evidence_url, email, email_grade}
--                      shown on the Find the Buyer card for a one-click confirm
--   buyer_resolution   {outcome: auto_resolved|prefilled|manual, checked_at,
--                      sources_tried, candidates}
--   buyer_resolved_at  last resolver pass (re-checked after 14 days)
--   posting_age_days   days the signal role has been open (ATS first-published
--                      date), Phase 6 ranking
--   size_estimate      estimated headcount and size_estimate_method (team
--                      page / Form D / JD / open-role proxy), Phase 6

ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS buyer_suggestion jsonb;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS buyer_resolution jsonb;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS buyer_resolved_at timestamptz;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS posting_age_days int;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS size_estimate int;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS size_estimate_method text;

CREATE INDEX IF NOT EXISTS idx_mse_leads_find_the_buyer
  ON mse_leads (product_id, buyer_resolved_at) WHERE contact_status = 'pending';
