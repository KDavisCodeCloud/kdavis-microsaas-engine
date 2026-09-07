-- SECURITY FIX. decide_hitl_item() (migration 20260831000036) checked the
-- caller's role with `if v_role not in ('admin','hitl') then raise ...` and
-- `if ... and v_role <> 'admin' then raise ...`. In SQL, NULL <> 'admin' and
-- NULL NOT IN (...) both evaluate to NULL (unknown), and plpgsql's IF
-- treats NULL as false -- so a caller with no role claim at all (v_role
-- NULL, e.g. a request authenticated with only the public anon key, no
-- user session) skipped BOTH checks and reached the function body as if
-- already authorized. That let an anonymous caller who knew or guessed a
-- pending mse_hitl_items id approve/reject it -- including a
-- mse_positioning-sourced item, bypassing the admin-only positioning
-- approval gate the rest of this codebase treats as a hard invariant
-- enforced at three independent layers. Found live, confirmed by calling
-- this function with the real anon key against microsaas-prod (returned
-- "no pending item with id ..." instead of a role-rejection error) while
-- building update_lead_stage()/log_lead_activity() just now -- those two
-- were written with the same bug and fixed before ever being applied; this
-- migration fixes the one that already shipped.
--
-- coalesce(v_role, '') makes both comparisons total (never NULL), so a
-- missing role claim always fails the check instead of silently passing.

create or replace function decide_hitl_item(
  p_item_id uuid,
  p_decision text,
  p_decided_by text,
  p_domain_status_column text default 'status'
)
returns mse_hitl_items
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role text;
  v_item public.mse_hitl_items;
  v_domain_status text;
begin
  v_role := (auth.jwt() -> 'app_metadata' ->> 'role');
  if coalesce(v_role, '') not in ('admin', 'hitl') then
    raise exception 'decide_hitl_item: admin or hitl role required, got %', coalesce(v_role, 'null');
  end if;

  if p_decision not in ('approved', 'rejected') then
    raise exception 'decide_hitl_item: decision must be approved or rejected, got %', p_decision;
  end if;

  select * into v_item from public.mse_hitl_items where id = p_item_id and status = 'pending';
  if v_item.id is null then
    raise exception 'decide_hitl_item: no pending item with id %', p_item_id;
  end if;

  if v_item.source_table = 'mse_positioning' and coalesce(v_role, '') <> 'admin' then
    raise exception 'decide_hitl_item: positioning-sourced items are admin-only, got role %', coalesce(v_role, 'null');
  end if;

  v_domain_status := case p_decision when 'approved' then 'approved' else 'rejected' end;

  if v_item.source_table = 'mse_support_drafts' then
    update public.mse_support_drafts set status = v_domain_status where id = v_item.source_id;
  elsif v_item.source_table = 'mse_content_surfaces' then
    update public.mse_content_surfaces set status = v_domain_status where id = v_item.source_id;
  elsif v_item.source_table = 'mse_positioning' then
    if p_decision = 'approved' then
      perform public.approve_positioning(v_item.source_id);
    else
      update public.mse_positioning set status = 'rejected' where id = v_item.source_id;
    end if;
  else
    raise exception 'decide_hitl_item: unsupported source_table %', v_item.source_table;
  end if;

  update public.mse_hitl_items
    set status = p_decision, decided_by = p_decided_by, decided_at = now()
    where id = p_item_id
    returning * into v_item;

  return v_item;
end;
$$;
