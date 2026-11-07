# Marketing & Outreach Buyer-Targeting Fix — Phase 0 Audit

**Date:** 2026-11-07 · **Mode:** read-only · **Source of truth:** live prod DB
(Supabase `gjezchco…`, shared by `kdavis-microsaas-engine` and
`kdavis-agentic-platform`) and code at `5cf2e98` (prod and staging both report
this SHA on `/health`).

## 1. Product IDs (canonical `mse_products.id`)

| Product | slug | id |
|---|---|---|
| Cloud Decoded | `cloud-decoded` | `777a1852-f84c-49d8-890e-cd14670b7f6f` |
| THD Agentic Systems Consulting | `thdagentic-consulting` | `9b6c8f36-985f-4d7a-a416-c40da89e23af` |

Both resolve to exactly one row. Note: the slug is `thdagentic-consulting`, not
`thd-agentic-consulting` as the brief assumed. It is unambiguous, so this is not a
STOP. `thd-partners` (Referral Partners) is a separate product and **out of scope**.

## 2. Rows per product (non-zero only)

| Table | CD | Consulting | Notes |
|---|---|---|---|
| `mse_leads` | 115 | 122 | statuses below |
| `mse_dm_sequences` | 19 | 17 | Approve Drafts / Ready to Paste lanes |
| `mse_dm_enrollment_skips` | 48 | 34 | all `contact_fit_rejected` |
| `mse_icp_configs` | 1 | 1 | |
| `mse_positioning` | 0 | 1 | consulting brief, `pending_review` |
| `mse_competitors` | 0 | 6 | |
| `mse_lead_finder_runs` | 25 | 24 | |
| `mse_ats_board_tokens` | 497 | 65 | source registry; not leads, so not purged |
| `mse_activities` | 113 | 123 | history; kept |
| `audit_log` | 78 | 137 | append-only; kept |
| `mse_email_sequences` | **0** | **0** | no email campaigns exist for either product |
| `mse_hitl_items` | 0 | 0 | |
| `mse_outreach_conversations` | 0 | 0 | Conversations lane is empty |
| `linkedin_content_queue` | (product_id=`marketing`) 5 unpublished, 46 published, 27 rejected | | |

`mse_leads` by status: CD has 39 `company_qualified`, 56 `disqualified`, 4 `pending_dm`
and 16 `pending_email`. Consulting has 104 `company_qualified`, 8 `disqualified`,
2 `pending_dm` and 8 `pending_email`.

`mse_dm_sequences` by status: CD has 3 `awaiting_contact` and 16 `pending_hitl`.
Consulting has 3 `awaiting_contact`, 7 `pending_hitl` and 7 `rejected_hitl`. None are
approved and none are sent.

Lane → table map (from `api/routers/outreach_lanes.py` and `buyer_research.py`):

- **Find the Buyer:** `mse_leads` where `contact_status='pending'`
- **Approve Drafts:** `mse_dm_sequences` where `status='pending_hitl'`
- **Ready to Paste:** `mse_dm_sequences` where `status='approved_manual'`
- **Conversations:** `mse_outreach_conversations`

## 3. Existing leads under the new buyer rules

Classified with the new deterministic gate (`agents/marketing/buyer_gate.py`).
Company band is 20–500 for CD and 20–200 for consulting.

| | Send queue (pass all three gates) | Find the Buyer | Reject (company) |
|---|---|---|---|
| Cloud Decoded (115) | **0** | 65 | 50 |
| Consulting (122) | **0** | 82 | 40 |

Why leads fail:

- **Reject, no resolved company domain (56).** Old `job_posting_signal` rows.
- **Reject, aggregator or ATS host as "company" (12).** Includes `linkedin.com`,
  `ziprecruiter.com`, `index.dev`, `apply.workable.com` and `camberhealth.notion.site`.
- **Reject, competitor (3).** Security Risk Advisors (consultancy), Coderio (dev
  agency) and Sparksoft (gov IT services).
- **Reject, out of size band (≈25, consulting).** Open-role count implies more than
  200 staff (26–40 open roles), plus one stated 301–1000 headcount.
- **Find the Buyer, no contact (125).** The company qualified but no person was named.
- **Find the Buyer, buyer title but email not verified (≈16).** Graded `risky` (5),
  `invalid` (5) or `unknown`/null (6). Examples: Roboflow CTO, Customer.io CTO,
  Lumafield Head of Engineering, Attentive CTO.
- **Find the Buyer, title ambiguous or wrong (≈6).** "Chief Information Officer",
  bare "VP" or "EVP", "Director, Design", a CEO at an unconfirmed size, and a title
  carrying scraped HTML with a second person's name in it.

Also observed, and not fixable by rules alone:

- **Wrong-person matches.** "Urs Hölzle, Director of Engineering, Fellow" is a known
  Google executive, and "Learning Center, Founder, ClassWallet" is a page heading.
  Contact discovery pairs a name and a title found near each other on a page.
- **Large companies with unknown headcount.** Samsung, Trainline, Algolia, Attentive
  and AutoScout24 passed company checks. `headcount_band` is null on 304 of 310 leads.

## 4. Why active workflows produced zero sends

Evidence is from `usage_events` and `audit_log`, because the service has no n8n API
access: `N8N_API_KEY` exists only in Railway, and the Railway CLI is logged out.
`lead_schedule.py` itself documents that "this service cannot read or verify n8n
executions".

