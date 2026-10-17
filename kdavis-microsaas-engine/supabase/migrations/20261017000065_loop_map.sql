-- Loop Map (CEO Decoded tab, 2026-10-17).
--
-- One "office floor" per product: stations (desks) in process order, Kelvin's
-- manual check marks per lap, the holes he logs, and an hourly snapshot of each
-- station's LIVE system status.
--
-- Two things never share a column, by design:
--   * system status  -- computed at request time by api/routers/loop_map.py from
--                       status_rule_key against real production rows, and
--                       snapshotted hourly into mse_loop_status_history;
--   * manual progress -- mse_loop_progress, written only by Kelvin's clicks.
-- A desk's color is the first, its check mark is the second. Neither can
-- overwrite the other because they live in different tables.
--
-- product_id = mse_products.id (canonical) on every table; tenant_id carried
-- nullable like mse_outreach_conversations (platform rule 1). RLS: service role
-- first, then admin only -- canonical role value 'admin', never 'owner'
-- (CLAUDE.md rule 9). Admin gets NO delete policy on notes: holes are resolved
-- and reopened, never deleted.

-- ---------------------------------------------------------------- products
CREATE TABLE IF NOT EXISTS mse_loop_products (
    product_id     uuid PRIMARY KEY REFERENCES mse_products(id) ON DELETE CASCADE,
    tenant_id      uuid,
    tab_order      integer NOT NULL DEFAULT 100,
    slug           text NOT NULL UNIQUE,
    display_name   text NOT NULL,
    playbook       text NOT NULL CHECK (playbook = ANY (ARRAY['signal_outbound','community_search','content','distributor'])),
    playbook_line  text,
    -- "Does this strategy work?" card. Editorial judgement, not system status.
    verdict_tone   text CHECK (verdict_tone IS NULL OR verdict_tone = ANY (ARRAY['ok','warn','stop','you','none'])),
    verdict_title  text,
    verdict_body   text,
    math           jsonb NOT NULL DEFAULT '[]'::jsonb,
    also           jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- stations
CREATE TABLE IF NOT EXISTS mse_loop_stations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id      uuid NOT NULL REFERENCES mse_products(id) ON DELETE CASCADE,
    tenant_id       uuid,
    step_order      integer NOT NULL CHECK (step_order >= 1),
    name            text NOT NULL,
    owner           text NOT NULL CHECK (owner = ANY (ARRAY['auto','you'])),
    description     text,
    next_action     text,
    -- Which evaluator in api/routers/loop_map.py computes this desk's status.
    -- An unknown key evaluates to 'unknown', never to a green.
    status_rule_key text NOT NULL,
    lane_link       text,
    playbook        text NOT NULL CHECK (playbook = ANY (ARRAY['signal_outbound','community_search','content','distributor'])),
    -- The reference design's short stat line, shown only when the live
    -- evaluator has no count of its own.
    target_hint     text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (product_id, step_order)
);

-- -------------------------------------------------------- playbook templates
-- A new product gets its stations by picking a playbook:
--   SELECT mse_loop_apply_playbook('<mse_products.id>', 'community_search');
-- (or POST /marketing/loop-map/products/{id}/playbook). No code change.
CREATE TABLE IF NOT EXISTS mse_loop_playbook_templates (
    playbook        text NOT NULL CHECK (playbook = ANY (ARRAY['signal_outbound','community_search','content','distributor'])),
    step_order      integer NOT NULL CHECK (step_order >= 1),
    name            text NOT NULL,
    owner           text NOT NULL CHECK (owner = ANY (ARRAY['auto','you'])),
    description     text,
    next_action     text,
    status_rule_key text NOT NULL,
    lane_link       text,
    PRIMARY KEY (playbook, step_order)
);

-- ---------------------------------------------------------------- progress
-- One row per check mark. Current lap = (laps recorded for the product) + 1.
-- Undo deletes the row for the CURRENT lap only; finished laps are history.
CREATE TABLE IF NOT EXISTS mse_loop_progress (
    product_id    uuid NOT NULL REFERENCES mse_products(id) ON DELETE CASCADE,
    tenant_id     uuid,
    lap           integer NOT NULL CHECK (lap >= 1),
    step_order    integer NOT NULL CHECK (step_order >= 1),
    completed_at  timestamptz NOT NULL DEFAULT now(),
    completed_by  text,
    PRIMARY KEY (product_id, lap, step_order)
);

