-- Migration 031: DIST Phase 7 -- support drafting layer.
-- Session 2026-08-30. pgvector extension already enabled on this project
-- (confirmed live -- same shared microsaas-prod instance jarvis-decoded's
-- nova.research_log already uses it on). Additive only, no ALTER on any
-- pre-existing table -- mse_products was itself only just created this
-- same session (migration 029), so the ALTER on it below is safe, unlike
-- the mse_leads ALTER Kelvin explicitly held back for his own review.

create table if not exists mse_support_tickets (
  id                uuid primary key default gen_random_uuid(),
  product_id        uuid not null references mse_products(id),
  tenant_id         uuid,
  channel           text not null check (channel in ('chat','email')),
  subject           text,
  body              text not null,
  tier              int  not null check (tier between 0 and 3),
  classification    text,
  sentiment         text,
  status            text not null default 'open'
                      check (status in ('open','drafted','answered','resolved','escalated')),
  first_response_at timestamptz,
  resolved_at       timestamptz,
  created_at        timestamptz not null default now()
);

create index if not exists idx_support_tickets_classification on mse_support_tickets (product_id, classification);
create index if not exists idx_support_tickets_open on mse_support_tickets (status) where status in ('open','drafted');

alter table mse_support_tickets enable row level security;
drop policy if exists service_role_all on mse_support_tickets;
create policy service_role_all on mse_support_tickets for all to service_role using (true) with check (true);
drop policy if exists owner_read on mse_support_tickets;
create policy owner_read on mse_support_tickets for select using (
  (auth.jwt() -> 'app_metadata' ->> 'role') = 'owner'
);

create table if not exists mse_support_drafts (
  id            uuid primary key default gen_random_uuid(),
  ticket_id     uuid not null references mse_support_tickets(id) on delete cascade,
  draft_body    text not null,
  confidence    numeric not null check (confidence between 0 and 1),
  sources       jsonb not null,
  status        text not null default 'pending'
                  check (status in ('pending','approved','edited','rejected','auto_sent')),
  final_body    text,
  approved_by   text,
  sent_at       timestamptz,
  created_at    timestamptz not null default now()
);

-- Owner-read-only, no exceptions -- drafts must never be reachable by a
-- tenant. No tenant-scoped SELECT policy exists here at all (unlike
-- mse_support_tickets, which a future tenant-facing "my tickets" view
-- might read) -- the only SELECT path is the owner-role policy below.
alter table mse_support_drafts enable row level security;
drop policy if exists service_role_all on mse_support_drafts;
create policy service_role_all on mse_support_drafts for all to service_role using (true) with check (true);
drop policy if exists owner_read on mse_support_drafts;
create policy owner_read on mse_support_drafts for select using (
  (auth.jwt() -> 'app_metadata' ->> 'role') = 'owner'
);

create table if not exists mse_support_kb (
  id          uuid primary key default gen_random_uuid(),
  product_id  uuid not null references mse_products(id) on delete cascade,
  source      text not null,
  chunk       text not null,
  embedding   vector(768),
  updated_at  timestamptz not null default now()
);

create index if not exists idx_support_kb_embedding on mse_support_kb using ivfflat (embedding vector_cosine_ops);

alter table mse_support_kb enable row level security;
drop policy if exists service_role_all on mse_support_kb;
create policy service_role_all on mse_support_kb for all to service_role using (true) with check (true);
drop policy if exists owner_read on mse_support_kb;
create policy owner_read on mse_support_kb for select using (
  (auth.jwt() -> 'app_metadata' ->> 'role') = 'owner'
);

-- Tier 1 auto-answer gate. mse_products was created THIS session
-- (migration 029) -- not a pre-existing live table other systems already
-- depend on -- so this ALTER is safe and in-scope, unlike mse_leads.
-- paying_customer_count has no live sync job yet (same open gap as
-- Ledger's record_product_mrr taking MRR as a parameter elsewhere in this
-- portfolio) -- defaults to 0, so the gate is correctly un-enableable
-- until a real value is populated by a future job, not silently bypassed.
alter table mse_products
  add column if not exists support_autoanswer_enabled boolean not null default false,
  add column if not exists support_autoanswer_threshold numeric not null default 0.92,
  add column if not exists paying_customer_count int not null default 0;

-- Real DB-level backstop, same proven mechanism as migration 029's
-- approve_positioning()/reject_direct_approval(): a SECURITY DEFINER
-- function is the only path that can set support_autoanswer_enabled =
-- true, gated by a session-local flag a BEFORE UPDATE trigger checks --
-- this is what actually stops an agent holding the same service_role
-- connection an owner-approval request would use, which RLS alone cannot
-- distinguish.
create or replace function enable_support_autoanswer(p_product_id uuid, p_classification text)
returns mse_products
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role               text;
  v_paying_customers    int;
  v_resolved_tickets    int;
  v_draft_stats         record;
  v_row                 public.mse_products;
