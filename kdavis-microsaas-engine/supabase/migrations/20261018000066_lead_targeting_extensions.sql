-- Lead targeting extensions (Kelvin, 2026-10-18). Four additions:
--
--   1. PAIN-PHRASE INTENT BOOST. Phrases live on mse_icp_configs.pain_phrases,
--      not in code, so tuning the list is a data edit. Matched phrases (and
--      the sentence each was found in) are stored on the lead and handed to
--      MKT-O2, so touch 1 can name the exact pain from the posting.
--   2. PARTNER LANE. A separate product row ("Referral Partners") with its
--      own ICP, its own leads, its own Outreach lane and its own Loop Map
--      desks. AI agencies, fractional CISOs and compliance/audit firms pass
--      the consultancy exclusion ONLY here.
--   3. VC OPERATING PARTNERS. A curated fund list (mse_vc_funds) and the
--      portfolio companies read from each fund's own site
--      (mse_vc_portfolio_companies). Portfolio companies with an open infra
--      role get mse_leads.funded_by and an intent boost.
--   4. HACKER NEWS. source='hn_thread', with the post text on the lead.
--
-- Additive only: new columns are nullable, CHECK lists are widened, never
-- narrowed.

-- ── mse_leads ────────────────────────────────────────────────────────────
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS pain_phrases text[];
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS pain_evidence jsonb;
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS funded_by text[];
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS partner_type text;
-- The text a human reads to understand the lead: the HN post, or the
-- partner firm's own description of itself. Separate from notes, which is
-- free-form and human-written.
ALTER TABLE mse_leads ADD COLUMN IF NOT EXISTS signal_text text;

COMMENT ON COLUMN mse_leads.pain_phrases IS
  'mse_icp_configs.pain_phrases entries found in this company''s job description text.';
COMMENT ON COLUMN mse_leads.pain_evidence IS
  '[{phrase, snippet}] -- the sentence each pain phrase was found in, verbatim from the posting.';
COMMENT ON COLUMN mse_leads.funded_by IS
  'mse_vc_funds.name for every curated fund whose own portfolio page lists this company.';
COMMENT ON COLUMN mse_leads.partner_type IS
  'Partner lane only: ai_agency | fractional_ciso | compliance_firm | vc_operating_partner.';

ALTER TABLE mse_leads DROP CONSTRAINT IF EXISTS mse_leads_partner_type_check;
ALTER TABLE mse_leads ADD CONSTRAINT mse_leads_partner_type_check CHECK (
  partner_type IS NULL OR partner_type = ANY (ARRAY[
    'ai_agency', 'fractional_ciso', 'compliance_firm', 'vc_operating_partner']));

ALTER TABLE mse_leads DROP CONSTRAINT IF EXISTS mse_leads_source_check;
ALTER TABLE mse_leads ADD CONSTRAINT mse_leads_source_check CHECK (source = ANY (ARRAY[
  'google_search', 'brave_search', 'real_estate_db', 'linkedin_manual', 'linkedin_engager',
  'job_posting_signal', 'cloud_decoded_job_signal',
  'hn_thread', 'partner_signal', 'vc_operating_partner']));

ALTER TABLE mse_dm_sequences DROP CONSTRAINT IF EXISTS mse_dm_sequences_lead_source_check;
ALTER TABLE mse_dm_sequences ADD CONSTRAINT mse_dm_sequences_lead_source_check CHECK (lead_source = ANY (ARRAY[
  'apollo', 'linkedin_manual', 'linkedin_engager', 'lead_finder', 'job_posting_signal',
  'cloud_decoded_job_signal', 'hn_thread', 'partner_signal', 'vc_operating_partner']));

CREATE INDEX IF NOT EXISTS idx_mse_leads_source_product ON mse_leads(source, product_id);

-- ── 1. Pain phrases ──────────────────────────────────────────────────────
ALTER TABLE mse_icp_configs ADD COLUMN IF NOT EXISTS pain_phrases jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE mse_icp_configs ADD COLUMN IF NOT EXISTS pain_phrase_boost jsonb NOT NULL
  DEFAULT '{"per_match": 0.10, "max": 0.20}'::jsonb;
COMMENT ON COLUMN mse_icp_configs.pain_phrases IS
  'Phrases scanned for in job description text. A match raises intent_score by '
  'pain_phrase_boost.per_match each, capped at pain_phrase_boost.max.';

