-- Positioning gate (migration 053, 2026-09-21) -- live-DB regression
-- script. Same rationale as tests/sql/test_dist_phase8_live.sql: this
-- exercises a real Postgres trigger + a real SECURITY DEFINER function
-- (approve_positioning) with a simulated real JWT via
-- set_config('request.jwt.claims', ...) -- a FakeSupabase-backed pytest
-- can prove the Python call sites behave correctly when a real DB
-- exception happens (see tests/test_mkt_o3_brevo.py's
-- test_run_o3_positioning_gate_rejection_... test), but it cannot prove
-- the trigger itself actually fires and actually respects the role
-- check embedded in approve_positioning(). This does.
--
-- Run manually (psql or the Supabase SQL editor) against a Supabase
-- BRANCH or local Postgres copy -- never against microsaas-prod, since
-- it inserts real rows. NOT wired into CI; no pytest fixture calls it.

begin;

-- ---------------------------------------------------------------------
-- Setup: one throwaway product with NO approved positioning yet.
-- ---------------------------------------------------------------------
insert into mse_products (id, slug, name) values
  ('44444444-4444-4444-4444-444444444444', 'gate-test-product', 'Gate Test Product')
on conflict (id) do nothing;

-- ---------------------------------------------------------------------
-- Test 1: has_approved_positioning() is false with no positioning row
-- at all.
-- ---------------------------------------------------------------------
do $$
begin
  if has_approved_positioning('44444444-4444-4444-4444-444444444444') then
    raise exception 'FAIL: has_approved_positioning true with zero positioning rows';
  end if;
  raise notice 'PASS: has_approved_positioning false with zero rows';
end $$;

-- ---------------------------------------------------------------------
-- Test 2: the trigger blocks an mse_email_sequences insert for a
-- product with no approved positioning -- the actual gate.
-- ---------------------------------------------------------------------
do $$
begin
  insert into mse_email_sequences (id, product_id, campaign_build_id, emails, status)
  values (
    '55555555-5555-5555-5555-555555555555',
    '44444444-4444-4444-4444-444444444444',
    gen_random_uuid(),
    '[]'::jsonb,
    'pending_hitl'
  );
  raise exception 'FAIL: insert succeeded for a product with no approved positioning';
exception
  when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
    if sqlerrm not like '%no approved positioning brief%' then
      raise exception 'FAIL: insert was blocked, but not by the positioning gate (%)', sqlerrm;
    end if;
    raise notice 'PASS: insert correctly blocked (%)', sqlerrm;
end $$;

-- ---------------------------------------------------------------------
-- Test 3: a non-admin JWT cannot approve positioning (regression on the
-- existing approve_positioning() role check -- this gate's only real
-- value is that approve_positioning() actually is role-gated).
-- ---------------------------------------------------------------------
insert into mse_positioning (
  id, product_id, icp, trigger_event, substitute_set, wedge, wedge_type,
  wedge_evidence, price_rationale, kill_criteria, status
) values (
  '66666666-6666-6666-6666-666666666666',
  '44444444-4444-4444-4444-444444444444',
  'test icp', 'test trigger',
  '[{"kind":"do_nothing"},{"kind":"a"},{"kind":"b"}]'::jsonb,
  'test wedge', 'structural', '{}'::jsonb, 'test price rationale',
  'test kill criteria', 'pending_review'
);

select set_config('request.jwt.claims', '{"app_metadata":{"role":"marketing"}}', true);

do $$
begin
  perform approve_positioning('66666666-6666-6666-6666-666666666666');
  raise exception 'FAIL: non-admin role approved a positioning brief';
exception
  when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
    raise notice 'PASS: non-admin role blocked from approve_positioning (%)', sqlerrm;
end $$;

-- ---------------------------------------------------------------------
-- Test 4: an admin JWT can approve, and the gate then opens -- the same
-- insert that failed in Test 2 now succeeds for the same product.
-- ---------------------------------------------------------------------
select set_config('request.jwt.claims', '{"app_metadata":{"role":"admin"}}', true);

select approve_positioning('66666666-6666-6666-6666-666666666666');

do $$
begin
  if not has_approved_positioning('44444444-4444-4444-4444-444444444444') then
    raise exception 'FAIL: has_approved_positioning still false after admin approval';
  end if;
  raise notice 'PASS: has_approved_positioning true after admin approval';
end $$;

do $$
begin
  insert into mse_email_sequences (id, product_id, campaign_build_id, emails, status)
  values (
    '55555555-5555-5555-5555-555555555555',
    '44444444-4444-4444-4444-444444444444',
    gen_random_uuid(),
    '[]'::jsonb,
    'pending_hitl'
  );
  raise notice 'PASS: insert succeeds once positioning is approved';
exception
  when others then
    raise exception 'FAIL: insert still blocked after approval (%)', sqlerrm;
end $$;

reset role;
select set_config('request.jwt.claims', '', true);

-- Cleanup -- roll back everything this script did. Change to COMMIT
-- only if intentionally leaving fixtures behind on a disposable branch.
rollback;