CREATE TABLE IF NOT EXISTS mse_loop_laps (
    product_id                uuid NOT NULL REFERENCES mse_products(id) ON DELETE CASCADE,
    tenant_id                 uuid,
    lap                       integer NOT NULL CHECK (lap >= 1),
    completed_at              timestamptz NOT NULL DEFAULT now(),
    open_holes_at_completion  integer NOT NULL DEFAULT 0,
    PRIMARY KEY (product_id, lap)
);

-- ------------------------------------------------------------------- notes
CREATE TABLE IF NOT EXISTS mse_loop_notes (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id   uuid NOT NULL REFERENCES mse_products(id) ON DELETE CASCADE,
    tenant_id    uuid,
    step_order   integer NOT NULL CHECK (step_order >= 1),
    lap          integer NOT NULL CHECK (lap >= 1),
    kind         text NOT NULL CHECK (kind = ANY (ARRAY['hole','note'])),
    text         text NOT NULL CHECK (char_length(text) BETWEEN 1 AND 2000),
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   text,
    resolved_at  timestamptz
);
CREATE INDEX IF NOT EXISTS mse_loop_notes_product_idx ON mse_loop_notes (product_id, step_order, created_at DESC);

-- ---------------------------------------------------------- status history
CREATE TABLE IF NOT EXISTS mse_loop_status_history (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id   uuid NOT NULL REFERENCES mse_products(id) ON DELETE CASCADE,
    tenant_id    uuid,
    step_order   integer NOT NULL,
    status       text NOT NULL CHECK (status = ANY (ARRAY['working','not_proven','waiting_on_you','blocked','not_built','unknown'])),
    count        integer,
    evidence     text,
    captured_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mse_loop_status_history_idx ON mse_loop_status_history (product_id, step_order, captured_at DESC);

-- --------------------------------------------------------------------- RLS
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['mse_loop_products','mse_loop_stations','mse_loop_playbook_templates',
                           'mse_loop_progress','mse_loop_laps','mse_loop_notes','mse_loop_status_history']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_service_role', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR ALL USING (auth.role() = ''service_role'') WITH CHECK (auth.role() = ''service_role'')',
                   t || '_service_role', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_admin_select', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT USING ((auth.jwt() -> ''app_metadata'' ->> ''role'') = ''admin'')',
                   t || '_admin_select', t);
  END LOOP;
END $$;

-- Admin writes: progress (check / undo), laps, notes (insert + resolve only).
DROP POLICY IF EXISTS mse_loop_progress_admin_insert ON mse_loop_progress;
CREATE POLICY mse_loop_progress_admin_insert ON mse_loop_progress FOR INSERT
    WITH CHECK ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
DROP POLICY IF EXISTS mse_loop_progress_admin_delete ON mse_loop_progress;
CREATE POLICY mse_loop_progress_admin_delete ON mse_loop_progress FOR DELETE
    USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
DROP POLICY IF EXISTS mse_loop_laps_admin_insert ON mse_loop_laps;
CREATE POLICY mse_loop_laps_admin_insert ON mse_loop_laps FOR INSERT
    WITH CHECK ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
DROP POLICY IF EXISTS mse_loop_notes_admin_insert ON mse_loop_notes;
CREATE POLICY mse_loop_notes_admin_insert ON mse_loop_notes FOR INSERT
    WITH CHECK ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
DROP POLICY IF EXISTS mse_loop_notes_admin_update ON mse_loop_notes;
CREATE POLICY mse_loop_notes_admin_update ON mse_loop_notes FOR UPDATE
    USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')
    WITH CHECK ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

