-- Migration 011: MSE Factory Expansion — brief generator + monitoring tables
-- Session: 2026-07-17. Corrected from the original spec: the FK on
-- mse_build_briefs.opportunity_id referenced a table called
-- "mse_opportunities", which doesn't exist anywhere in this project —
-- the real, live table is "opportunity_pipeline" (confirmed against the
-- live schema before writing this migration, not assumed).

-- =====================================================
-- TABLE: industry_color_map
-- Used by brief_generator to assign visual identity per vertical
-- =====================================================
CREATE TABLE IF NOT EXISTS industry_color_map (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz DEFAULT now(),
  vertical text UNIQUE NOT NULL,
  primary_accent text NOT NULL,
  secondary_accent text NOT NULL,
  mood text NOT NULL,
  benchmark_brands text[] DEFAULT '{}',
  notes text
);

INSERT INTO industry_color_map (vertical, primary_accent, secondary_accent, mood, benchmark_brands) VALUES
  ('finops',    '#2563eb', '#16a34a', 'trusted/professional',    ARRAY['Stripe','Brex','Ramp']),
  ('govtech',   '#1d4ed8', '#dc2626', 'authoritative/civic',     ARRAY['USDS','Login.gov','Socrata']),
  ('creator',   '#7c3aed', '#f59e0b', 'energetic/modern',        ARRAY['Beehiiv','Gumroad','Kit']),
  ('cloudops',  '#0ea5e9', '#6366f1', 'technical/precise',       ARRAY['Datadog','PagerDuty','Grafana']),
  ('retention', '#10b981', '#f97316', 'growth/warm',             ARRAY['ChurnZero','Gainsight','Vitally']),
  ('open',      '#5a96ff', '#f5a623', 'neutral/adaptable',       ARRAY[]::text[])
ON CONFLICT (vertical) DO NOTHING;

-- =====================================================
-- TABLE: mse_build_briefs
-- Created by brief_generator on every Verdict PASS
-- Visible in CEO dashboard and MSE dashboard
-- =====================================================
CREATE TABLE IF NOT EXISTS mse_build_briefs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  opportunity_id uuid REFERENCES opportunity_pipeline(id) ON DELETE SET NULL,
  product_name text NOT NULL,
  product_slug text UNIQUE NOT NULL,
  verdict_score numeric CHECK (verdict_score >= 0 AND verdict_score <= 100),
  vertical text REFERENCES industry_color_map(vertical),
  claude_code_brief jsonb NOT NULL DEFAULT '{}',
  claude_design_brief jsonb NOT NULL DEFAULT '{}',
  repo_branch text,
  status text NOT NULL DEFAULT 'pending_review'
    CHECK (status IN (
      'pending_review',
      'approved',
      'in_build',
      'launched',
      'monitoring_pending',
      'monitoring_active',
      'archived'
    )),
  visible_to text[] NOT NULL DEFAULT ARRAY['rd','technology','marketing','operations'],
  activated_monitoring bool NOT NULL DEFAULT false,
  monitoring_activated_at timestamptz,
  monitoring_trigger text CHECK (monitoring_trigger IN ('revenue_30d','manual')),
  mrr_at_activation numeric,
  mrr_sustained_days int DEFAULT 0,
  notes text
);

ALTER TABLE mse_build_briefs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can read build briefs" ON mse_build_briefs;
CREATE POLICY "Authenticated users can read build briefs"
  ON mse_build_briefs FOR SELECT
  TO authenticated
  USING (true);

DROP POLICY IF EXISTS "Service role can manage build briefs" ON mse_build_briefs;
CREATE POLICY "Service role can manage build briefs"
  ON mse_build_briefs FOR ALL
  TO service_role
  USING (true);

-- =====================================================
-- TABLE: mse_monitoring_events
-- Log of all Monitor agent runs and flags
-- Shared MSE project — one row per product per night + per flag
-- =====================================================
CREATE TABLE IF NOT EXISTS mse_monitoring_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz DEFAULT now(),
  product_slug text NOT NULL,
  product_name text NOT NULL,
  run_type text NOT NULL CHECK (run_type IN ('nightly','triggered','manual')),
  severity text CHECK (severity IN ('P1','P2','P3','healthy')),
  triggered_thresholds jsonb DEFAULT '[]',
  recommended_action text,
  requires_human_decision bool DEFAULT false,
  context text,
  status text NOT NULL DEFAULT 'open'
    CHECK (status IN ('open','acknowledged','resolved','dismissed')),
  resolved_at timestamptz,
  resolved_by text,
  resolution_notes text
);

ALTER TABLE mse_monitoring_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can read monitoring events" ON mse_monitoring_events;
CREATE POLICY "Authenticated users can read monitoring events"
  ON mse_monitoring_events FOR SELECT
  TO authenticated
  USING (true);

DROP POLICY IF EXISTS "Service role can manage monitoring events" ON mse_monitoring_events;
CREATE POLICY "Service role can manage monitoring events"
  ON mse_monitoring_events FOR ALL
  TO service_role
  USING (true);

-- =====================================================
-- FUNCTION: check_monitoring_activation
-- Nightly cron check — activates monitoring agent when
-- product sustains $4K MRR for 30 consecutive days
-- Called by n8n cron workflow
-- =====================================================
CREATE OR REPLACE FUNCTION check_monitoring_activation()
RETURNS TABLE (
  product_slug text,
  product_name text,
  should_activate bool,
  trigger_reason text
) AS $$
BEGIN
  RETURN QUERY
  SELECT
    b.product_slug,
    b.product_name,
    true as should_activate,
    'revenue_30d' as trigger_reason
  FROM mse_build_briefs b
  WHERE
    b.status = 'launched'
    AND b.activated_monitoring = false
    AND b.mrr_at_activation >= 4000
    AND b.mrr_sustained_days >= 30;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =====================================================
-- FUNCTION: activate_monitoring
-- Called by n8n after check_monitoring_activation confirms trigger
-- =====================================================
CREATE OR REPLACE FUNCTION activate_monitoring(
  p_product_slug text,
  p_trigger text DEFAULT 'revenue_30d'
)
RETURNS void AS $$
BEGIN
  UPDATE mse_build_briefs
  SET
    activated_monitoring = true,
    monitoring_activated_at = now(),
    monitoring_trigger = p_trigger,
    status = 'monitoring_active',
    updated_at = now()
  WHERE product_slug = p_product_slug;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =====================================================
-- INDEXES
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_build_briefs_status ON mse_build_briefs(status);
CREATE INDEX IF NOT EXISTS idx_build_briefs_vertical ON mse_build_briefs(vertical);
CREATE INDEX IF NOT EXISTS idx_build_briefs_monitoring ON mse_build_briefs(activated_monitoring, status);
CREATE INDEX IF NOT EXISTS idx_monitoring_events_product ON mse_monitoring_events(product_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_monitoring_events_severity ON mse_monitoring_events(severity, status);

-- =====================================================
-- REALTIME
-- Enable for CEO dashboard and MSE dashboard live updates
-- =====================================================
ALTER PUBLICATION supabase_realtime ADD TABLE mse_build_briefs;
ALTER PUBLICATION supabase_realtime ADD TABLE mse_monitoring_events;

-- NOTE: per-product tables (product_health_metrics, incident_log,
-- support_tickets) are NOT created here — per the spec, those are
-- templated and only run in each product's own isolated Supabase project
-- at monitoring activation time (the $4K/30-day maturity gate), not in
-- this shared MSE project. See docs/monitoring-agent-suite.md for the
-- template to run at that point.
