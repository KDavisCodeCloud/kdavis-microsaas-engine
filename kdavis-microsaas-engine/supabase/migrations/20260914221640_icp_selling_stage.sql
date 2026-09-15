-- Migration: selling_stage on mse_icp_configs
-- Marketing system stage-gate update (session 2026-09-15). Reflects each
-- product's current selling stage so MKT-O2 (DM sequence writer) and
-- MKT-O5 (sequence sender) can gate outreach without a separate table --
-- one field, read at both write time (MKT-O2, before a 'pending_hitl'
-- row is ever created -- the actual HITL approval queue is just
-- mse_dm_sequences WHERE status='pending_hitl', read directly by
-- frontend/app/outreach/page.tsx's Supabase client per outreach.py's own
-- docstring, so gating the write is what keeps a non-active product out
-- of that queue in the first place) and at send time (MKT-O5, in case a
-- product's stage changes after a sequence was already approved but
-- before it sent).
--
-- Values: 'active' (full outreach), 'warming' (content only, no
-- outreach), 'building' (list building only, no sends). Default is the
-- most conservative option ('building') so any existing or future
-- product with no explicit stage set fails closed to "don't send"
-- rather than silently defaulting to active.

ALTER TABLE mse_icp_configs
  ADD COLUMN IF NOT EXISTS selling_stage TEXT NOT NULL DEFAULT 'building';

ALTER TABLE mse_icp_configs
  DROP CONSTRAINT IF EXISTS mse_icp_configs_selling_stage_check;
ALTER TABLE mse_icp_configs
  ADD CONSTRAINT mse_icp_configs_selling_stage_check
  CHECK (selling_stage IN ('active', 'warming', 'building'));

-- mse_products rows for the two BUILD-stage products that don't have one
-- yet (ADA Title II, Bible Devotional) -- both are pre-product-market-fit,
-- zero sends until each ships (ADA Title II: product live; Bible
-- Devotional: app in TestFlight), so activation_definition is left null
-- rather than guessed.
INSERT INTO mse_products (slug, name, activation_definition) VALUES
  ('ada-title-ii', 'ADA Title II Compliance', null),
  ('bible-devotional', 'Bible Devotional App', null)
ON CONFLICT (slug) DO NOTHING;

-- Stage assignments. INSERT ... ON CONFLICT so this is safe whether or
-- not an mse_icp_configs row already exists for a given product (an
-- existing row created via the product's own onboarding flow keeps its
-- job_titles/locations/etc.; only selling_stage is set/overwritten here).
--
-- Cloud Decoded is deliberately NOT included -- it has no mse_products
-- row in this schema at all (CLAUDE.md is explicit that Cloud Decoded is
-- architecturally separate from the MSE product family: its own repo,
-- own Stripe account, "never Cloud Decoded" called out by name in the
-- Stripe Architecture rule). Setting its selling_stage would require
-- fabricating a product registry entry for a product this system was
-- never built to track. See the session report for the flag on this.
INSERT INTO mse_icp_configs (product_id, selling_stage)
SELECT id, 'active' FROM mse_products WHERE slug = 'thdagentic-consulting'
ON CONFLICT (product_id) DO UPDATE SET selling_stage = EXCLUDED.selling_stage;

INSERT INTO mse_icp_configs (product_id, selling_stage)
SELECT id, 'warming' FROM mse_products WHERE slug = 'small-portfolio-hub'
ON CONFLICT (product_id) DO UPDATE SET selling_stage = EXCLUDED.selling_stage;

INSERT INTO mse_icp_configs (product_id, selling_stage)
SELECT id, 'warming' FROM mse_products WHERE slug = 'decodedsix'
ON CONFLICT (product_id) DO UPDATE SET selling_stage = EXCLUDED.selling_stage;

INSERT INTO mse_icp_configs (product_id, selling_stage)
SELECT id, 'building' FROM mse_products WHERE slug = 'ada-title-ii'
ON CONFLICT (product_id) DO UPDATE SET selling_stage = EXCLUDED.selling_stage;

INSERT INTO mse_icp_configs (product_id, selling_stage)
SELECT id, 'building' FROM mse_products WHERE slug = 'bible-devotional'
ON CONFLICT (product_id) DO UPDATE SET selling_stage = EXCLUDED.selling_stage;

-- tradesdesk exists in mse_products but was not named in this stage-gate
-- update -- deliberately left untouched here (no INSERT/UPDATE for it).
-- If it already has an mse_icp_configs row, its selling_stage is
-- whatever the ADD COLUMN default above gave it ('building', the safe
-- default) until someone explicitly sets it.