-- ------------------------------------------------------- apply a playbook
CREATE OR REPLACE FUNCTION mse_loop_apply_playbook(p_product_id uuid, p_playbook text)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE n integer;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM mse_loop_playbook_templates WHERE playbook = p_playbook) THEN
    RAISE EXCEPTION 'Unknown loop playbook: %', p_playbook;
  END IF;
  IF EXISTS (SELECT 1 FROM mse_loop_stations WHERE product_id = p_product_id) THEN
    RAISE EXCEPTION 'Product % already has loop stations; edit them instead of re-applying a playbook', p_product_id;
  END IF;
  INSERT INTO mse_loop_stations (product_id, step_order, name, owner, description, next_action,
                                 status_rule_key, lane_link, playbook)
  SELECT p_product_id, step_order, name, owner, description, next_action, status_rule_key, lane_link, playbook
    FROM mse_loop_playbook_templates WHERE playbook = p_playbook;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;
REVOKE ALL ON FUNCTION mse_loop_apply_playbook(uuid, text) FROM PUBLIC;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    EXECUTE 'GRANT EXECUTE ON FUNCTION mse_loop_apply_playbook(uuid, text) TO service_role';
  END IF;
END $$;

-- Seed: generated from the P array in docs/design/loop-map-reference.html
-- (kdavis-agentic-platform). Text is copied verbatim; status is NOT seeded --
-- it is computed live by api/routers/loop_map.py from status_rule_key.

INSERT INTO mse_loop_products (product_id, tab_order, slug, display_name, playbook, playbook_line,
    verdict_tone, verdict_title, verdict_body, math, also)
VALUES
    ('9b6c8f36-985f-4d7a-a416-c40da89e23af', 1, 'consulting', 'Consulting', 'signal_outbound', 'Playbook: signal outbound · offer: contract platform work while they hire', 'ok', 'Right strategy, and your fastest money.', 'A company posting a DevOps role has a live problem you solve. The loop is built. What''s missing is the two human steps, a priced offer, and warm channels (past colleagues, Upwork, Contra), which convert far better than cold.', '[["Reachable buyers / month (if Find the Buyer is cleared)", "~40"], ["Replies at 3\u20138%", "1\u20133"], ["Calls", "~1 / month"], ["Close rate on calls", "~25%"], ["Engagements from cold alone", "1 every 3\u20134 mo"], ["With warm channels + 100 buyers/mo", "~1 / month"]]'::jsonb, '[["you", "Warm network: not started"], ["none", "Upwork / Contra profiles: not built"], ["ok", "LinkedIn posts Mon\u2013Fri: automated", "linkedin.posts"]]'::jsonb),
    ('777a1852-f84c-49d8-890e-cd14670b7f6f', 2, 'cloud', 'Cloud Decoded', 'signal_outbound', 'Playbook: signal outbound + demo · $299 / $699 / $2,499 per month', 'warn', 'Right strategy, slower cycle.', 'Engineering leaders rarely connect cloud credentials off a cold email. Expect 1–3 months from first touch to paid. The fastest first customers usually come from offering 2–3 hand-held pilots to people who reply.', '[["Reachable buyers / month (shared lead engine)", "~40"], ["Replies at 3\u20138%", "1\u20133"], ["Demos", "1\u20132 / month"], ["Paid in first 90 days", "0\u20131"], ["Value per customer", "$299\u2013699/mo"]]'::jsonb, '[["ok", "LinkedIn posts Mon\u2013Fri: automated", "linkedin.posts"], ["ok", "Public demo at /demo: live"]]'::jsonb),
    ('5ba0639e-4c9c-4205-be3d-7904f4dbc9b5', 3, 'sph', 'Small Portfolio Hub', 'community_search', 'Playbook: community + search · self-serve $99 / $149 / $199', 'stop', 'Right playbook, but nothing is running.', 'Landlords don''t post DevOps jobs, so outbound is correctly off for SPH. Its buyers gather in landlord Facebook groups, BiggerPockets and search. None of those channels has started.', '[["Helpful posts in landlord groups", "2\u20133 / week"], ["Comparison pages published", "0 \u2192 5"], ["Realistic first customer", "30\u201360 days of steady posting"], ["Customers in 90 days", "1\u20133"], ["Value per customer", "$99\u2013199/mo"]]'::jsonb, '[["none", "Landlord Facebook groups: not started"], ["none", "BiggerPockets: not started"], ["stop", "Search Console tracking: blocked"]]'::jsonb),
    ('da79dcfa-4ecc-4a3b-9c3f-829ac3220ec9', 4, 'd6', 'DecodedSix', 'content', 'Playbook: content · earns from ads + affiliate, not sales', 'warn', 'Right model, and it''s a launch bet.', 'Money comes from traffic. September showed the site is indexed but not ranked, which is normal for a young domain. GTA 6 launches Nov 19. That''s the window.', '[["Sessions in September", "~121"], ["Monthly sessions needed for premium ads", "50,000"], ["Realistic launch-window income", "$200\u20133K/mo"], ["Upside", "Launch-day articles + map rank"]]'::jsonb, '[["warn", "Reddit: commenting phase, no posts yet"], ["stop", "X: account suspended"], ["ok", "Amazon affiliate: live"]]'::jsonb),
    ('96e169fe-c9bf-4e3f-b152-37f9f3b83004', 5, 'ada', 'ADA Title II', 'signal_outbound', 'Playbook: signal outbound to public entities · deadline-driven', 'ok', 'Outbound fits this one well.', 'The buyers are public: cities, counties, school and special districts, each with a published ADA coordinator or IT contact. The compliance deadline gives them a reason to act now.', '[["Expected results", "Set at launch"]]'::jsonb, '[]'::jsonb),
    ('2f2ff5c8-23d7-40ac-bf5e-98b92c72adbb', 6, 'bible', 'Bible apps', 'distributor', 'Playbook: distributor · pastors bring whole congregations', 'warn', 'Use pastors, not cold outreach.', 'One pastor or ministry leader who says yes brings a whole group. Your free-access-for-groups feature is the hook.', '[["Expected results", "Set at launch"]]'::jsonb, '[]'::jsonb)
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO mse_loop_stations (product_id, step_order, name, owner, description, next_action,
    status_rule_key, lane_link, playbook, target_hint)
