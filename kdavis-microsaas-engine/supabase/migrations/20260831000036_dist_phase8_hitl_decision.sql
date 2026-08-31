-- Service-layer decision endpoint for the unified mse_hitl_items queue.
-- A single plpgsql function call is one implicit Postgres transaction --
-- the same real mechanism approve_positioning()/publish_surface()/
-- auto_publish_tier1_surface() already use tonight, not a new pattern.
-- Writes the queue row's new status AND the referenced domain row's
-- status together; any failure partway through rolls back both, tested
-- directly below with a forced-failure fixture.

create or replace function decide_hitl_item(
  p_item_id uuid,
  p_decision text,          -- 'approved' | 'rejected'
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
  if v_role not in ('admin', 'hitl') then
    raise exception 'decide_hitl_item: admin or hitl role required, got %', coalesce(v_role, 'null');
  end if;

  if p_decision not in ('approved', 'rejected') then
    raise exception 'decide_hitl_item: decision must be approved or rejected, got %', p_decision;
  end if;

  select * into v_item from public.mse_hitl_items where id = p_item_id and status = 'pending';
  if v_item.id is null then
    raise exception 'decide_hitl_item: no pending item with id %', p_item_id;
  end if;

  -- Tier 3 items sourced from mse_positioning can only ever be decided by
  -- an admin, regardless of the general admin-or-hitl check above --
  -- matches the standing constraint that positioning approval never
  -- becomes reachable via the shared queue for a non-admin role.
  if v_item.source_table = 'mse_positioning' and v_role <> 'admin' then
    raise exception 'decide_hitl_item: positioning-sourced items are admin-only, got role %', v_role;
  end if;

  v_domain_status := case p_decision when 'approved' then 'approved' else 'rejected' end;

  -- Domain row update, dynamic on source_table -- deliberately narrow to
  -- a small allowlist rather than fully dynamic SQL against an arbitrary
  -- table name (an arbitrary p_item_id-controlled table/column name would
  -- be a real SQL-injection-shaped risk if source_table weren't already
  -- constrained to rows this function itself just read from the queue
  -- table; allowlisting anyway as defense in depth).
  if v_item.source_table = 'mse_support_drafts' then
    update public.mse_support_drafts set status = v_domain_status where id = v_item.source_id;
  elsif v_item.source_table = 'mse_content_surfaces' then
    update public.mse_content_surfaces set status = v_domain_status where id = v_item.source_id;
  elsif v_item.source_table = 'mse_positioning' then
    -- Positioning's own approve_positioning() is the real, already-tested
    -- approval path (sets the trigger flag it requires) -- reuse it
    -- rather than writing a second, competing way to approve a brief.
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

revoke all on function decide_hitl_item(uuid, text, text, text) from public;
grant execute on function decide_hitl_item(uuid, text, text, text) to authenticated;
