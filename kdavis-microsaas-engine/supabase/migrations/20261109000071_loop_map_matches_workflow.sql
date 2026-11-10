-- 071: the Loop Map's station text matches the workflow as it runs today
-- (Kelvin, 2026-11-09: "loop map needs to be updated so that the workflow
-- matches").
--
-- Data only: no table, column or policy changes, so no RLS work here. The
-- desk COLORS are computed live by core/loop_map_rules.py and were already
-- right; what drifted was the hand-written text seeded in 065/066:
--   * hints frozen at seed time ("4 waiting · 0 ever approved", "49
--     companies waiting", "Approve GoReel") that now read as live numbers;
--   * replies said "mark them in Conversations" -- the button is now Mark
--     replied under Email in flight (email) or Ready to Paste (LinkedIn);
--   * sending did not say only verified addresses are emailed (2026-11-07);
--   * the one-company-one-product rule (2026-11-09) was missing;
--   * Referral Partners pointed at the Marketing page; partners now have their
--     own Partners tab, and the steps say when each one happens.
--
-- Matched on (product_id, step_order). Idempotent: re-running sets the same text.

-- ── THD Agentic Systems Consulting ─────────────────────────────────────────
UPDATE mse_loop_stations SET
  target_hint = '5 lookups a day',
  next_action = '5 lookups a day: CEO Decoded → Marketing → Outreach → Find the Buyer (filter: Consulting).'
WHERE product_id = '9b6c8f36-985f-4d7a-a416-c40da89e23af' AND step_order = 3;

UPDATE mse_loop_stations SET
  description = 'Every morning at 8am the system writes a 3-touch sequence for each newly named buyer: email if the address is verified, LinkedIn otherwise.',
  target_hint = '8am daily'
WHERE product_id = '9b6c8f36-985f-4d7a-a416-c40da89e23af' AND step_order = 4;

UPDATE mse_loop_stations SET
  description = 'You read and approve each draft. Nothing reaches a prospect without this click. If the same company also has a Cloud Decoded draft, only one of the two can be approved.',
  target_hint = 'daily block',
  next_action = 'Approve Drafts (amber CONSULTING badge): approve the clean ones, edit or reject the rest.'
WHERE product_id = '9b6c8f36-985f-4d7a-a416-c40da89e23af' AND step_order = 5;

UPDATE mse_loop_stations SET
  description = 'Approved email drafts send hourly through Resend, only to verified addresses, up to 12 a day shared with Cloud Decoded. Follow-ups go 3 and 7 days later unless they reply. LinkedIn drafts move to Ready to Paste and you send them.',
  target_hint = 'cap 12/day'
WHERE product_id = '9b6c8f36-985f-4d7a-a416-c40da89e23af' AND step_order = 6;

UPDATE mse_loop_stations SET
  description = 'Replies land in your Gmail; the system cannot see them. Click mark replied under Email in flight (or in Ready to Paste for LinkedIn). That stops the remaining follow-ups and opens the conversation.',
  target_hint = 'you mark it',
  next_action = 'Check Gmail in the daily block and mark every reply the same day.'
WHERE product_id = '9b6c8f36-985f-4d7a-a416-c40da89e23af' AND step_order = 7;

UPDATE mse_loop_stations SET
  description = 'The prospect books a call from the link in the email. Mark it in Conversations: Call booked.'
WHERE product_id = '9b6c8f36-985f-4d7a-a416-c40da89e23af' AND step_order = 8;

-- ── Cloud Decoded ──────────────────────────────────────────────────────────
UPDATE mse_loop_stations SET
  target_hint = 'shared 5-a-day block',
  next_action = 'Same 5-a-day lookup block (filter: Cloud Decoded).'
WHERE product_id = '777a1852-f84c-49d8-890e-cd14670b7f6f' AND step_order = 3;

UPDATE mse_loop_stations SET
  description = 'Every morning at 8am: email if the address is verified, LinkedIn otherwise. Every draft ends with the demo link.',
  next_action = 'Nothing.'
WHERE product_id = '777a1852-f84c-49d8-890e-cd14670b7f6f' AND step_order = 4;

UPDATE mse_loop_stations SET
  description = 'Your click. If the same company also has a consulting draft, only one of the two can be approved.',
  target_hint = 'daily block',
  next_action = 'Approve Drafts (blue CLOUD DECODED badge): approve the clean ones, edit or reject the rest.'
WHERE product_id = '777a1852-f84c-49d8-890e-cd14670b7f6f' AND step_order = 5;

UPDATE mse_loop_stations SET
  description = 'Shared sender and 12-a-day cap with consulting, verified addresses only. LinkedIn drafts go to Ready to Paste.'
WHERE product_id = '777a1852-f84c-49d8-890e-cd14670b7f6f' AND step_order = 6;

-- ── Referral Partners: their own tab, and when each step happens ───────────
UPDATE mse_loop_stations SET lane_link = '/partners'
WHERE product_id = 'c0e8aaf3-e858-4df4-b0ba-863580556a18';

UPDATE mse_loop_stations SET
  target_hint = 'monthly, on the 1st',
  next_action = 'Nothing. Runs on its own on the 1st of each month (up to 20 searches, 25 firms).'
WHERE product_id = 'c0e8aaf3-e858-4df4-b0ba-863580556a18' AND step_order = 1;

UPDATE mse_loop_stations SET
  next_action = 'Partners tab → Name the founder: paste the founder''s LinkedIn profile, name and title.'
WHERE product_id = 'c0e8aaf3-e858-4df4-b0ba-863580556a18' AND step_order = 3;

UPDATE mse_loop_stations SET
  description = 'Every morning at 8am, for each firm with a named founder: a connection note, a first message for 3 days after they accept, and one last note 4 days later.',
  target_hint = '8am daily'
WHERE product_id = 'c0e8aaf3-e858-4df4-b0ba-863580556a18' AND step_order = 4;

UPDATE mse_loop_stations SET
  description = 'Nothing goes out until you approve it. Approve only as many as you will paste this week.',
  next_action = 'Partners tab → Approve partnership notes.'
WHERE product_id = 'c0e8aaf3-e858-4df4-b0ba-863580556a18' AND step_order = 5;

UPDATE mse_loop_stations SET
  description = 'By hand on LinkedIn from your own account: send the connection note, then mark it sent. Message 2 goes 3 days after they accept; message 3 four days later if there is no reply.',
  target_hint = 'your daily block',
  next_action = 'Partners tab → Ready to paste: send today''s notes and mark each one sent.'
WHERE product_id = 'c0e8aaf3-e858-4df4-b0ba-863580556a18' AND step_order = 6;

UPDATE mse_loop_stations SET
  description = 'Marked by hand on the Partners tab when a partner answers. That stops the remaining messages.',
  next_action = 'Mark the reply on the Partners tab.'
WHERE product_id = 'c0e8aaf3-e858-4df4-b0ba-863580556a18' AND step_order = 7;

UPDATE mse_loop_stations SET
  next_action = 'Partners tab → Conversations: mark Intro call.'
WHERE product_id = 'c0e8aaf3-e858-4df4-b0ba-863580556a18' AND step_order = 8;

UPDATE mse_loop_stations SET
  next_action = 'Partners tab → Conversations: mark Agreement.'
WHERE product_id = 'c0e8aaf3-e858-4df4-b0ba-863580556a18' AND step_order = 9;
