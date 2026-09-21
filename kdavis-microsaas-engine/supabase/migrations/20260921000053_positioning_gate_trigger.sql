-- Migration 053: DB-level positioning gate on mse_email_sequences INSERT
-- (2026-09-21, W2.5 Step 3 -- the original build spec's Locked Decision
-- 3, "campaign generation is GATED on an approved positioning brief...
-- intentional: it blocks unsourced-claim emails").
--
-- Scoped to email-sequence generation specifically (MKT-O3's own INSERT
-- into this table via agents/marketing/mkt_o3_email_sequence_loader.py),
-- not the whole campaign trigger -- lead-finding/DM/SEO/social are
-- separate concerns (see this session's W2 report). A real Postgres
-- trigger, not an app-layer check: it fires for ANY inserter, including
-- the backend's own service_role connection, which bypasses RLS but
-- never bypasses a trigger (a completely different Postgres mechanism)
-- -- so this cannot be routed around by any backend code path, present
-- or future, the way an app-layer check could be forgotten or skipped.
--
-- No role='admin' check inside this trigger itself, unlike
-- approve_positioning()/enable_support_autoanswer() -- those functions
-- gate a HUMAN action taken via an authenticated request with a real
-- JWT. This trigger gates a BACKEND agent's insert, made via the
-- service_role connection, which carries no per-request JWT/role claims
-- to check (auth.jwt() is empty in that context). The human-facing,
-- role-gated action this trigger actually depends on is
-- approve_positioning() itself -- already shipped, already tested with
-- real JWTs (see tests/sql/test_positioning_gate_live.sql, which
-- exercises both that function AND this trigger together end to end).
CREATE OR REPLACE FUNCTION enforce_positioning_before_email_sequence()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  IF NOT public.has_approved_positioning(NEW.product_id) THEN
    RAISE EXCEPTION 'enforce_positioning_before_email_sequence: product % has no approved positioning brief -- campaign generation blocked', NEW.product_id;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enforce_positioning_before_email_sequence ON mse_email_sequences;
CREATE TRIGGER trg_enforce_positioning_before_email_sequence
  BEFORE INSERT ON mse_email_sequences
  FOR EACH ROW
  EXECUTE FUNCTION enforce_positioning_before_email_sequence();

-- Deliberately BEFORE INSERT only, not UPDATE: this gates NEW generation,
-- not the lifecycle of an already-generated row (approving/retiring a
-- row that predates a later positioning rejection/supersession must not
-- start failing retroactively).
