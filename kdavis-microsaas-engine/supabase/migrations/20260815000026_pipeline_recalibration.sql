-- Migration 026: Pipeline health auto-recalibration (Kelvin's rule, 2026-08-15).
--
-- Verdict/Dispatch have no memory across calls -- each evaluation is a
-- stateless LLM call. The rolling-10-submission build-rate monitor lives
-- in code (agents/aggregator/pipeline_health.py), reading real history
-- from opportunity_pipeline + opportunity_pipeline_rejections. When the
-- build rate drops below 10% and one failure category dominates (>40%),
-- the system auto-applies the matching recalibration and writes a row
-- here — this table IS the audit trail (no separate changelog needed)
-- and the runtime source of truth: agent.py/orchestrator's agent.py read
-- the active row(s) here and inject the corresponding instruction text
-- into Dispatch's or Verdict's system prompt at call time, rather than
-- mutating the prompt.md files on disk. Deliberately safer than a
-- self-modifying prompt file: fully reversible (deactivate the row),
-- fully logged (the row itself is the log), no code redeploy needed to
-- take effect or roll back.
--
-- Never covers the three permanent hard stops (third-party approval
-- gate, mid-acquisition platform, API cannot perform core action) —
-- those are enforced at the code level in agents/aggregator/agent.py
-- and are never subject to recalibration, by design (see
-- pipeline_health.py's _RECALIBRATION_ACTIONS — those three categories
-- have no entry, so a dominant category there is logged but never
-- auto-acted on).

CREATE TABLE IF NOT EXISTS mse_pipeline_recalibrations (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  triggered_at       TIMESTAMPTZ DEFAULT NOW(),
  window_size        INTEGER NOT NULL,
  build_rate_pct     NUMERIC NOT NULL,
  dominant_category  TEXT NOT NULL,
  category_pct       NUMERIC NOT NULL,
  action_taken       TEXT NOT NULL,
  -- 'dispatch' -> injected into agents/orchestrator/agent.py's Dispatch
  -- system prompt (research-scope changes: read-only profile, new
  -- sources). 'verdict' -> injected into agents/aggregator/agent.py's
  -- Verdict system prompt (soft-criteria recalibration, e.g. the
  -- widely-adopted-single-connection downgrade). 'none' -> logged for
  -- visibility only, no defined action exists for this category (always
  -- true for the three hard-stop categories).
  target             TEXT NOT NULL CHECK (target IN ('dispatch', 'verdict', 'none')),
  active             BOOLEAN NOT NULL DEFAULT TRUE,
  sample             JSONB DEFAULT '[]',
  tenant_id          UUID,
  created_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mse_pipeline_recalibrations_active ON mse_pipeline_recalibrations(active, target);

ALTER TABLE mse_pipeline_recalibrations ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_pipeline_recalibrations_admin_access ON mse_pipeline_recalibrations
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
