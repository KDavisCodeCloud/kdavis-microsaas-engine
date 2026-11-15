-- 073: lead-source expansion, Phase 2 -- Hacker News post fields and the
-- poster as a candidate buyer (Kelvin's lead-sources brief, 2026-11-15).
--
-- Two NULLable jsonb columns on mse_leads. Nothing is dropped, altered or
-- back-filled; existing rows read NULL ("never parsed").
--
--   buyer_candidates  list of {name, title, email, email_grade, source,
--                     confidence, evidence, evidence_url, ...}. Written by the
--                     HN scout (source 'hn_poster') and, from Phase 5, by the
--                     buyer resolver (form_d / company_page / github /
--                     search_snippet). A candidate is a SUGGESTION: it never
--                     becomes the lead's contact without either a verified
--                     buyer-titled email (resolver) or a human confirm.
--   hn_post_fields    {remote, urls, emails, header} parsed from the post.
--
-- mse_leads already has RLS (product_id scoped, service-role policy first);
-- new columns inherit it, so no policy changes here.

ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS buyer_candidates jsonb;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS hn_post_fields jsonb;

COMMENT ON COLUMN mse_leads.buyer_candidates IS
  'Suggested buyers with source + confidence (HN poster, Form D officer, team page, GitHub, search snippet). Never auto-promoted without a verified email + buyer title or a human confirm.';
COMMENT ON COLUMN mse_leads.hn_post_fields IS
  'Fields parsed from a Hacker News hiring post: remote flag, URLs, contact emails, header line.';
