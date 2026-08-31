-- Migration 032: DIST Phase 4 -- Surface Generator
-- mse_content_surfaces + the publish-only-by-admin gate, mirroring
-- Phase 0's approve_positioning() pattern exactly (migration 029):
-- SECURITY DEFINER function is the only path that can set
-- status='published', backed by a trigger that rejects any direct
-- UPDATE doing the same, regardless of which Postgres role holds the
-- connection. Real role value confirmed live tonight is 'admin', not
-- the spec text's 'owner' (see migrations 029/030/031's own fixes) --
-- used correctly from the start here, not copied-then-fixed a third time.

create table if not exists mse_content_surfaces (
  id            uuid primary key default gen_random_uuid(),
  product_id    uuid not null references mse_products(id) on delete cascade,
  archetype     text not null check (archetype in
                  ('vs_competitor','alternatives_to','jurisdiction',
                   'jtbd','calculator','faq_block')),
  slug          text not null,
  title         text not null,
  body_mdx      text,
  data_payload  jsonb,
  competitor_id uuid references mse_competitors(id),
  hitl_tier     int not null check (hitl_tier in (1,2,3)),
  status        text not null default 'draft'
                  check (status in ('draft','pending_review','approved','published','stale','archived')),
  quality_score jsonb,
  embedding     vector(768),
  reject_reason text,
  reject_count  int not null default 0,
  published_at  timestamptz,
  created_at    timestamptz not null default now(),
  unique (product_id, slug)
);

create index if not exists idx_content_surfaces_product_status on mse_content_surfaces(product_id, status);
create index if not exists idx_content_surfaces_competitor on mse_content_surfaces(competitor_id);
create index if not exists idx_content_surfaces_embedding on mse_content_surfaces
  using ivfflat (embedding vector_cosine_ops);

alter table mse_content_surfaces enable row level security;

-- Public can read only published surfaces (Phase 4.4: this is the future
-- render path once a product frontend consumes it -- no frontend does
-- yet tonight, but the read policy is correct infra to have in place).
drop policy if exists mse_content_surfaces_public_read on mse_content_surfaces;
create policy mse_content_surfaces_public_read on mse_content_surfaces
  for select using (status = 'published');

-- Admin can read everything (drafts, pending_review, etc.) for the HITL
-- queue UI (Phase 5/8, not built tonight, but the read path is correct
-- to have now).
drop policy if exists mse_content_surfaces_admin_read on mse_content_surfaces;
create policy mse_content_surfaces_admin_read on mse_content_surfaces
  for select using (
    (auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'
  );

-- Agents (service_role) can insert/update freely -- the actual backstop
-- against an agent-held key publishing its own surface is the trigger
-- below, same reasoning as Phase 0's own comment: RLS alone can't
-- distinguish "agent code" from "admin code" once both hold service_role.
drop policy if exists mse_content_surfaces_service_role_all on mse_content_surfaces;
create policy mse_content_surfaces_service_role_all on mse_content_surfaces
  for all to service_role using (true) with check (true);

-- The real, DB-level backstop: status may only become 'published' via
-- this SECURITY DEFINER function.
create or replace function publish_surface(p_id uuid)
returns mse_content_surfaces
language plpgsql
security definer
set search_path = ''
as $func$
declare
  v_role text;
  v_row public.mse_content_surfaces;
  v_positioning_status text;
begin
  v_role := (auth.jwt() -> 'app_metadata' ->> 'role');
  if v_role is distinct from 'admin' then
    raise exception 'publish_surface: admin role required, got %', coalesce(v_role, 'null');
  end if;

  -- The whole point of Phase 0: never publish for a product without an
  -- approved positioning brief, enforced here too, not just at plan time.
  select mp.status into v_positioning_status
  from public.mse_content_surfaces cs
  join public.mse_positioning mp on mp.product_id = cs.product_id and mp.status = 'approved'
  where cs.id = p_id
  limit 1;

  if v_positioning_status is distinct from 'approved' then
    raise exception 'publish_surface: product has no approved positioning brief';
  end if;

  perform set_config('dist.publishing', 'true', true);

  update public.mse_content_surfaces
    set status = 'published', published_at = now()
    where id = p_id and status in ('approved','pending_review')
    returning * into v_row;

  perform set_config('dist.publishing', 'false', true);

  if v_row.id is null then
    raise exception 'publish_surface: no publishable row with id %', p_id;
  end if;

  return v_row;
end;
$func$;

revoke all on function publish_surface(uuid) from public;
grant execute on function publish_surface(uuid) to authenticated;

create or replace function reject_direct_publish()
returns trigger
language plpgsql
as $func$
begin
  if NEW.status = 'published' and OLD.status is distinct from 'published' then
    if coalesce(current_setting('dist.publishing', true), '') <> 'true' then
      raise exception 'mse_content_surfaces: status may only become published via publish_surface()';
    end if;
  end if;
  return NEW;
end;
$func$;

drop trigger if exists trg_reject_direct_publish on mse_content_surfaces;
create trigger trg_reject_direct_publish
  before update on mse_content_surfaces
  for each row execute function reject_direct_publish();

-- Duplicate-detection lookup, mirrors Phase 7's match_support_kb() shape
-- (migration 031) -- SECURITY DEFINER, search_path='public' (not ''),
-- required for pgvector's <=> operator to resolve inside the function.
create or replace function match_content_surfaces(
  p_product_id uuid,
  p_query_embedding vector(768),
  p_match_threshold float default 0.85,
  p_match_count int default 5
)
returns table (id uuid, slug text, similarity float)
language plpgsql
security definer
set search_path = 'public'
as $func$
begin
  return query
  select cs.id, cs.slug, 1 - (cs.embedding <=> p_query_embedding) as similarity
  from mse_content_surfaces cs
  where cs.product_id = p_product_id
    and cs.embedding is not null
    and 1 - (cs.embedding <=> p_query_embedding) > p_match_threshold
  order by cs.embedding <=> p_query_embedding
  limit p_match_count;
end;
$func$;

revoke all on function match_content_surfaces(uuid, vector, float, int) from public;
grant execute on function match_content_surfaces(uuid, vector, float, int) to service_role, authenticated;
