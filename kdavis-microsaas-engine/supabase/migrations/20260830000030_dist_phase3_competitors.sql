-- Migration 030: DIST Phase 3 -- competitor registry + freshness loop.
-- Additive only. Seeded from mse_positioning.substitute_set regardless of
-- approval status -- nothing is 'approved' yet system-wide (Phase 0's own
-- verified state as of tonight), and this registry being accurate is
-- independently useful before an owner signs off on the brief it came
-- from. do_nothing entries are excluded (not a competitor with a pricing
-- page to monitor).

CREATE TABLE IF NOT EXISTS mse_competitors (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id        uuid NOT NULL REFERENCES mse_products(id) ON DELETE CASCADE,
  name              text NOT NULL,
  kind              text NOT NULL,          -- mirrors substitute_set.kind
  pricing_url       text,
  pricing_snapshot  jsonb,                  -- structured tiers
  positioning       text,
  known_weaknesses  jsonb DEFAULT '[]',
  last_verified_at  timestamptz NOT NULL DEFAULT now(),
  last_changed_at   timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (product_id, name)
);

ALTER TABLE mse_competitors ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role can manage competitors" ON mse_competitors;
CREATE POLICY "Service role can manage competitors"
  ON mse_competitors FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can read competitors" ON mse_competitors;
CREATE POLICY "Authenticated users can read competitors"
  ON mse_competitors FOR SELECT
  TO authenticated
  USING (true);

CREATE INDEX IF NOT EXISTS idx_competitors_product ON mse_competitors(product_id);
CREATE INDEX IF NOT EXISTS idx_competitors_pricing_url ON mse_competitors(pricing_url);
