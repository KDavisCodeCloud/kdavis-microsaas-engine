-- 074: lead-source expansion, Phase 3 -- SEC Form D (funding + buyer names)
-- (Kelvin's lead-sources brief, 2026-11-15). Code: agents/marketing/form_d.py.
--
-- 1. mse_form_d_filings: one row per Form D read from EDGAR's official daily
--    index + primary_doc.xml. Public filings, shared by every product, so
--    product_id is NULLable and NULL means "all products" (same convention as
--    mse_suppressed_companies). tenant_id is carried for the platform rule and
--    is NULL: these rows belong to no tenant.
-- 2. mse_form_d_index_days: which daily index files have been fully read, so
--    the job is incremental and a holiday (no file) is retried, not skipped.
-- 3. mse_leads: funding_date / funding_amount / funding_source /
--    form_d_accession. NULLable, nothing back-filled.
--
-- No column is dropped or altered and no row is rewritten.

CREATE TABLE IF NOT EXISTS mse_form_d_filings (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id             uuid REFERENCES mse_products(id),   -- NULL = shared by all products
  tenant_id              uuid,
  accession_no           text NOT NULL UNIQUE,
  cik                    text NOT NULL,
  form_type              text NOT NULL CHECK (form_type IN ('D', 'D/A')),
  filed_on               date NOT NULL,
  issuer_name            text NOT NULL,
  issuer_norm            text NOT NULL,
  entity_type            text,
  state                  text,
  year_of_inc            text,
  industry_group         text,
  total_offering_amount  numeric,
  total_amount_sold      numeric,
  date_of_first_sale     date,
  related_persons        jsonb NOT NULL DEFAULT '[]'::jsonb,
  filing_url             text,
  seed_checked_at        timestamptz,
  seed_board             text,
  created_at             timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE mse_form_d_filings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS mse_form_d_filings_service_role ON mse_form_d_filings;
CREATE POLICY mse_form_d_filings_service_role ON mse_form_d_filings FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

DROP POLICY IF EXISTS mse_form_d_filings_admin_select ON mse_form_d_filings;
CREATE POLICY mse_form_d_filings_admin_select ON mse_form_d_filings FOR SELECT
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

CREATE INDEX IF NOT EXISTS idx_mse_form_d_filings_norm ON mse_form_d_filings (issuer_norm, filed_on DESC);
CREATE INDEX IF NOT EXISTS idx_mse_form_d_filings_tech
  ON mse_form_d_filings (filed_on DESC) WHERE industry_group IN ('Computers', 'Other Technology', 'Telecommunications');

CREATE TABLE IF NOT EXISTS mse_form_d_index_days (
  day           date PRIMARY KEY,
  product_id    uuid REFERENCES mse_products(id),   -- NULL = shared
  tenant_id     uuid,
  entries       int,
  processed_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE mse_form_d_index_days ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS mse_form_d_index_days_service_role ON mse_form_d_index_days;
CREATE POLICY mse_form_d_index_days_service_role ON mse_form_d_index_days FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

DROP POLICY IF EXISTS mse_form_d_index_days_admin_select ON mse_form_d_index_days;
CREATE POLICY mse_form_d_index_days_admin_select ON mse_form_d_index_days FOR SELECT
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS funding_date date;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS funding_amount numeric;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS funding_source text;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS form_d_accession text;

COMMENT ON COLUMN mse_leads.funding_date IS 'Most recent funding date (Form D first sale, else filing date) -- intent signal.';
COMMENT ON COLUMN mse_leads.funding_source IS 'Where funding_date came from, e.g. sec_form_d.';
