-- Migration 052: canonical product identity reconciliation (2026-09-21,
-- W2.5 follow-up to migration 051's email-campaign session).
--
-- LOCKED DECISION (Kelvin): mse_products.id is canonical product identity
-- for all campaign/email tables and the positioning gate.
-- opportunity_pipeline rows map TO products; they never own campaign
-- identity.
--
-- Step 1 discovery (read-only, presented and confirmed before this file
-- was written) found FOUR unrelated "product_id" spaces in use across
-- this repo with no resolving FK between them: opportunity_pipeline.id
-- (what the live campaign trigger actually keys on), mse_products.id
-- (the DIST positioning registry -- already the FK target of
-- mse_positioning.product_id), mse_build_briefs.id, and whatever the 2
-- existing mse_email_sequences rows happened to be stamped with (one of
-- each of the first and third). This migration adds the mapping,
-- backfills the 2 confirmed matches, registers a real launched product
-- that had no mse_products row at all, repoints the 2 orphan rows, and
-- adds the FK that was previously impossible because they didn't
-- resolve to anything.

-- 1. Nullable opportunity_id on mse_products, same ON DELETE SET NULL
--    shape as mse_build_briefs.opportunity_id -- opportunity_pipeline is
--    a heavily-pruned table (confirmed only 4 rows remain of many
--    historical ones), so a product must never disappear because its
--    originating opportunity row was later archived/deleted.
ALTER TABLE mse_products
  ADD COLUMN IF NOT EXISTS opportunity_id UUID REFERENCES opportunity_pipeline(id) ON DELETE SET NULL;

-- 2. Backfill the 2 confirmed exact matches from the Step 1 discovery.
--    All 8 other mse_products rows stay NULL -- confirmed no_match
--    (hand-conceived Decoded Empire / internal / consumer products, or
--    TradesDesk sub-verticals with no dedicated opportunity row of
--    their own).
UPDATE mse_products SET opportunity_id = 'b1ebd730-a83b-48f8-96f0-e2701449677b'
  WHERE id = 'ccc66fa2-ce86-4eb7-9b7c-6cbae9404c2e' AND opportunity_id IS NULL;   -- tradesdesk

UPDATE mse_products SET opportunity_id = '9d26abed-7a8a-427f-8493-be487f7eb641'
  WHERE id = '5ba0639e-4c9c-4205-be3d-7904f4dbc9b5' AND opportunity_id IS NULL;   -- small-portfolio-hub

-- 3. Register Showing Signal -- a real, launched product (own repo, own
--    Stripe/domain setup -- referenced repeatedly in this repo's own
--    CLAUDE.md as the Domain/Stripe Architecture "reference
--    implementation"; opportunity_pipeline.id 797adb70, status
--    'launched'; build brief mse_build_briefs.cb6f9203) that was never
--    added to this registry, because it launched (~2026-08-08/09)
--    before mse_products existed (2026-08-30) and nobody backfilled it.
--    activation_definition is INFERRED from the brief's own pitch (build
--    briefs have no explicit activation_definition field) -- flagged for
--    Kelvin to correct if this specific wording is wrong.
INSERT INTO mse_products (id, slug, name, status, activation_definition, opportunity_id)
VALUES (
  '7e1c9343-be82-4da6-bf38-909f8665d55d',
  'showing-signal',
  'Showing Signal',
  'active',
  'First automated CRM/SMS follow-up fired from a ShowingTime event',
  '797adb70-f518-41e9-8a9c-d5141691076f'
)
ON CONFLICT (slug) DO NOTHING;

-- 4. Repoint the 2 orphan mse_email_sequences rows onto their confirmed
--    mse_products.id (previously stamped with an opportunity_pipeline.id
--    and an mse_build_briefs.id respectively -- see the migration
--    header and this session's discovery report).
UPDATE mse_email_sequences SET product_id = 'ccc66fa2-ce86-4eb7-9b7c-6cbae9404c2e'
  WHERE id = '29d8bab1-3253-4fad-8dc2-120066fcc899';   -- was mse_build_briefs.id 52cfbaf2... -> tradesdesk

UPDATE mse_email_sequences SET product_id = '7e1c9343-be82-4da6-bf38-909f8665d55d'
  WHERE id = '4817dba9-1b0f-4f90-9c99-65c4ae1bcec8';   -- was opportunity_pipeline.id 797adb70... -> showing-signal

-- 5. Validated FK -- now safe, both existing rows resolve to a real
--    mse_products.id. Fails this whole migration (and, per
--    core/migrate.py's all-or-nothing batch transaction, the whole
--    pending batch) rather than silently dropping or orphaning data if
--    anything doesn't resolve.
ALTER TABLE mse_email_sequences
  ADD CONSTRAINT mse_email_sequences_product_id_fkey
  FOREIGN KEY (product_id) REFERENCES mse_products(id);

-- 6. Read-only helper for Step 3's DB-level generation gate. Positioning
--    already keys directly on mse_products.id (mse_positioning.product_id
--    references mse_products(id) -- confirmed via migration
--    20260830000029, no opportunity-id indirection needed), so this is a
--    convenience wrapper, not a resolution layer.
CREATE OR REPLACE FUNCTION has_approved_positioning(p_product_id UUID)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.mse_positioning
    WHERE product_id = p_product_id AND status = 'approved'
  );
$$;
