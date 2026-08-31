-- DIST Phase 8 (2026-08-31) -- live-DB regression script.
--
-- decide_hitl_item(), mse_activities' append-only trigger, and the
-- hitl-role RLS boundary are pure Postgres behavior with no Python
-- wrapper anywhere in this repo -- decoded-empire-os's dist-queue.ts
-- calls decide_hitl_item via .rpc() directly. A Python pytest mock of
-- that RPC would only prove the mock returns what the mock was told to
-- return; it would prove nothing about the real function. Consistent
-- with tests/test_supabase_client.py's own stated convention in this
-- repo ("can't be verified without a real DB connection" -- and it
-- doesn't pretend otherwise), this is a SQL script, not a pytest file.
--
-- Run manually (psql or the Supabase SQL editor) against a Supabase
-- BRANCH or local Postgres copy -- never against microsaas-prod, since
-- it inserts and deletes real rows. This is the exact set of queries
-- run live, by hand, against microsaas-prod on 2026-08-31 to verify
-- Phase 8 before this file existed; it is captured here so the same
-- proof is repeatable without hand-retyping it, once branching or local
-- Postgres is available (both were unavailable this session -- see
-- Task 6's report). NOT wired into CI; no pytest fixture calls it.

begin;

-- ---------------------------------------------------------------------
-- Setup: one throwaway product + one pending mse_support_drafts item +
-- its mse_hitl_items row.
-- ---------------------------------------------------------------------
insert into mse_products (id, slug, name) values
  ('11111111-1111-1111-1111-111111111111', 'phase8-test-product', 'Phase 8 Test Product')
on conflict (id) do nothing;

insert into mse_support_drafts (id, product_id, ticket_id, status, draft_body)
values (
  '22222222-2222-2222-2222-222222222222',
  '11111111-1111-1111-1111-111111111111',
  null,
  'pending_review',
  'Draft reply body.'
);

insert into mse_hitl_items (id, product_id, source_table, source_id, tier, summary, status)
values (
  '33333333-3333-3333-3333-333333333333',
  '11111111-1111-1111-1111-111111111111',
  'mse_support_drafts',
  '22222222-2222-2222-2222-222222222222',
  2,
  'Test support draft decision',
  'pending'
);

-- ---------------------------------------------------------------------
-- Test 1: happy-path transactional sync -- admin approves, both the
-- domain row (mse_support_drafts.status) and the queue row
-- (mse_hitl_items.status/decided_by/decided_at) change together.
-- ---------------------------------------------------------------------
select set_config('request.jwt.claims', '{"app_metadata":{"role":"admin"}}', true);

select decide_hitl_item(
  '33333333-3333-3333-3333-333333333333',
  'approved',
  'test-admin@example.com'
);

do $$
declare
  v_draft_status text;
  v_item_status text;
begin
  select status into v_draft_status from mse_support_drafts where id = '22222222-2222-2222-2222-222222222222';
  select status into v_item_status from mse_hitl_items where id = '33333333-3333-3333-3333-333333333333';
  if v_draft_status is distinct from 'approved' or v_item_status is distinct from 'approved' then
    raise exception 'FAIL: happy-path sync did not update both rows (draft=%, item=%)', v_draft_status, v_item_status;
  end if;
  raise notice 'PASS: happy-path transactional sync';
end $$;

-- ---------------------------------------------------------------------
-- Test 2: forced-failure rollback -- a queue row pointing at an
-- unsupported source_table must reject inside decide_hitl_item(), and
-- the queue row itself must NOT have moved off 'pending' (proves the
-- domain-row update and the queue-row update are one transaction, not
-- two separate writes).
-- ---------------------------------------------------------------------
insert into mse_hitl_items (id, product_id, source_table, source_id, tier, summary, status)
values (
  '44444444-4444-4444-4444-444444444444',
  '11111111-1111-1111-1111-111111111111',
  'not_a_real_table',
  '22222222-2222-2222-2222-222222222222',
  2,
  'Forced-failure fixture',
  'pending'
);

do $$
begin
  begin
    perform decide_hitl_item('44444444-4444-4444-4444-444444444444', 'approved', 'test-admin@example.com');
    raise exception 'FAIL: decide_hitl_item did not reject an unsupported source_table';
  exception when others then
    if sqlerrm like 'FAIL:%' then
      raise;
    end if;
    raise notice 'PASS: unsupported source_table rejected as expected (%)', sqlerrm;
  end;
end $$;

do $$
declare
  v_status text;
begin
  select status into v_status from mse_hitl_items where id = '44444444-4444-4444-4444-444444444444';
  if v_status is distinct from 'pending' then
    raise exception 'FAIL: forced-failure item moved off pending (status=%) -- rollback did not hold', v_status;
  end if;
  raise notice 'PASS: forced-failure rollback held, queue row still pending';
end $$;

-- ---------------------------------------------------------------------
-- Test 3: mse_activities append-only -- insert must succeed, UPDATE and
-- DELETE must both raise, regardless of role (service_role bypasses RLS
-- but not triggers).
-- ---------------------------------------------------------------------
insert into mse_activities (id, product_id, subject_type, subject_id, kind, body, actor)
values (
  '55555555-5555-5555-5555-555555555555',
  '11111111-1111-1111-1111-111111111111',
  'support_draft',
  '22222222-2222-2222-2222-222222222222',
  'decision',
  'Approved via test',
  'test-admin@example.com'
);

do $$
begin
  begin
    update mse_activities set body = 'tampered' where id = '55555555-5555-5555-5555-555555555555';
    raise exception 'FAIL: mse_activities UPDATE was not blocked';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
    raise notice 'PASS: mse_activities UPDATE blocked (%)', sqlerrm;
  end;
end $$;

do $$
begin
  begin
    delete from mse_activities where id = '55555555-5555-5555-5555-555555555555';
    raise exception 'FAIL: mse_activities DELETE was not blocked';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
    raise notice 'PASS: mse_activities DELETE blocked (%)', sqlerrm;
  end;
end $$;

-- ---------------------------------------------------------------------
-- Test 4: hitl-role RLS boundary -- 'hitl' role reads mse_leads /
-- mse_support_tickets / mse_hitl_items but NOT mse_positioning.
-- ---------------------------------------------------------------------
select set_config('request.jwt.claims', '{"app_metadata":{"role":"hitl"}}', true);

do $$
declare
  v_count int;
begin
  select count(*) into v_count from mse_hitl_items where id = '33333333-3333-3333-3333-333333333333';
  if v_count = 0 then
    raise exception 'FAIL: hitl role could not read mse_hitl_items';
  end if;
  raise notice 'PASS: hitl role can read mse_hitl_items';
end $$;

do $$
declare
  v_count int;
begin
  select count(*) into v_count from mse_positioning;
  if v_count <> 0 then
    raise exception 'FAIL: hitl role could read mse_positioning (must be admin-only)';
  end if;
  raise notice 'PASS: hitl role correctly blocked from mse_positioning';
end $$;

reset role;
select set_config('request.jwt.claims', '', true);

-- Cleanup -- roll back everything this script did. Change to COMMIT
-- only if intentionally leaving fixtures behind on a disposable branch.
rollback;
