-- Migration: Cloud Decoded ICP config for the self-hosted lead finder
-- (agents/marketing/mkt_lead_finder.py, api/routers/leads.py's
-- POST /marketing/leads/find + GET /marketing/leads/icp-products).
--
-- Kelvin's directive (2026-09-16): "the cloud decoded product needs a
-- lead finder also. needs to be added to the lead pipeline." Cloud
-- Decoded has deliberately had no mse_products row until now (see the
-- 20260914221640_icp_selling_stage.sql migration's own comment: separate
-- repo, separate Stripe account, "never Cloud Decoded" in the Stripe
-- Architecture rule) -- this is a scoped exception to that boundary, at
-- Kelvin's explicit request, and it stays scoped: this row exists only
-- so the lead-finder/ICP machinery (which already keys everything off
-- mse_products.id) has something to attach an ICP config to. It adds no
-- Stripe coupling, no billing linkage, no campaign_builds entry -- Cloud
-- Decoded's own checkout/subscription flow in kdavis-agentic-platform is
-- completely untouched.
--
-- ICP is the one already documented for this product in
-- kdavis-agentic-platform/CLAUDE.md's per-product design-personality
-- section: "Industry: B2B DevOps / Enterprise Infrastructure. Customer:
-- VP Engineering, Head of Platform." Locations kept broad (US, Remote)
-- rather than city-level -- this is a national/remote-first enterprise
-- SaaS sale, not a local-market product like the trades/real-estate
-- verticals this scraper also serves.
--
-- search_templates use the same {title}/{location} placeholders
-- scrapers/google_search.py's scrape() already requires (template.format
-- (title=title, location=location)) -- confirmed by reading that
-- function directly, not assumed. Two angles: a direct LinkedIn-profile
-- search for people holding the ICP title (the actual buyer), and a
-- company-site search for teams that plausibly run the kind of
-- infrastructure Cloud Decoded's 11-agent roster addresses (CI/CD,
-- Kubernetes, IAM, FinOps, drift detection) -- both land on real pages
-- google_search.py already knows how to extract a lead from (LinkedIn
-- metadata-only, or a company site's visible-text email).
--
-- min_company_size=100: "Enterprise Infrastructure" -- excludes
-- 5-person startups without fabricating an upper bound; Enterprise-tier
-- customers can be arbitrarily large. No max_company_size set.

INSERT INTO mse_products (slug, name, status, activation_definition) VALUES
  ('cloud-decoded', 'Cloud Decoded', 'active', 'Started a paid Stripe subscription (Starter/Growth/Enterprise)')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO mse_icp_configs (
  product_id, job_titles, locations, industries, search_templates,
  target_count, selling_stage, min_company_size
)
SELECT
  id,
  '["VP Engineering", "Head of Platform", "Director of Platform Engineering", "Head of Infrastructure", "VP Infrastructure"]'::jsonb,
  '["United States", "Remote"]'::jsonb,
  '["B2B DevOps", "Enterprise Infrastructure", "Cloud Infrastructure", "SaaS"]'::jsonb,
  '[
    "\"{title}\" {location} site:linkedin.com/in",
    "\"{title}\" \"platform team\" {location}",
    "\"{title}\" kubernetes incident response {location}"
  ]'::jsonb,
  100,
  'active',
  100
FROM mse_products WHERE slug = 'cloud-decoded'
ON CONFLICT (product_id) DO UPDATE SET
  job_titles = EXCLUDED.job_titles,
  locations = EXCLUDED.locations,
  industries = EXCLUDED.industries,
  search_templates = EXCLUDED.search_templates,
  target_count = EXCLUDED.target_count,
  selling_stage = EXCLUDED.selling_stage,
  min_company_size = EXCLUDED.min_company_size;