VALUES
    ('9b6c8f36-985f-4d7a-a416-c40da89e23af', 1, 'Find hiring companies', 'auto', 'Every Sunday at 6am the scraper reads public company job boards and keeps companies that posted a DevOps, SRE or platform role in the last 60 days.', 'Nothing. It runs on its own.', 'outbound.lead_run', NULL, 'signal_outbound', '~9,000 posts read/run'),
    ('9b6c8f36-985f-4d7a-a416-c40da89e23af', 2, 'Qualify the company', 'auto', 'Drops consultancies, MSPs, staffing firms and infrastructure vendors, plus companies too big to need you.', 'Nothing.', 'outbound.qualify', NULL, 'signal_outbound', '6–15 companies/run'),
    ('9b6c8f36-985f-4d7a-a416-c40da89e23af', 3, 'Find the buyer', 'you', 'The system checks team pages and job posts for the CTO or Head of Engineering. If it can''t find one, the company waits in your Find the Buyer lane.', '5 lookups a day: CEO Decoded → Marketing → Outreach → Find the Buyer.', 'outbound.find_buyer', '/marketing#outreach', 'signal_outbound', '49 companies waiting'),
    ('9b6c8f36-985f-4d7a-a416-c40da89e23af', 4, 'Write the draft', 'auto', 'Writes a 3-touch sequence for the named buyer, as email or LinkedIn depending on the route.', 'Ask Claude Code for the 8am enrollment report.', 'outbound.draft_enrollment', '/marketing#outreach', 'signal_outbound', '8am daily'),
    ('9b6c8f36-985f-4d7a-a416-c40da89e23af', 5, 'Approve', 'you', 'You read and approve. Nothing reaches a prospect without this click.', 'Read the 4 drafts and approve the clean ones today.', 'outbound.approve', '/marketing#outreach', 'signal_outbound', '4 waiting · 0 ever approved'),
    ('9b6c8f36-985f-4d7a-a416-c40da89e23af', 6, 'Send', 'auto', 'Email drafts send hourly through Resend. LinkedIn drafts move to Ready to Paste, and you send them.', 'Add the DMARC record in Namecheap.', 'outbound.send', '/marketing#outreach', 'signal_outbound', 'cap 12/day'),
    ('9b6c8f36-985f-4d7a-a416-c40da89e23af', 7, 'Reply', 'you', 'Replies come to your Gmail. You mark them in Conversations, which stops the remaining touches.', 'Mark replies during the daily block.', 'outbound.replies', '/marketing#outreach', 'signal_outbound', '0 replies'),
    ('9b6c8f36-985f-4d7a-a416-c40da89e23af', 8, 'Book a call', 'you', 'The prospect books a call from the link in the email.', 'Click your own link once to confirm booking works.', 'outbound.calls_booked', '/marketing#outreach', 'signal_outbound', 'via thdagentic.com'),
    ('9b6c8f36-985f-4d7a-a416-c40da89e23af', 9, 'Proposal & close', 'you', 'A fixed offer you can send after the call: 30-day scope, deliverables, price.', 'Write a one-page offer with a price.', 'outbound.proposals', '/marketing#outreach', 'signal_outbound', 'no priced offer'),
    ('777a1852-f84c-49d8-890e-cd14670b7f6f', 1, 'Find hiring companies', 'auto', 'Same Sunday scraper as consulting, with Cloud Decoded''s own target list.', 'Nothing.', 'outbound.lead_run', NULL, 'signal_outbound', 'shared scraper'),
    ('777a1852-f84c-49d8-890e-cd14670b7f6f', 2, 'Qualify', 'auto', 'Same exclusions, except infrastructure vendors are scored down instead of dropped.', 'Nothing.', 'outbound.qualify', NULL, 'signal_outbound', '4–10 companies/run'),
    ('777a1852-f84c-49d8-890e-cd14670b7f6f', 3, 'Find the buyer', 'you', 'Engineering manager, Head of Platform/SRE, or CTO at smaller companies.', 'Same 5-a-day lookup block.', 'outbound.find_buyer', '/marketing#outreach', 'signal_outbound', 'shared lane'),
    ('777a1852-f84c-49d8-890e-cd14670b7f6f', 4, 'Write the draft', 'auto', 'Drafts route to email or LinkedIn and end with the demo link.', 'Same 8am report.', 'outbound.draft_enrollment', '/marketing#outreach', 'signal_outbound', '8am daily'),
    ('777a1852-f84c-49d8-890e-cd14670b7f6f', 5, 'Approve', 'you', 'Your click.', 'Approve GoReel if the copy reads right.', 'outbound.approve', '/marketing#outreach', 'signal_outbound', '0 approved'),
    ('777a1852-f84c-49d8-890e-cd14670b7f6f', 6, 'Send', 'auto', 'Shared sender and daily cap with consulting.', 'DMARC record.', 'outbound.send', '/marketing#outreach', 'signal_outbound', 'cap 12/day'),
    ('777a1852-f84c-49d8-890e-cd14670b7f6f', 7, 'Try the demo', 'auto', 'No signup. 8 sample incidents, approvals are simulated.', 'Record a 2-minute walkthrough from it.', 'cd.demo_sessions', NULL, 'signal_outbound', 'theclouddecoded.com/demo'),
    ('777a1852-f84c-49d8-890e-cd14670b7f6f', 8, 'Sign up & pay', 'auto', 'Stripe checkout, a guided setup screen and a welcome email.', 'Add the Resend records for theclouddecoded.com.', 'cd.signup_pay', NULL, 'signal_outbound', 'welcome email fails'),
    ('777a1852-f84c-49d8-890e-cd14670b7f6f', 9, 'Connect cloud', 'you', 'Customer connects AWS, Azure, GitHub, Kubernetes with a setup checklist.', 'Offer to do the first setup on a call.', 'cd.cloud_connected', NULL, 'signal_outbound', 'no real customer yet'),
    ('5ba0639e-4c9c-4205-be3d-7904f4dbc9b5', 1, 'Positioning', 'you', 'Who it''s for (10–30 unit self-managed landlords) and why it beats the free tools.', 'Nothing.', 'product.positioning_approved', NULL, 'community_search', 'brief approved'),
    ('5ba0639e-4c9c-4205-be3d-7904f4dbc9b5', 2, 'Site & search pages', 'auto', 'The site plus comparison pages that rank for searches like ''Buildium alternative''.', 'Have Claude Code publish 5 comparison pages.', 'product.search_pages', NULL, 'community_search', '0 extra pages live'),
    ('5ba0639e-4c9c-4205-be3d-7904f4dbc9b5', 3, 'Get seen', 'you', 'Value-first posts in landlord groups that answer real questions. No selling.', 'Wednesday block: 2 helpful posts.', 'product.community_posts', '/marketing', 'community_search', 'no channel running'),
    ('5ba0639e-4c9c-4205-be3d-7904f4dbc9b5', 4, 'Visit the site', 'auto', 'Traffic from posts and search.', 'Connect Search Console.', 'product.search_console', NULL, 'community_search', 'not measured'),
    ('5ba0639e-4c9c-4205-be3d-7904f4dbc9b5', 5, 'Sign up', 'auto', 'Self-serve signup.', 'Nothing.', 'product.signups', NULL, 'community_search', 'working'),
    ('5ba0639e-4c9c-4205-be3d-7904f4dbc9b5', 6, 'Pay', 'auto', 'Stripe checkout at $99/$149/$199.', 'Nothing.', 'product.payments', NULL, 'community_search', 'prices verified'),
    ('5ba0639e-4c9c-4205-be3d-7904f4dbc9b5', 7, 'Onboarding emails', 'auto', 'Welcome and setup emails after signup.', 'Approve the SPH email templates in CEO Decoded.', 'product.onboarding_emails', NULL, 'community_search', 'drafted, not approved'),
    ('5ba0639e-4c9c-4205-be3d-7904f4dbc9b5', 8, 'Paying customer', 'auto', 'The first landlord pays.', 'Comes after ''Get seen'' runs for a few weeks.', 'product.paying_customers', NULL, 'community_search', '0'),
    ('da79dcfa-4ecc-4a3b-9c3f-829ac3220ec9', 1, 'Find topics', 'auto', 'Reads IGN, GameSpot, Reddit and YouTube for new GTA 6 news.', 'Nothing.', 'd6.topics', NULL, 'content', 'every 4 hours'),
    ('da79dcfa-4ecc-4a3b-9c3f-829ac3220ec9', 2, 'Write', 'auto', 'Writes articles from approved topics.', 'Nothing.', 'd6.drafts', NULL, 'content', 'drafts queued'),
    ('da79dcfa-4ecc-4a3b-9c3f-829ac3220ec9', 3, 'Approve', 'you', 'Topic and article approval.', 'Clear the queue twice a week.', 'd6.approval_queue', NULL, 'content', 'your queue'),
    ('da79dcfa-4ecc-4a3b-9c3f-829ac3220ec9', 4, 'Publish', 'auto', 'Goes live on the site.', 'Nothing.', 'd6.published', NULL, 'content', '~60 articles'),
    ('da79dcfa-4ecc-4a3b-9c3f-829ac3220ec9', 5, 'Indexed', 'auto', 'Google has the pages.', 'Nothing.', 'd6.indexed', NULL, 'content', '255 pages'),
    ('da79dcfa-4ecc-4a3b-9c3f-829ac3220ec9', 6, 'Rank', 'auto', 'Pages show up in search results.', 'Have launch-day articles ready to publish Nov 19.', 'd6.ranking', NULL, 'content', '~0 impressions in Sept'),
    ('da79dcfa-4ecc-4a3b-9c3f-829ac3220ec9', 7, 'Traffic', 'auto', 'Visitors.', 'Turn the interactive map on at launch.', 'd6.traffic', NULL, 'content', '~121 sessions/mo'),
    ('da79dcfa-4ecc-4a3b-9c3f-829ac3220ec9', 8, 'Earn', 'auto', 'AdSense, then Mediavine; Amazon affiliate; $17 guides after launch.', 'Reapply to AdSense with launch traffic.', 'd6.earnings', NULL, 'content', 'affiliate only'),
    ('96e169fe-c9bf-4e3f-b152-37f9f3b83004', 1, 'Build the product', 'you', 'Your own success model applies.', 'Finish the build.', 'product.build', NULL, 'signal_outbound', 'in build'),
    ('96e169fe-c9bf-4e3f-b152-37f9f3b83004', 2, 'List public entities', 'auto', 'Public lists of cities, counties and districts by size and state.', 'A new target list in the existing engine.', 'outbound.icp_configured', NULL, 'signal_outbound', 'not built'),
    ('96e169fe-c9bf-4e3f-b152-37f9f3b83004', 3, 'Find the contact', 'auto', 'ADA coordinator, clerk or IT director. Usually public.', '—', 'outbound.find_buyer', '/marketing#outreach', 'signal_outbound', 'not built'),
    ('96e169fe-c9bf-4e3f-b152-37f9f3b83004', 4, 'Draft & approve', 'you', 'Same drafts and approval queue.', '—', 'outbound.approve', '/marketing#outreach', 'signal_outbound', 'engine exists'),
    ('96e169fe-c9bf-4e3f-b152-37f9f3b83004', 5, 'Scan their site', 'auto', 'A free accessibility scan of their site as the opener.', '—', 'none.not_built', NULL, 'signal_outbound', 'not built'),
    ('96e169fe-c9bf-4e3f-b152-37f9f3b83004', 6, 'Quote & contract', 'you', 'Public procurement or a simple quote.', '—', 'none.not_built', NULL, 'signal_outbound', 'not built'),
    ('2f2ff5c8-23d7-40ac-bf5e-98b92c72adbb', 1, 'Build the apps', 'you', 'Main app plus spinoffs.', '—', 'product.build', NULL, 'distributor', '18 sessions planned'),
    ('2f2ff5c8-23d7-40ac-bf5e-98b92c72adbb', 2, 'List churches & ministries', 'you', 'Local churches, men''s and women''s ministry leaders.', '—', 'none.not_built', NULL, 'distributor', 'not built'),
    ('2f2ff5c8-23d7-40ac-bf5e-98b92c72adbb', 3, 'Pastor outreach', 'you', 'Offer free group access for their ministry.', '—', 'none.not_built', NULL, 'distributor', 'not built'),
    ('2f2ff5c8-23d7-40ac-bf5e-98b92c72adbb', 4, 'Group joins', 'auto', 'Shared notes and accountability pull members in.', '—', 'none.not_built', NULL, 'distributor', 'not built'),
    ('2f2ff5c8-23d7-40ac-bf5e-98b92c72adbb', 5, 'App store', 'auto', 'Listing and search keywords.', '—', 'none.not_built', NULL, 'distributor', 'not built'),
    ('2f2ff5c8-23d7-40ac-bf5e-98b92c72adbb', 6, 'Paid subscribers', 'auto', 'Paid tiers for people outside free groups.', '—', 'none.not_built', NULL, 'distributor', '—')
