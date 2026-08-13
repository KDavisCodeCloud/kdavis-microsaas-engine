-- Migration 021: mse_social_content — MKT-V1 Content Multiplier output.
--
-- Same category as mse_seo_content/mse_research_reports (internal factory
-- tooling, not per-customer tenant data) — admin-access RLS precedent per
-- 20260709000005_marketing_engine.sql's own note, not the auth.uid() pattern.

CREATE TABLE IF NOT EXISTS mse_social_content (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id         UUID NOT NULL,
  campaign_build_id  UUID NOT NULL,
  platform           TEXT NOT NULL CHECK (platform IN ('reddit', 'facebook')),
  title              TEXT,  -- reddit posts have a title; facebook group posts don't
  body               TEXT NOT NULL,
  tenant_id          UUID,  -- required per CLAUDE.md; unused as the RLS key here
  created_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mse_social_content_product ON mse_social_content(product_id, created_at DESC);

ALTER TABLE mse_social_content ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_social_content_admin_access ON mse_social_content
  USING (current_setting('app.role', true) = 'admin');
