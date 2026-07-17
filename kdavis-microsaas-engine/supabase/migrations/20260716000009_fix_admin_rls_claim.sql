-- Migration 009: Fix admin-access RLS policies to read the real claim
--
-- Every "admin access" policy since migration 002 checked
-- current_setting('app.role', true) = 'admin' — a Postgres session GUC
-- that nothing in this codebase ever actually sets (no pre-request hook,
-- no SET call anywhere). It always evaluates to NULL, so these policies
-- have silently denied every request except the service-role key since
-- the day they were written — including the MSE dashboard's own logged-in
-- admin reads.
--
-- Correct claim location, verified live against auth.users
-- (raw_app_meta_data / raw_user_meta_data) 2026-07-16: role was actually
-- stored in raw_user_meta_data ("user_metadata" in the JWT), which is
-- client-editable via supabase.auth.updateUser() — using it directly in
-- an authorization check would let any signed-in user grant themselves
-- admin. Moved to app_metadata (service-role only, not client-editable)
-- as part of this fix; policies below read auth.jwt() from app_metadata.

ALTER POLICY pipeline_admin_access ON opportunity_pipeline
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY audit_log_admin_access ON audit_log
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY mse_research_reports_admin_access ON mse_research_reports
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY campaign_builds_admin_access ON campaign_builds
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY mse_apollo_leads_admin_access ON mse_apollo_leads
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY mse_dm_sequences_admin_access ON mse_dm_sequences
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY mse_email_sequences_admin_access ON mse_email_sequences
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY mse_seo_content_admin_access ON mse_seo_content
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

-- CEO Decoded schema tables (20260704000004_ceo_decoded_schema.sql) were
-- already reading auth.jwt() -> 'user_metadata' ->> 'role' — functionally
-- working (until this migration's user_metadata cleanup, see below) but
-- via the same insecure, client-editable claim location. Brought in line
-- with the rest of the fix for consistency and security.
ALTER POLICY advisory_threads_admin_access ON advisory_threads
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY agent_events_admin_access ON agent_events
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY build_queue_admin_access ON build_queue
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY gap_tracker_admin_access ON gap_tracker
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY hitl_queue_admin_access ON hitl_queue
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY hitl_routing_rules_admin_access ON hitl_routing_rules
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY legal_documents_admin_access ON legal_documents
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY operating_stack_admin_access ON operating_stack
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY session_log_admin_access ON session_log
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER POLICY team_members_admin_access ON team_members
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

-- NOTE: the actual role claim was moved server-side for the existing admin
-- user (auth.users.raw_app_meta_data.role, was raw_user_meta_data.role) via
-- the Supabase Admin API as a one-time manual step alongside this migration
-- — that data move isn't expressible as SQL against auth.users' normal
-- columns from here, so it isn't repeated in this file. Any new operator
-- accounts (e.g. a second admin/marketing/rnd user) must have their role
-- set via app_metadata (Admin API or dashboard), never user_metadata.
