-- Migration 025: Brevo list mapping — replaces systeme.io as MKT-O3's email
-- sequence provider. Each MSE product needs its Brevo contact list ID
-- stored so agents/marketing/mkt_o3_email_sequence_loader.py's
-- enroll_trial_in_sequence() knows which list to add a trial signup to
-- (adding a contact to that list is what fires the product's pre-built
-- Brevo automation workflow — see BREVO_SETUP.md).
--
-- product_id is a plain UUID with no FK, matching every other product_id
-- column in this schema (mse_leads, mse_icp_configs, campaign_builds,
-- etc.) — there is no mse_products table in this repo to reference; the
-- task spec's "FK to mse_products" doesn't match the real schema, so this
-- follows the established real pattern instead (see migration 024's own
-- mse_icp_configs for the same product_id UNIQUE shape).

CREATE TABLE IF NOT EXISTS mse_brevo_lists (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id     UUID NOT NULL UNIQUE,
  brevo_list_id  INTEGER NOT NULL,
  list_name      TEXT,
  tenant_id      UUID,
  created_at     TIMESTAMPTZ DEFAULT NOW(),
  updated_at     TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE mse_brevo_lists ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_brevo_lists_admin_access ON mse_brevo_lists
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