begin
  v_role := (auth.jwt() -> 'app_metadata' ->> 'role');
  if v_role is distinct from 'owner' then
    raise exception 'enable_support_autoanswer: owner role required, got %', coalesce(v_role, 'null');
  end if;

  select paying_customer_count into v_paying_customers from public.mse_products where id = p_product_id;
  if v_paying_customers is null or v_paying_customers < 100 then
    raise exception 'enable_support_autoanswer: product has % paying customers, needs >= 100', coalesce(v_paying_customers, 0);
  end if;

  select count(*) into v_resolved_tickets
    from public.mse_support_tickets
    where product_id = p_product_id and status = 'resolved';
  if v_resolved_tickets < 200 then
    raise exception 'enable_support_autoanswer: % resolved tickets, needs >= 200', v_resolved_tickets;
  end if;

  select
    count(*) filter (where d.status in ('approved','auto_sent')) as total_final,
    count(*) filter (where d.status = 'approved') as approved_no_edit
  into v_draft_stats
  from public.mse_support_drafts d
  join public.mse_support_tickets t on t.id = d.ticket_id
  where t.product_id = p_product_id
    and t.classification = p_classification
    and d.created_at >= now() - interval '30 days'
    and d.status in ('approved','edited','auto_sent');

  if v_draft_stats.total_final is null or v_draft_stats.total_final = 0
     or (v_draft_stats.approved_no_edit::numeric / v_draft_stats.total_final) < 0.95 then
    raise exception 'enable_support_autoanswer: classification % has not run >= 30 days at >= 95%% approve-without-edit', p_classification;
  end if;

  perform set_config('dist.enabling_autoanswer', 'true', true);

  update public.mse_products
    set support_autoanswer_enabled = true
    where id = p_product_id
    returning * into v_row;

  perform set_config('dist.enabling_autoanswer', 'false', true);

  return v_row;
end;
$$;

revoke all on function enable_support_autoanswer(uuid, text) from public;
grant execute on function enable_support_autoanswer(uuid, text) to authenticated;

create or replace function reject_direct_autoanswer_enable()
returns trigger
language plpgsql
as $$
begin
  if NEW.support_autoanswer_enabled = true and OLD.support_autoanswer_enabled is distinct from true then
    if coalesce(current_setting('dist.enabling_autoanswer', true), '') <> 'true' then
      raise exception 'mse_products: support_autoanswer_enabled may only become true via enable_support_autoanswer()';
    end if;
  end if;
  return NEW;
end;
$$;

drop trigger if exists trg_reject_direct_autoanswer_enable on mse_products;
create trigger trg_reject_direct_autoanswer_enable
  before update on mse_products
  for each row execute function reject_direct_autoanswer_enable();

-- Cosine-similarity search over mse_support_kb. postgrest's query builder
-- doesn't expose pgvector's <=> operator directly, so SUP-D2
-- (agents/dist/support_drafter.py) calls this via .rpc() instead --
-- same pattern this project would need for any pgvector search through
-- supabase-py. SECURITY DEFINER + search_path pinned since it's called
-- with a service-role connection from application code, not exposed to
-- end users directly.
create or replace function match_support_kb(
  p_product_id uuid,
  p_query_embedding vector(768),
  p_match_count int default 5
)
returns table (
  id text,
  source text,
  chunk text,
  similarity numeric
)
language plpgsql
security definer
-- NOT search_path = '' -- real bug hit and fixed live: pgvector's <=>
-- operator resolves via search_path even when the operand TYPE is
-- schema-qualified (public.vector). The extension lives in public
-- (confirmed: select extnamespace::regnamespace from pg_extension),
-- so an empty search_path makes the operator itself unresolvable
-- ("operator does not exist: public.vector <=> public.vector") even
-- though every table reference below is already explicitly
-- public-qualified. Pinning to exactly 'public' (not empty, not
-- unset) is the standard safe pattern for a SECURITY DEFINER function
-- that needs an extension's operators.
set search_path = 'public'
as $$
begin
  return query
  select
    k.id::text,
    k.source,
    k.chunk,
    (1 - (k.embedding <=> p_query_embedding))::numeric as similarity
  from public.mse_support_kb k
  where k.product_id = p_product_id and k.embedding is not null
  order by k.embedding <=> p_query_embedding
  limit p_match_count;
end;
$$;

revoke all on function match_support_kb(uuid, vector, int) from public;
grant execute on function match_support_kb(uuid, vector, int) to service_role;
