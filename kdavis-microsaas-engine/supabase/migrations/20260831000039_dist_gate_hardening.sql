-- Phase 0 gate hardening (B.1-B.3 of the SPH restoration + gate-hardening pass).
--
-- Two real defects, both proven by how small-portfolio-hub v4 got approved
-- while stating Core goes margin-negative inside its own ICP:
--
-- 1. wedge_type had no way to represent "a substitute already offers this,
--    as a real option/setting/tier" -- distinct from "temporary" (they COULD
--    close the gap) because here they already HAVE. Adding 'invalid' as a
--    fourth wedge_type value, set by DIST-P2's new Q0 pre-check.
-- 2. approve_positioning() only ever checked defensibility (is the wedge
--    real) never viability (does the unit economics survive at scale).
--    gross_margin_at_ceiling + margin_floor close that gap; an explicit
--    margin_override_reason lets the owner approve anyway with the reason
--    on record, rather than silently blocking forever.

alter table mse_positioning
  add column if not exists gross_margin_at_ceiling numeric,
  add column if not exists margin_floor numeric not null default 0.70,
  add column if not exists margin_override_reason text;

alter table mse_positioning drop constraint if exists mse_positioning_wedge_type_check;
alter table mse_positioning add constraint mse_positioning_wedge_type_check
  check (wedge_type = any (array['structural', 'execution', 'invalid', 'temporary']));

comment on column mse_positioning.gross_margin_at_ceiling is
  'Gross margin at the TOP of the approved tier''s unit range (not the average) -- v4 looked fine on average and was margin-negative at its own ceiling. Null blocks approval unless margin_override_reason is set.';
comment on column mse_positioning.margin_floor is
  'Minimum acceptable gross_margin_at_ceiling for approve_positioning() to allow approval without an explicit override. Default 0.70, per-row overridable for a product with a known-different cost structure.';
comment on column mse_positioning.margin_override_reason is
  'Required by approve_positioning() whenever gross_margin_at_ceiling is null or below margin_floor at approval time. Records why the owner approved anyway.';

-- create or replace does NOT replace a function whose signature changed --
-- it adds a second overload, which makes any 1-arg call ambiguous (real
-- bug hit and fixed live during this migration's own testing, 2026-08-31).
drop function if exists public.approve_positioning(uuid);

create or replace function public.approve_positioning(p_id uuid, p_margin_override_reason text default null)
 returns mse_positioning
 language plpgsql
 security definer
 set search_path to ''
as $function$
declare
  v_role text;
  v_row public.mse_positioning;
  v_target public.mse_positioning;
begin
  v_role := (auth.jwt() -> 'app_metadata' ->> 'role');
  if v_role is distinct from 'admin' then
    raise exception 'approve_positioning: admin role required, got %', coalesce(v_role, 'null');
  end if;

  select * into v_target from public.mse_positioning where id = p_id and status = 'pending_review';
  if v_target.id is null then
    raise exception 'approve_positioning: no pending_review row with id %', p_id;
  end if;

  if v_target.wedge_type = 'invalid' then
    raise exception 'approve_positioning: wedge_type is invalid -- a substitute already offers this (see wedge_evidence.q0), cannot approve';
  end if;

  if v_target.gross_margin_at_ceiling is null or v_target.gross_margin_at_ceiling < v_target.margin_floor then
    if p_margin_override_reason is null or length(trim(p_margin_override_reason)) = 0 then
      raise exception 'approve_positioning: gross_margin_at_ceiling (%) is null or below margin_floor (%) -- pass p_margin_override_reason to approve anyway',
        v_target.gross_margin_at_ceiling, v_target.margin_floor;
    end if;
  end if;

  perform set_config('dist.approving', 'true', true);

  update public.mse_positioning
    set status = 'approved', approved_by = auth.jwt() ->> 'sub', approved_at = now(),
        margin_override_reason = coalesce(p_margin_override_reason, margin_override_reason)
    where id = p_id and status = 'pending_review'
    returning * into v_row;

  perform set_config('dist.approving', 'false', true);

  if v_row.id is null then
    raise exception 'approve_positioning: no pending_review row with id %', p_id;
  end if;

  return v_row;
end;
$function$;
