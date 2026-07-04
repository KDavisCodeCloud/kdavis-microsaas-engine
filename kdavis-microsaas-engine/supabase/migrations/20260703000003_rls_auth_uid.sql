-- Migration 003: Update RLS policies to enforce via auth.uid()
-- Replaces current_setting('app.tenant_id') with auth.uid() so
-- get_supabase_for_request(jwt) enforces isolation at the DB level.
-- tenant.id = the Supabase Auth user UUID (set on subscription.created webhook).

DROP POLICY IF EXISTS tenant_isolation ON usage_events;
DROP POLICY IF EXISTS tenant_isolation ON milestones;
DROP POLICY IF EXISTS tenant_isolation ON retention_sequences;
DROP POLICY IF EXISTS tenant_isolation ON weekly_digest_log;

-- tenants: each user sees only their own row
CREATE POLICY tenant_isolation ON tenants
  USING (id = auth.uid());

-- child tables: join via tenants RLS (tenant_id = auth user UUID)
CREATE POLICY tenant_isolation ON usage_events
  USING (tenant_id = auth.uid());

CREATE POLICY tenant_isolation ON milestones
  USING (tenant_id = auth.uid());

CREATE POLICY tenant_isolation ON retention_sequences
  USING (tenant_id = auth.uid());

CREATE POLICY tenant_isolation ON weekly_digest_log
  USING (tenant_id = auth.uid());
