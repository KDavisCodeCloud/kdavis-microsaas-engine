-- Migration 002: Opportunity pipeline (research agent output)

CREATE TABLE opportunity_pipeline (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  vertical TEXT NOT NULL,
  pain_point TEXT NOT NULL,
  icp JSONB NOT NULL,
  solution_concept TEXT NOT NULL,
  mrr_calculation TEXT,
  competitor_pricing_avg NUMERIC(10,2),
  conservative_mrr_potential NUMERIC(10,2) NOT NULL,
  competition_density TEXT CHECK (competition_density IN ('red', 'yellow', 'green')),
  competition_density_reason TEXT,
  build_confidence_score INTEGER CHECK (build_confidence_score BETWEEN 0 AND 100),
  build_confidence_reason TEXT,
  retention_hooks JSONB NOT NULL DEFAULT '{}',
  competitor_examples JSONB DEFAULT '[]',
  source_urls JSONB DEFAULT '[]',
  tier_structure JSONB DEFAULT '{}',
  mcp_integration_surface TEXT,
  stack_compatible BOOLEAN DEFAULT TRUE,
  stack_compatibility_notes TEXT,
  estimated_build_weeks INTEGER,
  status TEXT DEFAULT 'discovered' CHECK (status IN ('discovered', 'validated', 'watch', 'rejected', 'READY_TO_BUILD', 'building', 'launched', 'tracking_mrr')),
  rejection_reason TEXT,
  owner TEXT,
  mrr_actual NUMERIC(10,2),
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Hard MRR floor enforced at DB level
ALTER TABLE opportunity_pipeline
  ADD CONSTRAINT mrr_floor_check
  CHECK (conservative_mrr_potential >= 4000 OR status = 'rejected');

ALTER TABLE opportunity_pipeline ENABLE ROW LEVEL SECURITY;

-- Pipeline is admin-accessible (no per-tenant isolation — this is internal tooling)
CREATE POLICY pipeline_admin_access ON opportunity_pipeline
  USING (current_setting('app.role', true) = 'admin');
