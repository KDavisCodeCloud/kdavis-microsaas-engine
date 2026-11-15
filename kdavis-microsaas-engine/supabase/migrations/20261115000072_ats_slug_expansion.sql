-- 072: lead-source expansion, Phase 1 -- ATS slug discovery at scale
-- (Kelvin's lead-sources brief, 2026-11-15).
--
-- The brief asks for an `ats_slugs` table (provider, slug, company domain,
-- first_seen, last_checked, active_role_count, status). That table already
-- exists as mse_ats_board_tokens (migration 057, 643 rows on 2026-11-15), and
-- every scout reads and writes it. A second table would be a copy that drifts,
-- so this migration:
--   1. widens the provider CHECK to admit Recruitee. SmartRecruiters is NOT
--      added: api.smartrecruiters.com/robots.txt disallows every agent but
--      LinkedInBot, which the brief makes a stop condition;
--   2. creates `ats_slugs` as a security_invoker VIEW with the brief's column
--      names, so the table's own RLS applies to anyone reading through it.
--
-- No column is dropped or altered and no row is rewritten. Widening a CHECK
-- cannot invalidate an existing row.

ALTER TABLE mse_ats_board_tokens DROP CONSTRAINT IF EXISTS mse_ats_board_tokens_provider_check;
ALTER TABLE mse_ats_board_tokens ADD CONSTRAINT mse_ats_board_tokens_provider_check
  CHECK (provider IN ('greenhouse', 'lever', 'ashby', 'workable', 'recruitee'));

CREATE OR REPLACE VIEW ats_slugs WITH (security_invoker = true) AS
SELECT
  id,
  provider,
  board_token          AS slug,
  company,
  company_domain,
  created_at           AS first_seen,
  last_fetched_at      AS last_checked,
  open_role_count      AS active_role_count,
  COALESCE(last_status, 'never_fetched') AS status,
  consecutive_failures,
  discovered_from,
  product_id           AS discovered_for_product_id
FROM mse_ats_board_tokens;

COMMENT ON VIEW ats_slugs IS
  'Lead-sources brief naming over mse_ats_board_tokens (the real table). security_invoker: the table''s RLS applies.';

-- Views carry no RLS of their own; with security_invoker the caller's rights
-- on mse_ats_board_tokens decide (service_role all; admin/hitl read).
REVOKE ALL ON ats_slugs FROM anon;
GRANT SELECT ON ats_slugs TO authenticated, service_role;