ON CONFLICT (product_id, step_order) DO NOTHING;

INSERT INTO mse_loop_playbook_templates (playbook, step_order, name, owner, description, next_action,
    status_rule_key, lane_link)
VALUES
    ('signal_outbound', 1, 'Find hiring companies', 'auto', 'Every Sunday at 6am the scraper reads public company job boards and keeps companies that posted a DevOps, SRE or platform role in the last 60 days.', 'Nothing. It runs on its own.', 'outbound.lead_run', NULL),
    ('signal_outbound', 2, 'Qualify the company', 'auto', 'Drops consultancies, MSPs, staffing firms and infrastructure vendors, plus companies too big to need you.', 'Nothing.', 'outbound.qualify', NULL),
    ('signal_outbound', 3, 'Find the buyer', 'you', 'The system checks team pages and job posts for the CTO or Head of Engineering. If it can''t find one, the company waits in your Find the Buyer lane.', '5 lookups a day: CEO Decoded → Marketing → Outreach → Find the Buyer.', 'outbound.find_buyer', '/marketing#outreach'),
    ('signal_outbound', 4, 'Write the draft', 'auto', 'Writes a 3-touch sequence for the named buyer, as email or LinkedIn depending on the route.', 'Ask Claude Code for the 8am enrollment report.', 'outbound.draft_enrollment', '/marketing#outreach'),
    ('signal_outbound', 5, 'Approve', 'you', 'You read and approve. Nothing reaches a prospect without this click.', 'Read the 4 drafts and approve the clean ones today.', 'outbound.approve', '/marketing#outreach'),
    ('signal_outbound', 6, 'Send', 'auto', 'Email drafts send hourly through Resend. LinkedIn drafts move to Ready to Paste, and you send them.', 'Add the DMARC record in Namecheap.', 'outbound.send', '/marketing#outreach'),
    ('signal_outbound', 7, 'Reply', 'you', 'Replies come to your Gmail. You mark them in Conversations, which stops the remaining touches.', 'Mark replies during the daily block.', 'outbound.replies', '/marketing#outreach'),
    ('signal_outbound', 8, 'Book a call', 'you', 'The prospect books a call from the link in the email.', 'Click your own link once to confirm booking works.', 'outbound.calls_booked', '/marketing#outreach'),
    ('signal_outbound', 9, 'Proposal & close', 'you', 'A fixed offer you can send after the call: 30-day scope, deliverables, price.', 'Write a one-page offer with a price.', 'outbound.proposals', '/marketing#outreach'),
    ('community_search', 1, 'Positioning', 'you', 'Who it''s for (10–30 unit self-managed landlords) and why it beats the free tools.', 'Nothing.', 'product.positioning_approved', NULL),
    ('community_search', 2, 'Site & search pages', 'auto', 'The site plus comparison pages that rank for searches like ''Buildium alternative''.', 'Have Claude Code publish 5 comparison pages.', 'product.search_pages', NULL),
    ('community_search', 3, 'Get seen', 'you', 'Value-first posts in landlord groups that answer real questions. No selling.', 'Wednesday block: 2 helpful posts.', 'product.community_posts', '/marketing'),
    ('community_search', 4, 'Visit the site', 'auto', 'Traffic from posts and search.', 'Connect Search Console.', 'product.search_console', NULL),
    ('community_search', 5, 'Sign up', 'auto', 'Self-serve signup.', 'Nothing.', 'product.signups', NULL),
    ('community_search', 6, 'Pay', 'auto', 'Stripe checkout at $99/$149/$199.', 'Nothing.', 'product.payments', NULL),
    ('community_search', 7, 'Onboarding emails', 'auto', 'Welcome and setup emails after signup.', 'Approve the SPH email templates in CEO Decoded.', 'product.onboarding_emails', NULL),
    ('community_search', 8, 'Paying customer', 'auto', 'The first landlord pays.', 'Comes after ''Get seen'' runs for a few weeks.', 'product.paying_customers', NULL),
    ('content', 1, 'Find topics', 'auto', 'Reads IGN, GameSpot, Reddit and YouTube for new GTA 6 news.', 'Nothing.', 'd6.topics', NULL),
    ('content', 2, 'Write', 'auto', 'Writes articles from approved topics.', 'Nothing.', 'd6.drafts', NULL),
    ('content', 3, 'Approve', 'you', 'Topic and article approval.', 'Clear the queue twice a week.', 'd6.approval_queue', NULL),
    ('content', 4, 'Publish', 'auto', 'Goes live on the site.', 'Nothing.', 'd6.published', NULL),
    ('content', 5, 'Indexed', 'auto', 'Google has the pages.', 'Nothing.', 'd6.indexed', NULL),
    ('content', 6, 'Rank', 'auto', 'Pages show up in search results.', 'Have launch-day articles ready to publish Nov 19.', 'd6.ranking', NULL),
    ('content', 7, 'Traffic', 'auto', 'Visitors.', 'Turn the interactive map on at launch.', 'd6.traffic', NULL),
    ('content', 8, 'Earn', 'auto', 'AdSense, then Mediavine; Amazon affiliate; $17 guides after launch.', 'Reapply to AdSense with launch traffic.', 'd6.earnings', NULL),
    ('distributor', 1, 'Build the apps', 'you', 'Main app plus spinoffs.', '—', 'product.build', NULL),
    ('distributor', 2, 'List churches & ministries', 'you', 'Local churches, men''s and women''s ministry leaders.', '—', 'none.not_built', NULL),
    ('distributor', 3, 'Pastor outreach', 'you', 'Offer free group access for their ministry.', '—', 'none.not_built', NULL),
    ('distributor', 4, 'Group joins', 'auto', 'Shared notes and accountability pull members in.', '—', 'none.not_built', NULL),
    ('distributor', 5, 'App store', 'auto', 'Listing and search keywords.', '—', 'none.not_built', NULL),
    ('distributor', 6, 'Paid subscribers', 'auto', 'Paid tiers for people outside free groups.', '—', 'none.not_built', NULL)
ON CONFLICT (playbook, step_order) DO NOTHING;
