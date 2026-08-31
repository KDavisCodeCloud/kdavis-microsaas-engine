-- Migration 029: DIST Phase 0 -- product registry + positioning gate
-- Session 2026-08-30. mse_products did not exist anywhere in this schema
-- (confirmed live) despite every other mse_* table implicitly assuming a
-- central product registry -- existing tables (mse_build_briefs,
-- opportunity_pipeline) use a free-text product_slug instead. Created here
-- as the real prerequisite every later DIST phase's product_id FK needs.

create table if not exists mse_products (
  id                     uuid primary key default gen_random_uuid(),
  slug                   text unique not null,
  name                   text not null,
  status                 text not null default 'active',
  activation_definition  text,
  created_at             timestamptz not null default now()
);

alter table mse_products enable row level security;

drop policy if exists service_role_all on mse_products;
create policy service_role_all on mse_products for all to service_role using (true) with check (true);

drop policy if exists authenticated_read on mse_products;
create policy authenticated_read on mse_products for select to authenticated using (true);

insert into mse_products (slug, name, activation_definition) values
  ('small-portfolio-hub', 'Small Portfolio Hub', 'First rent payment collected'),
  ('tradesdesk', 'TradesDesk', 'First quote sent'),
  ('decodedsix', 'DecodedSix (interactive map)', 'First 10 markers tracked'),
  ('thdagentic-consulting', 'THD Agentic Systems Consulting', null)
on conflict (slug) do nothing;

-- =====================================================
-- TABLE: mse_positioning
-- Phase 0 positioning gate. No mse_content_surfaces row may publish
-- (Phase 4) for a product without exactly one approved row here.
-- =====================================================
create table if not exists mse_positioning (
  id                  uuid primary key default gen_random_uuid(),
  product_id          uuid not null references mse_products(id) on delete cascade,
  version             int  not null default 1,

  icp                 text not null,
  trigger_event       text not null,
  substitute_set      jsonb not null,
  wedge               text not null,
  wedge_type          text not null check (wedge_type in ('structural','temporary')),
  wedge_evidence      jsonb not null,
  price_rationale     text not null,
  kill_criteria       text not null,
  moat_risk           boolean not null default false,
  review_log          jsonb not null default '[]',

  status              text not null default 'draft'
                        check (status in ('draft','pending_review','approved','rejected','superseded')),
  approved_by         text,
  approved_at         timestamptz,
  created_at          timestamptz not null default now(),

  unique (product_id, version)
);

-- substitute_set contract: array, >= 3 entries, at least one do_nothing.
-- Enforced as a real CHECK constraint, not just app-layer convention --
-- an agent inserting a draft that violates the shape fails at the DB.
-- Postgres CHECK constraints can't contain subqueries (0A000), so the
-- do_nothing-membership test uses jsonb_path_exists (a plain function
-- call over the jsonb value, not a correlated subquery) instead of the
-- more natural EXISTS(SELECT ... jsonb_array_elements(...)) form.
alter table mse_positioning
  add constraint mse_positioning_substitute_set_shape
  check (
    jsonb_typeof(substitute_set) = 'array'
    and jsonb_array_length(substitute_set) >= 3
    and jsonb_path_exists(substitute_set, '$[*] ? (@.kind == "do_nothing")')
  );

create unique index if not exists mse_positioning_one_approved
  on mse_positioning (product_id) where status = 'approved';

alter table mse_positioning enable row level security;

