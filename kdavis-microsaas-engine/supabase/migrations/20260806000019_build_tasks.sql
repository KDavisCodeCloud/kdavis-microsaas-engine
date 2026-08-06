-- build_tasks — per-product build checklist, shared source of truth read
-- and written across team.thdstack.com (mark complete + notes), the MSE
-- dashboard, and the CEO Decoded dashboard (both read-only reflections).
--
-- Two task_type values per product: 'standard' (the same infra checklist
-- every product needs -- Stripe, Supabase, deploy, domain) and 'custom'
-- (one row per phase from that product's own BUILD_BRIEF_CLAUDE_CODE.md,
-- so the checklist reflects what THIS product actually needs to build,
-- not a generic template).
--
-- Not multi-tenant customer data (this is THD's own internal build
-- tracking, same category as team_members/tasks from the Team Management
-- System spec), so RLS here is "any authenticated user" rather than the
-- tenant_id-scoped pattern used for real product schemas.

CREATE TABLE IF NOT EXISTS build_tasks (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id  UUID NOT NULL REFERENCES opportunity_pipeline(id) ON DELETE CASCADE,
  brief_id        UUID REFERENCES mse_build_briefs(id) ON DELETE SET NULL,
  task_type       TEXT NOT NULL CHECK (task_type IN ('standard', 'custom')),
  title           TEXT NOT NULL,
  description     TEXT,
  sort_order      INTEGER NOT NULL DEFAULT 0,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed')),
  notes           TEXT,
  completed_by    TEXT,
  completed_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_build_tasks_opportunity ON build_tasks(opportunity_id);

ALTER TABLE build_tasks ENABLE ROW LEVEL SECURITY;

CREATE POLICY build_tasks_service_role ON build_tasks FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Any authenticated user (team member or admin) can read every build's
-- checklist -- this is exactly the information all three dashboards need
-- to show, and it isn't financial/strategic data (CLAUDE.md's team-member
-- boundary is about revenue/finance/agent-internals, not "what infra step
-- are we on").
CREATE POLICY build_tasks_authenticated_read ON build_tasks
  FOR SELECT TO authenticated USING (true);

-- Marking a task complete and leaving notes is the entire point of this
-- table for a team member -- writable by any authenticated user, not
-- admin-gated like opportunity_pipeline itself.
CREATE POLICY build_tasks_authenticated_update ON build_tasks
  FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

-- Realtime: matches the same "the write itself is what notifies
-- subscribed dashboards, no separate broadcast step" pattern already used
-- for mse_build_briefs (see 20260717000011_factory_expansion.sql).
ALTER PUBLICATION supabase_realtime ADD TABLE build_tasks;