- **The hourly sender ran and found nothing to send.** It started 1,496 times, most
  recently 2026-11-07 19:00 UTC with `{"count": 0}`. Only one MKT-O5 send has ever
  succeeded, on 2026-10-02, which was the earlier send test.
- **The 8am enrollment does run.** MKT-O2 has 79 `win` rows, the latest today at
  15:00 UTC, plus 56 "parked no buyer" events. The 7 JSON-decode failures are all
  `partner_signal`, which is out of scope. **"Active workflows enrolled no one" is
  therefore not accurate:** drafts are created and then sit at `pending_hitl`.
- **The n8n 8am workflow only calls `/marketing/linkedin/dm-sequences`.** That
  drafts LinkedIn-intake leads only. The lead-finder enrollment is in-process.

**Root causes on the send path, in order:**

1. **No real recipient is sendable.** Of the 23 `pending_hitl` drafts, only 5 sit on
   `lead_route=outbound_email`, and all 5 have grade `risky` (catch-all). The other
   18 would be approved to `approved_manual`, which MKT-O5 never polls. Even if Kelvin
   had approved every draft, at most 5 emails could send, and all 5 to unverified
   catch-all addresses.
2. **SMTP verification essentially never succeeds.** `mse_email_patterns` records 4
   successes in 291 probes across the whole system, and only 3 leads in the entire
   DB are graded `valid`. Contributing causes found in `core/email_finder.py`:
   - It sends `HELO localhost`, which Google, Microsoft and Proofpoint MX servers
     commonly reject or tarpit.
   - Every non-250/550 reply, and every connection error, collapses to `unverified`
     with nothing recorded.
   - Outbound port 25 is unreachable from this WSL machine. Whether Railway allows
     it could not be verified because the CLI is logged out.
3. **The rules admitted catch-all (`risky`) to the outbound route** (decision 3b,
   2026-10-01). The 2026-11-07 brief reverses this: only `verified` may enter the queue.
4. **Contact fit accepted individual contributors.** `contact_fit.FUNCTIONAL_TITLE_RE`
   accepted "Platform Engineer", "DevOps Engineer", "Cloud Architect" and "SRE Lead"
   at any company size. Cloud Decoded's `mse_icp_configs.job_titles` also lists `SRE`,
   `DevOps engineer`, `platform engineer`, `cloud engineer` and `infrastructure
   engineer`, with `min_company_size=100` and no maximum.
5. **Company suppression did not exist.** Dedup is contact-level only, so a
   rejected company re-ingests on the next run.

## 5. Approved draft → Resend: the exact path

1. Approve: `POST /marketing/outreach/dm-sequences/{id}/approve`, in
   `api/routers/outreach.py:135`. `_approval_target_status()` decides the status
   from the lead's own `lead_route` and `email`: `outbound_email` with an email gives
   `approved_hitl`, and anything else gives `approved_manual`.
2. The hourly n8n run calls `POST /marketing/send-sequences`, which runs
   `mkt_o5_sequence_sender.run_sequence_sender`.
3. `run_send_touch_1` applies these checks in order:
   1. void expired approvals
   2. bounce-rate pause
   3. `selling_stage=active`
   4. daily cap
   5. `_get_lead` (recipient selection: `mse_leads.email` via `lead_finder_lead_id`,
      otherwise `mse_apollo_leads`)
   6. `_email_send_block_reason` (route and grade)
   7. suppression
   8. atomic claim
   9. `_send_email`, which calls `resend.Emails.send`

**Recipient selection is correct mechanically.** It sends to the lead's stored
address. The problem is what gets stored: unverified pattern guesses on wrong or
IC contacts.

## 6. Follow-ups and reply detection — what actually exists

- **touch_2:** sent 3 days after touch_1. ✅ Exists.
- **touch_3:** drafted by MKT-O2 and stored in `mse_dm_sequences.touch_3`, but
  **never sent**. MKT-O5 marks a sequence `sequence_complete` after touch_2. ❌
- **Reply stop:** works only through the manual "replied" action in the Conversations
  and Ready-to-Paste lanes. `_record_reply` moves the sequence to `replied`, so
  touch_2's `status='touch_1_sent'` query skips it. ✅ Manual only.
- **Automatic reply detection:** none. There is no Resend inbound handler. ❌
- **Bounce detection:** **inert.** `sends_paused()` counts `mse_leads.status='bounced'`,
  but nothing in the codebase ever sets that status, because no Resend webhook
  handler exists. ❌
- **Unsubscribe:** `GET`/`POST /marketing/unsubscribe` exists with an HMAC token,
  RFC 8058 headers are set, and suppression is checked before every send. ✅
- **Warmup ramp:** not implemented. The cap is a flat `MARKETING_DAILY_SEND_CAP`,
  default 200. ❌
- **Risky sub-cap bug:** `risky_sends_today()` reads `metadata.email_grade` from the
  win rows, but `_write_audit` never writes it, so the sub-cap never counts. ❌ This
  is moot once risky is blocked.

## 7. Blockers known at the end of Phase 0

- **Railway CLI is logged out.** Deploying to staging or prod needs it.
  Kelvin: run `! railway login`.
- **SMTP egress from Railway is unverified.** If port 25 is blocked there, no lead
  can ever reach `valid`, and the fix would be a third-party verifier (a new vendor
  needs Kelvin's approval) or Railway Pro egress.
