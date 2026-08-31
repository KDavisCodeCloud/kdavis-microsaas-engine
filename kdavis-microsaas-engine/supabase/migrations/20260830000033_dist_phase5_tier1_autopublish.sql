-- Migration 033: DIST Phase 5 -- Tier 1 auto-publish
-- The ONE case in this whole subsystem where the system itself, not a
-- human, triggers a real publish. Narrowly scoped on purpose: callable
-- only by service_role (not 'authenticated' -- publish_surface() is the
-- human path, granted to authenticated admin JWTs; this is the machine
-- path, granted only to the backend's own service-role connection), and
-- hardcodes hitl_tier = 1 in both the eligibility check and the actual
-- UPDATE's WHERE clause -- calling this against a tier-2/3 row matches
-- zero rows and raises, it can never be used to bypass those tiers.
--
-- Same admin-approval-only backstop as publish_surface() (migration 032):
-- sets the session-local dist.publishing flag before writing, which is
-- what lets it past migration 032's trg_reject_direct_publish trigger --
-- that trigger is role-agnostic by design, so this function still has to
-- go through the same real gate, just via a different eligibility check.

create or replace function auto_publish_tier1_surface(p_id uuid)
returns mse_content_surfaces
language plpgsql
security definer
set search_path = ''
as $func$
declare
  v_row public.mse_content_surfaces;
  v_positioning_status text;
begin
  select mp.status into v_positioning_status
  from public.mse_content_surfaces cs
  join public.mse_positioning mp on mp.product_id = cs.product_id and mp.status = 'approved'
  where cs.id = p_id and cs.hitl_tier = 1 and cs.status = 'pending_review'
  limit 1;

  if v_positioning_status is distinct from 'approved' then
    raise exception 'auto_publish_tier1_surface: no tier-1 pending_review row with an approved positioning brief for id %', p_id;
  end if;

  perform set_config('dist.publishing', 'true', true);

  update public.mse_content_surfaces
    set status = 'published', published_at = now()
    where id = p_id and hitl_tier = 1 and status = 'pending_review'
    returning * into v_row;

  perform set_config('dist.publishing', 'false', true);

  if v_row.id is null then
    raise exception 'auto_publish_tier1_surface: publish failed for id % (wrong tier or status)', p_id;
  end if;

  return v_row;
end;
$func$;

revoke all on function auto_publish_tier1_surface(uuid) from public;
grant execute on function auto_publish_tier1_surface(uuid) to service_role;
