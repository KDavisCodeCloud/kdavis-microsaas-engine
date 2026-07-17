-- Migration 012: seed industry_color_map with MSE's ACTUAL research
-- verticals. The factory-expansion spec's seed data (finops, govtech,
-- creator, cloudops, retention, open) doesn't match any real vertical
-- name this system's research swarm actually produces — confirmed
-- against research.py's VALID_VERTICALS and a real live swarm run
-- 2026-07-17, which returned "HR / Ops / People Management", "Finance /
-- Accounting / Bookkeeping", etc. Without this, brief_generator's color
-- lookup would miss on every real opportunity and always fall back to
-- 'open'. Keeping the original generic entries too (harmless, and useful
-- if MSE's vertical list ever expands beyond these 6).

INSERT INTO industry_color_map (vertical, primary_accent, secondary_accent, mood, benchmark_brands) VALUES
  ('Healthcare / Medical Front Desk',    '#0ea5e9', '#16a34a', 'trusted/reassuring',   ARRAY['Kareo','athenahealth','SimplePractice']),
  ('Legal / Professional Services',      '#1d4ed8', '#dc2626', 'authoritative/precise', ARRAY['Clio','MyCase','PracticePanther']),
  ('E-commerce / Retail Ops',             '#7c3aed', '#f59e0b', 'energetic/operational', ARRAY['Shopify','ShipStation','Skubana']),
  ('Real Estate / Property Management',  '#2563eb', '#f97316', 'professional/warm',     ARRAY['AppFolio','Buildium','Rentec Direct']),
  ('HR / Ops / People Management',       '#6366f1', '#10b981', 'organized/human',       ARRAY['Gusto','Rippling','BambooHR']),
  ('Finance / Accounting / Bookkeeping', '#2563eb', '#16a34a', 'trusted/professional',  ARRAY['QuickBooks','Xero','Bench'])
ON CONFLICT (vertical) DO NOTHING;