-- Seeded for the two outbound products only, by id.
UPDATE mse_icp_configs SET pain_phrases = '[
  "ARM to Bicep", "Bicep migration", "Terraform migration", "tech debt",
  "pipeline modernization", "GitHub Actions migration", "Azure DevOps to GitHub",
  "Key Vault", "secrets management", "IaC standardization", "cloud cost reduction",
  "SOC 2 readiness", "landing zone"
]'::jsonb
WHERE product_id IN ('9b6c8f36-985f-4d7a-a416-c40da89e23af', '777a1852-f84c-49d8-890e-cd14670b7f6f')
  AND pain_phrases = '[]'::jsonb;

-- ── 2. Partner lane: its own product ─────────────────────────────────────
INSERT INTO mse_products (id, slug, name, status)
VALUES ('c0e8aaf3-e858-4df4-b0ba-863580556a18', 'thd-partners', 'Referral Partners', 'active')
ON CONFLICT (id) DO NOTHING;

-- selling_stage='active' so MKT-O2 drafts partner notes. outbound_enabled
-- stays FALSE: that flag authorises the weekly ATS sweep, which is the
-- prospect pipeline and must never run against this lane.
-- search_templates: the Brave queries per partner segment. Data, so the
-- query list is tuned without a deploy.
INSERT INTO mse_icp_configs (product_id, vertical, selling_stage, outbound_enabled, job_titles,
                             exclude_criteria, search_templates, target_count)
VALUES ('c0e8aaf3-e858-4df4-b0ba-863580556a18', 'partners', 'active', false,
        '["Founder", "Co-Founder", "Managing Partner", "CEO", "Principal", "Owner", "Operating Partner", "Talent Partner", "Head of Platform"]'::jsonb,
        '{"exclude_tags": ["staffing"], "note": "Consultancies, MSPs and cloud partners are the TARGET in this lane, not an exclusion."}'::jsonb,
        '[
          {"segment": "ai_agency",       "query": "\"AI automation agency\" founder"},
          {"segment": "ai_agency",       "query": "\"AI development agency\" \"our team\""},
          {"segment": "ai_agency",       "query": "\"AI agency\" \"we build\" agents automation founder"},
          {"segment": "ai_agency",       "query": "\"generative AI\" consultancy \"managing partner\""},
          {"segment": "fractional_ciso", "query": "\"fractional CISO\" services firm founder"},
          {"segment": "fractional_ciso", "query": "\"fractional CISO\" \"managing partner\""},
          {"segment": "fractional_ciso", "query": "\"vCISO services\" startups founder"},
          {"segment": "compliance_firm", "query": "\"SOC 2 audit\" CPA firm \"our team\""},
          {"segment": "compliance_firm", "query": "\"SOC 2 readiness\" consulting firm founder"},
          {"segment": "compliance_firm", "query": "\"HIPAA compliance\" consulting firm founder"},
          {"segment": "compliance_firm", "query": "\"ISO 27001\" certification consultancy founder"}
        ]'::jsonb,
        40)
ON CONFLICT (product_id) DO NOTHING;

