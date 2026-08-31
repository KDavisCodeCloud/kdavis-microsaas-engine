-- Migration 030: DIST Phase 1 — attribution + funnel instrumentation
-- Nothing currently connects a signup to a source, or tells you where
-- signups die. Additive only, no ALTER on any existing table.

CREATE TABLE IF NOT EXISTS mse_attribution_touches (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id    uuid NOT NULL REFERENCES mse_products(id),
  anon_id       text NOT NULL,
  tenant_id     uuid,
  touch_index   int  NOT NULL,
  channel       text NOT NULL,
  surface_slug  text,
  utm           jsonb,
  referrer      text,
  occurred_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_attribution_touches_anon ON mse_attribution_touches (anon_id, touch_index);
CREATE INDEX IF NOT EXISTS idx_attribution_touches_tenant ON mse_attribution_touches (tenant_id);
CREATE INDEX IF NOT EXISTS idx_attribution_touches_product_anon ON mse_attribution_touches (product_id, anon_id);

ALTER TABLE mse_attribution_touches ENABLE ROW LEVEL SECURITY;
CREATE POLICY service_role_all ON mse_attribution_touches FOR ALL TO service_role USING (true) WITH CHECK (true);
-- 'admin', not 'owner' -- the DIST spec text says 'owner' throughout, but
-- the real live account (kdav2k5@gmail.com, confirmed via auth.users)
-- has app_metadata.role='admin', matching every other RLS/role check in
-- this entire codebase (tenant_context.py, factory.py, etc.). Written
-- verbatim from the spec, this policy would have permanently locked out
-- the real owner account. Flagged for Phase 0/3/7/8 too -- they copy the
-- identical 'owner' pattern from the same spec text.
CREATE POLICY owner_read ON mse_attribution_touches FOR SELECT
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

CREATE TABLE IF NOT EXISTS mse_funnel_events (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id  uuid NOT NULL REFERENCES mse_products(id),
  tenant_id   uuid NOT NULL,
  step        text NOT NULL CHECK (step IN
                ('signup','email_verified','activated','trial_started','paid','churned')),
  occurred_at timestamptz NOT NULL DEFAULT now(),
  metadata    jsonb,
  UNIQUE (tenant_id, step)
);

CREATE INDEX IF NOT EXISTS idx_funnel_events_product ON mse_funnel_events (product_id, step);

ALTER TABLE mse_funnel_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY service_role_all ON mse_funnel_events FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY owner_read ON mse_funnel_events FOR SELECT
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

CREATE OR REPLACE VIEW mse_attribution_summary AS
SELECT
  f.product_id,
  first_touch.channel  AS first_touch_channel,
  last_touch.channel   AS last_touch_channel,
  count(*) FILTER (WHERE f.step = 'signup') AS signups,
  count(*) FILTER (WHERE f.step = 'paid')   AS paid
FROM mse_funnel_events f
LEFT JOIN LATERAL (
  SELECT channel FROM mse_attribution_touches t
  WHERE t.tenant_id = f.tenant_id ORDER BY touch_index ASC LIMIT 1
) first_touch ON true
LEFT JOIN LATERAL (
  SELECT channel FROM mse_attribution_touches t
  WHERE t.tenant_id = f.tenant_id ORDER BY touch_index DESC LIMIT 1
) last_touch ON true
GROUP BY 1,2,3;
