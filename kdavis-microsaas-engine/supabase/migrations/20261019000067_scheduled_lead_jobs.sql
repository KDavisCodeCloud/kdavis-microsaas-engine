-- 067: in-process schedules for the lead-source scouts, more pain phrases,
-- and a table for LinkedIn enrollment skip reasons (Kelvin, 2026-10-19).
--
-- 1. mse_scheduled_job_runs: one row per (job, product, period). The row IS the
--    lock: a scheduler on a second replica or after a restart inserts the
--    same key, hits the unique index and stands down. Never deleted.
-- 2. Seven more pain phrases on consulting + Cloud Decoded (data, not code).
-- 3. mse_dm_enrollment_skips: why the 08:00 LinkedIn enrollment passed over
--    a lead, one row per lead per run, so the Loop Map can show it.

CREATE TABLE IF NOT EXISTS mse_scheduled_job_runs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_key      text NOT NULL,
    product_id   uuid NOT NULL REFERENCES mse_products(id),
    period_key   text NOT NULL,
    status       text NOT NULL DEFAULT 'claimed'
                 CHECK (status IN ('claimed', 'complete', 'failed')),
    run_id       uuid,
    result       jsonb,
    error        text,
    claimed_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS mse_scheduled_job_runs_once
    ON mse_scheduled_job_runs (job_key, product_id, period_key);
ALTER TABLE mse_scheduled_job_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mse_scheduled_job_runs_service_role ON mse_scheduled_job_runs;
CREATE POLICY mse_scheduled_job_runs_service_role ON mse_scheduled_job_runs FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
DROP POLICY IF EXISTS mse_scheduled_job_runs_admin_select ON mse_scheduled_job_runs;
CREATE POLICY mse_scheduled_job_runs_admin_select ON mse_scheduled_job_runs FOR SELECT
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

-- Pain phrases: append, never duplicate (idempotent on a re-run).
UPDATE mse_icp_configs c
   SET pain_phrases = c.pain_phrases || (
         SELECT COALESCE(jsonb_agg(p), '[]'::jsonb)
           FROM jsonb_array_elements_text(
                  '["technical debt","ARM templates","legacy infrastructure","modernize",
                    "Azure migration","re-platform","migrate to Kubernetes"]'::jsonb) AS p
          WHERE NOT (c.pain_phrases ? p))
 WHERE c.product_id IN ('9b6c8f36-985f-4d7a-a416-c40da89e23af', '777a1852-f84c-49d8-890e-cd14670b7f6f');

CREATE TABLE IF NOT EXISTS mse_dm_enrollment_skips (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id  uuid NOT NULL REFERENCES mse_products(id),
    lead_id     uuid,
    run_at      timestamptz NOT NULL,
    reason      text NOT NULL,
    detail      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mse_dm_enrollment_skips_recent
    ON mse_dm_enrollment_skips (product_id, run_at DESC);
ALTER TABLE mse_dm_enrollment_skips ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mse_dm_enrollment_skips_service_role ON mse_dm_enrollment_skips;
CREATE POLICY mse_dm_enrollment_skips_service_role ON mse_dm_enrollment_skips FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
DROP POLICY IF EXISTS mse_dm_enrollment_skips_admin_select ON mse_dm_enrollment_skips;
CREATE POLICY mse_dm_enrollment_skips_admin_select ON mse_dm_enrollment_skips FOR SELECT
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