-- ── 3. VC funds ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mse_vc_funds (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id        uuid NOT NULL DEFAULT 'c0e8aaf3-e858-4df4-b0ba-863580556a18' REFERENCES mse_products(id),
    tenant_id         uuid,
    name              text NOT NULL UNIQUE,
    domain            text NOT NULL,
    team_url          text,
    portfolio_url     text,
    stage_focus       text,
    active            boolean NOT NULL DEFAULT true,
    -- What the last read of the fund's own pages established, so a fund whose
    -- team page renders in JavaScript shows as "nothing readable" rather than
    -- silently contributing zero.
    last_team_status      text,
    last_portfolio_status text,
    last_checked_at   timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE mse_vc_funds ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mse_vc_funds_service_role ON mse_vc_funds;
CREATE POLICY mse_vc_funds_service_role ON mse_vc_funds FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
DROP POLICY IF EXISTS mse_vc_funds_admin_select ON mse_vc_funds;
CREATE POLICY mse_vc_funds_admin_select ON mse_vc_funds FOR SELECT
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

CREATE TABLE IF NOT EXISTS mse_vc_portfolio_companies (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id   uuid NOT NULL DEFAULT 'c0e8aaf3-e858-4df4-b0ba-863580556a18' REFERENCES mse_products(id),
    tenant_id    uuid,
    fund_id      uuid NOT NULL REFERENCES mse_vc_funds(id) ON DELETE CASCADE,
    company      text,
    domain       text NOT NULL,
    -- ATS board found for this company, if any ("greenhouse:acme").
    board_key    text,
    board_status text,  -- ok | empty | none_found | not_probed
    infra_roles  integer,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (fund_id, domain)
);
CREATE INDEX IF NOT EXISTS idx_mse_vc_portfolio_domain ON mse_vc_portfolio_companies(domain);
ALTER TABLE mse_vc_portfolio_companies ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mse_vc_portfolio_service_role ON mse_vc_portfolio_companies;
CREATE POLICY mse_vc_portfolio_service_role ON mse_vc_portfolio_companies FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
DROP POLICY IF EXISTS mse_vc_portfolio_admin_select ON mse_vc_portfolio_companies;
CREATE POLICY mse_vc_portfolio_admin_select ON mse_vc_portfolio_companies FOR SELECT
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

-- Curated: seed-to-Series-B funds that back B2B SaaS / infrastructure
-- companies. URLs are each fund's own team and portfolio pages, checked by
-- hand on 2026-10-18. Edit rows here, not code.
INSERT INTO mse_vc_funds (name, domain, team_url, portfolio_url, stage_focus) VALUES
  ('Amplify Partners',        'amplifypartners.com',     'https://amplifypartners.com/team',          NULL,                                         'seed-A, infrastructure/devtools'),
  ('Uncork Capital',          'uncorkcapital.com',       'https://uncorkcapital.com/team',            'https://uncorkcapital.com/companies',        'seed'),
  ('Wing Venture Capital',    'wing.vc',                 'https://wing.vc/team',                      'https://wing.vc/portfolio',                  'seed-B, enterprise'),
  ('Craft Ventures',          'craftventures.com',       'https://craftventures.com/team',            'https://craftventures.com/portfolio',        'seed-B, B2B SaaS'),
  ('NextView Ventures',       'nextview.vc',             'https://nextview.vc/team',                  'https://nextview.vc/portfolio',              'seed'),
  ('Scale Venture Partners',  'scalevp.com',             'https://scalevp.com/team',                  'https://scalevp.com/portfolio',              'A-B, B2B SaaS'),
  ('Emergence Capital',       'emcap.com',               'https://emcap.com/team',                    'https://emcap.com/portfolio',                'early, B2B SaaS'),
  ('Heavybit',                'heavybit.com',            'https://heavybit.com/team',                 'https://heavybit.com/portfolio',             'seed, developer tools'),
  ('Unusual Ventures',        'unusual.vc',              'https://unusual.vc/team',                   'https://unusual.vc/portfolio',               'seed-A, enterprise'),
  ('Matrix',                  'matrix.vc',               'https://matrix.vc/team',                    'https://matrix.vc/portfolio',                'seed-A'),
  ('Point Nine',              'pointnine.com',           'https://pointnine.com/team',                NULL,                                         'seed, B2B SaaS'),
  ('Glasswing Ventures',      'glasswing.vc',            'https://glasswing.vc/team',                 NULL,                                         'seed-A, AI/enterprise'),
  ('Bain Capital Ventures',   'baincapitalventures.com', 'https://baincapitalventures.com/team/?team=platform', 'https://baincapitalventures.com/portfolio', 'seed-B, B2B'),
  ('Kleiner Perkins',         'kleinerperkins.com',      'https://www.kleinerperkins.com/people',     'https://www.kleinerperkins.com/partnerships', 'seed-B'),
  ('Lightspeed',              'lsvp.com',                'https://lsvp.com/team',                     'https://lsvp.com/portfolio',                 'seed-B, enterprise'),
  ('Battery Ventures',        'battery.com',             'https://www.battery.com/people',            NULL,                                         'seed-B, software'),
  ('Boldstart Ventures',      'boldstart.vc',            'https://boldstart.vc/team',                 'https://boldstart.vc/portfolio',             'pre-seed-A, developer-first B2B'),
  ('Work-Bench',              'work-bench.com',          'https://work-bench.com/team',               'https://work-bench.com/portfolio',           'seed, enterprise'),
  ('Costanoa Ventures',       'costanoa.vc',             'https://costanoa.vc/about',                 'https://costanoa.vc/portfolio',              'seed-A, B2B'),
  ('First Round Capital',     'firstround.com',          'https://firstround.com/team',               'https://firstround.com/companies',           'seed'),
  ('Felicis',                 'felicis.com',             'https://felicis.com/team',                  'https://felicis.com/portfolio',              'seed-B'),
  ('Crosslink Capital',       'crosslinkcapital.com',    'https://crosslinkcapital.com/team',         'https://crosslinkcapital.com/portfolio',     'early, B2B'),
  ('Basis Set Ventures',      'basisset.com',            'https://basisset.com/team',                 'https://basisset.com/portfolio',             'seed, B2B AI'),
  ('Founder Collective',      'foundercollective.com',   'https://foundercollective.com/team',        'https://foundercollective.com/portfolio',    'seed'),
  ('Decibel',                 'decibel.vc',              'https://decibel.vc/team',                   NULL,                                         'seed, enterprise infrastructure')
ON CONFLICT (name) DO NOTHING;

-- ── Loop Map: the partner lane's own desk set ────────────────────────────
ALTER TABLE mse_loop_products DROP CONSTRAINT IF EXISTS mse_loop_products_playbook_check;
ALTER TABLE mse_loop_products ADD CONSTRAINT mse_loop_products_playbook_check
  CHECK (playbook = ANY (ARRAY['signal_outbound','community_search','content','distributor','partner']));
ALTER TABLE mse_loop_stations DROP CONSTRAINT IF EXISTS mse_loop_stations_playbook_check;
ALTER TABLE mse_loop_stations ADD CONSTRAINT mse_loop_stations_playbook_check
  CHECK (playbook = ANY (ARRAY['signal_outbound','community_search','content','distributor','partner']));
ALTER TABLE mse_loop_playbook_templates DROP CONSTRAINT IF EXISTS mse_loop_playbook_templates_playbook_check;
ALTER TABLE mse_loop_playbook_templates ADD CONSTRAINT mse_loop_playbook_templates_playbook_check
  CHECK (playbook = ANY (ARRAY['signal_outbound','community_search','content','distributor','partner']));

INSERT INTO mse_loop_playbook_templates (playbook, step_order, name, owner, description, next_action,
    status_rule_key, lane_link)
VALUES
  ('partner', 1, 'Find partner firms', 'auto', 'Brave search plus each firm''s own site finds AI agencies, fractional CISOs and compliance/audit firms. Curated VC funds'' own team pages find operating, platform and talent partners.', 'Nothing. Run it from the Partners lane.', 'outbound.lead_run', '/marketing#partners'),
  ('partner', 2, 'Qualify the firm', 'auto', 'Keeps firms whose own site says what they do. Staffing firms are dropped; consultancies are the target here, not an exclusion.', 'Nothing.', 'outbound.qualify', '/marketing#partners'),
  ('partner', 3, 'Name the founder', 'you', 'Founder or managing partner, read from the firm''s own team or about page. When the site doesn''t say, you name them.', 'Paste the founder''s LinkedIn profile, name and title.', 'outbound.find_buyer', '/marketing#partners'),
  ('partner', 4, 'Write the partnership note', 'auto', 'A partnership note, not a sales sequence: the implementation partner their clients need after an audit, or their white-label cloud and IAM arm.', 'Nothing.', 'outbound.draft_enrollment', '/marketing#partners'),
  ('partner', 5, 'Approve', 'you', 'Nothing goes out until you approve it.', 'Approve or edit the note in the Partners lane.', 'outbound.approve', '/marketing#partners'),
  ('partner', 6, 'Send on LinkedIn', 'you', 'Manual LinkedIn by default. You paste the note from the Partners lane.', 'Paste it, then mark it sent.', 'partner.sent', '/marketing#partners'),
  ('partner', 7, 'They reply', 'you', 'Marked by hand when a partner answers.', 'Mark the reply.', 'outbound.replies', '/marketing#outreach'),
  ('partner', 8, 'Intro call', 'you', 'A call to agree how referrals work.', 'Mark the call.', 'outbound.calls_booked', '/marketing#outreach'),
  ('partner', 9, 'Referral agreement', 'you', 'A partner agrees to send you work.', 'Mark it won.', 'partner.agreed', '/marketing#outreach')
ON CONFLICT (playbook, step_order) DO NOTHING;

INSERT INTO mse_loop_products (product_id, tab_order, slug, display_name, playbook, playbook_line,
    verdict_tone, verdict_title, verdict_body, math, also)
VALUES ('c0e8aaf3-e858-4df4-b0ba-863580556a18', 7, 'partners', 'Partners', 'partner',
        'Playbook: referral partners · offer: implementation partner after an audit, white-label cloud and IAM arm',
        'warn', 'Warm intros beat cold email, if partners say yes.',
        'An audit firm, a fractional CISO or an AI agency already has the client and the deadline. One partner who refers twice a year is worth more than a hundred cold sends. The lane is new: nothing has been sent yet.',
        '[["Partner firms found per run", "~10–20"], ["Replies to a partnership note", "10–20%"], ["Referral agreements in 90 days", "1–3"], ["Referrals per partner per year", "1–2"]]'::jsonb,
        '[["you", "VC platform teams: curated list of 25 funds"]]'::jsonb)
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO mse_loop_stations (product_id, step_order, name, owner, description, next_action,
                               status_rule_key, lane_link, playbook)
SELECT 'c0e8aaf3-e858-4df4-b0ba-863580556a18', step_order, name, owner, description, next_action,
       status_rule_key, lane_link, playbook
  FROM mse_loop_playbook_templates WHERE playbook = 'partner'
ON CONFLICT (product_id, step_order) DO NOTHING;