-- Owner-only SELECT of the raw table (spec's own policy, verbatim).
drop policy if exists mse_positioning_tenant_read on mse_positioning;
create policy mse_positioning_tenant_read on mse_positioning
  for select using (
    (auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'
  );

-- Service role (agents) may INSERT drafts and UPDATE most fields, but the
-- guard requires agents can never themselves flip status to 'approved'.
-- Real enforcement: service_role gets ALL privileges at the RLS layer
-- (matching this project's own established convention -- service_role
-- bypasses RLS by default in Supabase, so the *actual* backstop against
-- an agent-held service-role key approving its own brief is the trigger
-- below, not RLS, which cannot distinguish "agent code" from "owner code"
-- both using the same service_role connection). This mirrors how
-- provision_stripe.py-style manual gates are enforced in this codebase:
-- a hard code-level check, not a claim you can't verify at the DB layer
-- alone when both actors share one Postgres role.
drop policy if exists service_role_all on mse_positioning;
create policy service_role_all on mse_positioning for all to service_role using (true) with check (true);

-- The real, DB-level backstop: status may only become 'approved' via this
-- SECURITY DEFINER function, which is the ONLY thing allowed to write
-- 'approved' -- verified below, not by application-layer discipline.
-- Called by an authenticated request carrying a real owner-role JWT
-- (checked via auth.jwt(), which is populated from the caller's own
-- session, not spoofable by a service-role backend key alone unless that
-- key ALSO forges the JWT claims -- out of scope for what RLS can stop;
-- this function is the code-level gate the app must call through, and
-- app/agent code is instructed (Non-Negotiable, enforced by convention +
-- this function's own role check) to never bypass it with a raw UPDATE.
create or replace function approve_positioning(p_id uuid)
returns mse_positioning
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role text;
  v_row public.mse_positioning;
begin
  v_role := (auth.jwt() -> 'app_metadata' ->> 'role');
  if v_role is distinct from 'admin' then
    raise exception 'approve_positioning: admin role required, got %', coalesce(v_role, 'null');
  end if;

  update public.mse_positioning
    set status = 'approved', approved_by = auth.jwt() ->> 'sub', approved_at = now()
    where id = p_id and status = 'pending_review'
    returning * into v_row;

  if v_row.id is null then
    raise exception 'approve_positioning: no pending_review row with id %', p_id;
  end if;

  return v_row;
end;
$$;

revoke all on function approve_positioning(uuid) from public;
grant execute on function approve_positioning(uuid) to authenticated;

-- A raw UPDATE ... SET status = 'approved' is still physically possible
-- for a service_role-holding connection (Postgres itself can't
-- distinguish "the approve_positioning caller" from "an agent's direct
-- UPDATE" once both hold the same role) -- so this is additionally
-- guarded by a trigger that rejects any UPDATE setting status='approved'
-- that did NOT go through approve_positioning's own session-local flag.
-- This is the real, unconditional DB-level block the spec's guard asks
-- for: no code path other than approve_positioning can ever result in a
-- committed 'approved' row, full stop, regardless of which role holds
-- the connection.
create or replace function reject_direct_approval()
returns trigger
language plpgsql
as $$
begin
  if NEW.status = 'approved' and OLD.status is distinct from 'approved' then
    if coalesce(current_setting('dist.approving', true), '') <> 'true' then
      raise exception 'mse_positioning: status may only become approved via approve_positioning()';
    end if;
  end if;
  return NEW;
end;
$$;

drop trigger if exists trg_reject_direct_approval on mse_positioning;
create trigger trg_reject_direct_approval
  before update on mse_positioning
  for each row execute function reject_direct_approval();

-- approve_positioning sets the session-local flag reject_direct_approval
-- checks, then clears it -- the only path where NEW.status='approved'
-- passes the trigger.
create or replace function approve_positioning(p_id uuid)
returns mse_positioning
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role text;
  v_row public.mse_positioning;
begin
  v_role := (auth.jwt() -> 'app_metadata' ->> 'role');
  if v_role is distinct from 'admin' then
    raise exception 'approve_positioning: admin role required, got %', coalesce(v_role, 'null');
  end if;

  perform set_config('dist.approving', 'true', true);

  update public.mse_positioning
    set status = 'approved', approved_by = auth.jwt() ->> 'sub', approved_at = now()
    where id = p_id and status = 'pending_review'
    returning * into v_row;

  perform set_config('dist.approving', 'false', true);

  if v_row.id is null then
    raise exception 'approve_positioning: no pending_review row with id %', p_id;
  end if;

  return v_row;
end;
$$;

revoke all on function approve_positioning(uuid) from public;
grant execute on function approve_positioning(uuid) to authenticated;
