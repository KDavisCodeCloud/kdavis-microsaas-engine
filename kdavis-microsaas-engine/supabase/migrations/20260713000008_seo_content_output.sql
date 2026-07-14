-- Migration 008: SEO content output — MKT-S1's generated support blog posts.
--
-- Same category as mse_email_sequences (20260713000007): internal factory
-- tooling, admin-access RLS precedent, tenant_id nullable per CLAUDE.md's
-- blanket rule but not used as the RLS key.

CREATE TABLE IF NOT EXISTS mse_seo_content (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id         UUID NOT NULL,
  campaign_build_id  UUID REFERENCES campaign_builds(id),
  title              TEXT NOT NULL,
  blog_post          TEXT NOT NULL,
  meta_description   TEXT NOT NULL,
  schema_json        JSONB NOT NULL,
  word_count         INTEGER NOT NULL,
  status             TEXT DEFAULT 'draft'
                       CHECK (status IN ('draft', 'published', 'archived')),
  tenant_id          UUID,
  created_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mse_seo_content_product ON mse_seo_content(product_id);
CREATE INDEX IF NOT EXISTS idx_mse_seo_content_campaign_build ON mse_seo_content(campaign_build_id);

ALTER TABLE mse_seo_content ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_seo_content_admin_access ON mse_seo_content
  USING (current_setting('app.role', true) = 'admin');
