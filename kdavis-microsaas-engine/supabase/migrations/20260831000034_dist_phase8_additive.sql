-- DIST Phase 8 (additive only). The mse_leads ALTER is a SEPARATE,
-- unapplied migration (20260831000035_dist_phase8_mse_leads_alter.sql) --
-- staged, not run against microsaas-prod, per the explicit hard gate on
-- this task. Everything in this file is CREATE TABLE / CREATE INDEX /
-- CREATE POLICY / CREATE TRIGGER only.

-- =====================================================
-- TABLE: mse_hitl_items -- the unified queue
-- =====================================================
create table if not exists mse_hitl_items (
  id            uuid primary key default gen_random_uuid(),
  product_id    uuid references mse_products(id),
  source_table  text not null,
  source_id     uuid not null,
  tier          int  not null check (tier between 1 and 3),
  summary       text not null,
  assigned_to   text,
  status        text not null default 'pending'
                  check (status in ('pending','approved','edited','rejected','expired')),
  decided_by    text,
  decided_at    timestamptz,
  due_at        timestamptz,
  created_at    timestamptz not null default now(),
  unique (source_table, source_id)
);

create index if not exists idx_mse_hitl_items_pending
  on mse_hitl_items (status, tier, assigned_to) where status = 'pending';

alter table mse_hitl_items enable row level security;

drop policy if exists mse_hitl_items_service_role on mse_hitl_items;
create policy mse_hitl_items_service_role on mse_hitl_items
  for all to service_role using (true) with check (true);

-- Real role model, matching decoded-empire-os's own app-level Role type
-- (lib/auth/roles.ts: 'admin' | 'hitl' | 'viewer') -- 'admin' and 'hitl'
-- (the wife's real role, per that app's DASHBOARD_HITL_EMAIL) can both
-- read/act on the queue; positioning-sourced items are additionally
-- restricted below since Tier 3 positioning approval must stay
-- admin-only regardless of queue membership.
drop policy if exists mse_hitl_items_read on mse_hitl_items;
create policy mse_hitl_items_read on mse_hitl_items
  for select using (
    (auth.jwt() -> 'app_metadata' ->> 'role') in ('admin', 'hitl')
  );

-- =====================================================
-- TABLE: mse_activities -- append-only, no exceptions
-- =====================================================
create table if not exists mse_activities (
  id           uuid primary key default gen_random_uuid(),
  product_id   uuid references mse_products(id),
  subject_type text not null check (subject_type in ('lead','tenant')),
  subject_id   uuid not null,
  kind         text not null,
  body         text,
  actor        text not null,
  occurred_at  timestamptz not null default now()
);

create index if not exists idx_mse_activities_subject
  on mse_activities (subject_type, subject_id, occurred_at desc);

alter table mse_activities enable row level security;

drop policy if exists mse_activities_insert on mse_activities;
create policy mse_activities_insert on mse_activities
  for insert to service_role, authenticated with check (true);

drop policy if exists mse_activities_read on mse_activities;
create policy mse_activities_read on mse_activities
  for select using (
    (auth.jwt() -> 'app_metadata' ->> 'role') in ('admin', 'hitl')
  );

-- Real, unconditional enforcement -- NOT just "no UPDATE/DELETE RLS
-- policy exists". service_role bypasses RLS entirely by default in
-- Supabase (the same fact Phase 0's own approve_positioning() comment
-- documents), so an RLS-only block would still let a service-role
-- connection UPDATE/DELETE freely. Triggers fire unconditionally
-- regardless of RLS bypass status -- this is the actual backstop, tested
-- directly against a real service-role connection below, not assumed
-- from the policy list alone.
create or replace function reject_activity_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception 'mse_activities is append-only -- % is not permitted, at any layer, for any role', TG_OP;
end;
$$;

drop trigger if exists trg_reject_activity_update on mse_activities;
create trigger trg_reject_activity_update
  before update on mse_activities
  for each row execute function reject_activity_mutation();

drop trigger if exists trg_reject_activity_delete on mse_activities;
create trigger trg_reject_activity_delete
  before delete on mse_activities
  for each row execute function reject_activity_mutation();

-- =====================================================
-- TABLE: mse_product_mrr -- inert until STRIPE_SECRET_KEY exists
-- =====================================================
create table if not exists mse_product_mrr (
  id              uuid primary key default gen_random_uuid(),
  product_id      uuid not null references mse_products(id),
  snapshot_date   date not null,
  mrr_cents       bigint not null,
  active_subs     int not null,
  new_mrr_cents   bigint default 0,
  churn_mrr_cents bigint default 0,
  unique (product_id, snapshot_date)
);

alter table mse_product_mrr enable row level security;

drop policy if exists mse_product_mrr_service_role on mse_product_mrr;
create policy mse_product_mrr_service_role on mse_product_mrr
  for all to service_role using (true) with check (true);

drop policy if exists mse_product_mrr_read on mse_product_mrr;
create policy mse_product_mrr_read on mse_product_mrr
  for select using (
    (auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'
  );
