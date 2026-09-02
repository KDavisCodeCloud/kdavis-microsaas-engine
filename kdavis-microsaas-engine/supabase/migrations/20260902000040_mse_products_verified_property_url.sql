-- Migration 040: mse_products.verified_property_url — real gap found
-- 2026-09-02 while end-to-end verifying GSC access for small-portfolio-hub:
-- agents/dist/indexation_monitor.py::sync_indexation and
-- api/routers/dist.py both read product.get("verified_property_url"),
-- but migration 030 (DIST Phase 2) never added the column to
-- mse_products -- it only created mse_indexation/mse_generator_state.
-- Additive only.

ALTER TABLE mse_products
  ADD COLUMN IF NOT EXISTS verified_property_url text;

COMMENT ON COLUMN mse_products.verified_property_url IS
  'Google Search Console property identifier for this product''s live site. '
  'Format matters: a Domain property (the type this session verified for '
  'small-portfolio-hub) uses "sc-domain:example.com", NOT a URL -- '
  'https://example.com/ only works for a URL-prefix property, a different '
  'verification type. Confirmed live 2026-09-02 via a real Search Console '
  'API call using this exact format.';
