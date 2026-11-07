-- 068: buyer-targeting fix, phase 1 (Kelvin, 2026-11-07).
--
-- 1. Soft-purge statuses. Widening CHECK constraints only: no column is
--    dropped or altered and no row changes here.
--      mse_leads.status            + 'rejected_wrong_icp'
--      mse_dm_sequences.status     + 'archived_wrong_icp'
--      mse_email_suppressions.reason + 'wrong_icp'
-- 2. mse_suppressed_companies: company-level suppression. Until now dedup
--    was contact-level (email / linkedin_url) plus an implicit "a row with
--    this domain exists" check that only covered the job-signal sources and
--    vanished if a row was ever cleaned up. A suppressed domain (or
--    normalised company name) is never ingested again for that product, or
--    for any product when product_id IS NULL.

ALTER TABLE mse_leads DROP CONSTRAINT IF EXISTS mse_leads_status_check;
ALTER TABLE mse_leads ADD CONSTRAINT mse_leads_status_check CHECK (status = ANY (ARRAY[
  'company_qualified', 'pending_dm', 'pending_email', 'contacted', 'converted',
  'unsubscribed', 'bounced', 'disqualified', 'rejected_wrong_icp']));

ALTER TABLE mse_dm_sequences DROP CONSTRAINT IF EXISTS mse_dm_sequences_status_check;
ALTER TABLE mse_dm_sequences ADD CONSTRAINT mse_dm_sequences_status_check CHECK (status = ANY (ARRAY[
  'awaiting_contact', 'pending_hitl', 'approved_hitl', 'approved_manual', 'rejected_hitl',
  'touch_1_sending', 'touch_1_sent', 'touch_2_sending', 'sequence_complete', 'replied',
  'suppressed', 'approval_expired', 'archived_wrong_icp']));

ALTER TABLE mse_email_suppressions DROP CONSTRAINT IF EXISTS mse_email_suppressions_reason_check;
ALTER TABLE mse_email_suppressions ADD CONSTRAINT mse_email_suppressions_reason_check CHECK (reason = ANY (ARRAY[
  'unsubscribed', 'bounced', 'complained', 'manual', 'wrong_icp']));

CREATE TABLE IF NOT EXISTS mse_suppressed_companies (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id    uuid REFERENCES mse_products(id),   -- NULL = every product
    tenant_id     uuid,
    domain        text,
    company_norm  text,
    reason        text NOT NULL,
    source        text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (domain IS NOT NULL OR company_norm IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS mse_suppressed_companies_domain_once
    ON mse_suppressed_companies (COALESCE(product_id, '00000000-0000-0000-0000-000000000000'::uuid), domain)
    WHERE domain IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS mse_suppressed_companies_name_once
    ON mse_suppressed_companies (COALESCE(product_id, '00000000-0000-0000-0000-000000000000'::uuid), company_norm)
    WHERE domain IS NULL;
ALTER TABLE mse_suppressed_companies ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mse_suppressed_companies_service_role ON mse_suppressed_companies;
CREATE POLICY mse_suppressed_companies_service_role ON mse_suppressed_companies FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
DROP POLICY IF EXISTS mse_suppressed_companies_admin_select ON mse_suppressed_companies;
CREATE POLICY mse_suppressed_companies_admin_select ON mse_suppressed_companies FOR SELECT
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
