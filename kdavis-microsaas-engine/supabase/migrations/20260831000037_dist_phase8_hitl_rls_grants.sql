-- DIST Phase 8 (2026-08-31) -- extend read access to the 'hitl' role
-- (the wife's queue) across the domain tables Phase 8's unified queue
-- surfaces, per EXECUTION_ORDER.md's Phase 8 spec. These four policy
-- changes were applied live to microsaas-prod by hand during this
-- session's testing (via the Supabase Management API's database/query
-- endpoint) before this migration file existed; this file codifies them
-- so the schema history is accurate and the same state is reproducible
-- on a fresh environment. Applying this migration against microsaas-prod
-- now is a no-op (drop-if-exists + recreate of policies already live).
--
-- mse_positioning is deliberately left untouched here -- it stays
-- admin-only for both read and write. decide_hitl_item() independently
-- re-checks role for positioning-sourced items even if a page-level gate
-- were bypassed (see decoded-empire-os's positioning/page.tsx comment).

alter table mse_leads enable row level security;
drop policy if exists mse_leads_admin_access on mse_leads;
drop policy if exists mse_leads_admin_all on mse_leads;
create policy mse_leads_admin_all on mse_leads
  for all
  using ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')
  with check ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

drop policy if exists mse_leads_hitl_read on mse_leads;
create policy mse_leads_hitl_read on mse_leads
  for select
  using ((auth.jwt() -> 'app_metadata' ->> 'role') in ('admin', 'hitl'));

alter table mse_support_tickets enable row level security;
drop policy if exists owner_read on mse_support_tickets;
drop policy if exists mse_support_tickets_read on mse_support_tickets;
create policy mse_support_tickets_read on mse_support_tickets
  for select
  using ((auth.jwt() -> 'app_metadata' ->> 'role') in ('admin', 'hitl'));

alter table mse_support_drafts enable row level security;
drop policy if exists owner_read on mse_support_drafts;
drop policy if exists mse_support_drafts_read on mse_support_drafts;
create policy mse_support_drafts_read on mse_support_drafts
  for select
  using ((auth.jwt() -> 'app_metadata' ->> 'role') in ('admin', 'hitl'));

alter table mse_content_surfaces enable row level security;
drop policy if exists mse_content_surfaces_admin_read on mse_content_surfaces;
drop policy if exists mse_content_surfaces_hitl_read on mse_content_surfaces;
create policy mse_content_surfaces_hitl_read on mse_content_surfaces
  for select
  using ((auth.jwt() -> 'app_metadata' ->> 'role') in ('admin', 'hitl'));
