-- Closes the DIST Phase 8 loop: mse_leads.stage/owner/next_action (migration
-- 20260831000035, applied to microsaas-prod 2026-09-07) now exist, but there
-- was no atomic way to change a lead's stage and record why in mse_activities
-- together. Mirrors decide_hitl_item()'s own transaction shape (one plpgsql
-- function call = one implicit Postgres transaction) rather than inventing a
-- second pattern for the same problem.
--
-- Role-gated to admin only, matching the live mse_leads_admin_all policy
-- (migration 20260831000037) -- the hitl role has SELECT on mse_leads but no
-- write grant today. This function does not change that; it only gives
-- admin a real, atomic write path instead of two separate client-side calls.

create or replace function update_lead_stage(
  p_lead_id         uuid,
  p_stage           text,
  p_actor           text,
  p_owner           text default null,
  p_next_action     text default null,
  p_next_action_due date default null,
  p_note            text default null
)
returns mse_leads
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role text;
  v_lead public.mse_leads;
begin
  v_role := (auth.jwt() -> 'app_metadata' ->> 'role');
  -- coalesce, not a bare <> -- NULL <> 'admin' is NULL (unknown), which
  -- plpgsql's IF treats as false and silently falls through, letting an
  -- unauthenticated/anon-key caller (v_role NULL) skip this check entirely.
  -- Caught by testing this function with the anon key before shipping it;
  -- see the sibling fix to decide_hitl_item, which had the identical bug.
  if coalesce(v_role, '') <> 'admin' then
    raise exception 'update_lead_stage: admin role required, got %', coalesce(v_role, 'null');
  end if;

  if p_stage not in ('new','contacted','replied','qualified','demo','won','lost') then
    raise exception 'update_lead_stage: invalid stage %', p_stage;
  end if;

  update public.mse_leads
    set stage = p_stage,
        owner = coalesce(p_owner, owner),
        next_action = p_next_action,
        next_action_due = p_next_action_due,
        last_activity_at = now()
    where id = p_lead_id
    returning * into v_lead;

  if v_lead.id is null then
    raise exception 'update_lead_stage: no lead with id %', p_lead_id;
  end if;

  insert into public.mse_activities (product_id, subject_type, subject_id, kind, body, actor)
  values (v_lead.product_id, 'lead', p_lead_id, 'stage_change',
          coalesce(p_note, format('stage -> %s', p_stage)), p_actor);

  return v_lead;
end;
$$;

revoke all on function update_lead_stage(uuid, text, text, text, text, date, text) from public;
grant execute on function update_lead_stage(uuid, text, text, text, text, date, text) to authenticated;

-- Lets the wife (hitl) leave a note on a lead without granting her any
-- mse_leads write -- pure activity log insert, already permitted to any
-- authenticated role by mse_activities' own insert policy (migration
-- 20260831000034); this just gives the app a named, role-checked entry
-- point instead of a bare table insert with no server-side role check at
-- all.
create or replace function log_lead_activity(
  p_lead_id uuid,
  p_actor   text,
  p_body    text
)
returns mse_activities
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role text;
  v_product_id uuid;
  v_activity public.mse_activities;
begin
  v_role := (auth.jwt() -> 'app_metadata' ->> 'role');
  -- coalesce, not a bare NOT IN -- see update_lead_stage's comment above.
  if coalesce(v_role, '') not in ('admin', 'hitl') then
    raise exception 'log_lead_activity: admin or hitl role required, got %', coalesce(v_role, 'null');
  end if;

  select product_id into v_product_id from public.mse_leads where id = p_lead_id;
  if v_product_id is null and not found then
    raise exception 'log_lead_activity: no lead with id %', p_lead_id;
  end if;

  insert into public.mse_activities (product_id, subject_type, subject_id, kind, body, actor)
  values (v_product_id, 'lead', p_lead_id, 'note', p_body, p_actor)
  returning * into v_activity;

  return v_activity;
end;
$$;

revoke all on function log_lead_activity(uuid, text, text) from public;
grant execute on function log_lead_activity(uuid, text, text) to authenticated;
