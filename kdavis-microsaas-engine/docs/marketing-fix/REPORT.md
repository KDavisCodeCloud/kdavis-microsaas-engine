# Marketing & Outreach Buyer-Targeting Fix — Report

**Dates:** 2026-11-07 to 2026-11-08 · **Scope:** Cloud Decoded (`777a1852…`) and THD
Agentic Systems Consulting (`9b6c8f36…`) only · **Evidence:** live prod database and
deployed code, not docs.

**Where it runs now:**

| Service | Environment | Commit |
|---|---|---|
| mse-api | production | `8c2d7de` |
| mse-api | staging | `8c2d7de` |
| kdavis-agentic-platform | production | `99b3081` |

Both mse-api environments report their commit on `/health`, and migration 070 is applied to
both. The platform service has no staging environment and deploys `master` on push.

Phase 0 audit: [`00-audit.md`](00-audit.md).

---

## 1. Purge

Every purge was soft: a status change plus suppression. Snapshots were taken before
anything changed, and nothing was hard-deleted.

| | Cloud Decoded | Consulting |
|---|---|---|
| Leads moved to `rejected_wrong_icp` | 117 | 130 |
| Drafts moved to `archived_wrong_icp` | 20 | 13 |
| Leads remaining under the new rules | 10 | 12 (+1 closed test lead) |

How those rows got there:

- **Phase 1, 2026-11-07:** 237 leads and 29 drafts. 191 companies were suppressed in
  `mse_suppressed_companies` (source `marketing_fix_20261107`) and 60 addresses in
  `mse_email_suppressions` (`wrong_icp`).
- **2026-11-08 cleanup.** The Sunday 13:00 UTC lead run went out on the pre-fix code,
  because Railway was logged out until 20:16. It added 29 leads and 7 drafts:
  - The new gate rejected 8 of the leads, including Rearc, an AWS consultancy.
  - It archived 4 of the drafts: Rearc, Ema, Raft and Checkr.
  - Source `marketing_fix_20261108`.
- **2026-11-08 Phase 3 spot check:** 2 more leads rejected, Tubi (resold domain) and OnHires
  (recruiting agency). Source `marketing_fix_20261108_spotcheck`.

Backups:

- `mse_*_backup_20261107` tables on prod: 237 leads and 36 sequences.
- `.backups/marketing-fix-20261107/` (gitignored): JSON snapshots, including the 2026-11-08
  old-run leads and drafts and the unpublished LinkedIn posts.

Other products were not touched. SPH, Showing Signal, TradesDesk, DecodedSix and the rest
are byte-identical to the Phase 0 baseline, re-checked at the end
(`diff others_before others_after` returns nothing).

Reversing one: every suppressed company row carries its reason code. Deleting that row,
and setting the lead back to `company_qualified`, restores it. Roboflow, Customer.io,
Lumafield and the other good companies swept up in Phase 1 are recoverable this way.

## 2. Root causes

1. **Individual contributors passed contact fit.**
   - `contact_fit.FUNCTIONAL_TITLE_RE` accepted "Platform Engineer", "DevOps Engineer",
     "Cloud Architect" and "SRE Lead" at any company size.
   - Cloud Decoded's `job_titles` listed SRE, DevOps engineer, platform engineer, cloud
     engineer and infrastructure engineer.
   - **Fix:** `agents/marketing/buyer_gate.py`, a single deterministic gate.
     - Allow only CTO, VP/Head/Director of Engineering, Platform, Infrastructure, DevOps
       or SRE, plus founders.
     - A CEO is allowed only with a stated headcount under 50.
     - Anything else is DENY or AMBIGUOUS, and AMBIGUOUS is never promoted.
     - Migration 069 sets `job_titles` to buyers only, with bands of 20–500 for Cloud
       Decoded and 20–200 for consulting.
2. **Catch-all (`risky`) addresses were allowed to send** under decision 3b.
   - **Fix:** only `valid` routes, drafts as email or sends. This is enforced in four
     places: routing, MKT-O2, approve, and the MKT-O5 sender.
3. **Companies were never suppressed,** so a rejected company re-ingested the next week.
   - **Fix:** `mse_suppressed_companies`, checked by every scout. The gate also rejects:
     - job boards and ATS hosts used as the "company"
     - staffing firms, MSPs and consultancies, including Rearc, OnHires and similar firms
       found live
     - the excluded employers (CorVel, Honeywell, Boeing)
     - resold domains such as `*.it.com`
4. **The copy was written to a technical peer** and asked for "a specific dollar amount"
   with nothing to source it from.
   - **Fix:** see Drafts.
5. **The send path was incomplete:**
   - touch_3 was drafted but never sent;
   - nothing ever set `bounced`, so the 3% bounce pause was inert;
   - there was no warm-up ramp;
   - the risky sub-cap read an `email_grade` that was never written.
   - **Fix:** see Send test.

## 3. Funnel

**Fresh run, 2026-11-08 ~20:19 UTC** on the new code:

| Product | Run id | Companies considered | Leads |
|---|---|---|---|
| Cloud Decoded | `e881987b…` | 361 | **3** |
| Consulting | `958e5efd…` | 346 | **1** |

`funnel_stats` are persisted on both run rows. Brave queries were 32 and 27, inside the cap
of 40 per run. Monthly Brave usage is now **220 of 900** (refuse threshold 720).

Where Cloud Decoded's 361 companies dropped (consulting's numbers in brackets):

| Stage | Cloud Decoded | Consulting |
|---|---|---|
| No matching role | 176 | 178 |
| Already in today's 13:00 run | 56 | 57 |
| Competitor (consultancy, MSP, staffing, reseller) | 69 | 89 |
| Too large by open-role count | 61 | 46 |
| No resolvable domain | 5 | 2 |

**Target count of 100 was not reached, and I did not chase it.** The limit is the source,
not the rules. The registry of about 350 public ATS boards had already been drained by the
13:00 run, so further runs would mostly produce duplicates and spend Brave budget. Weekly
volume at the new bar is roughly 20–30 leads across both products, and growing it means
adding boards (see Remaining gaps).

**Leads under the new rules today (22):**

- **Send queue: 1.** Aerones, CTO, SMTP-`valid` address on the company domain. I re-routed
  it from manual LinkedIn, because the old code had routed it.
- **Find the Buyer: 21.** Of these, 6 have a buyer-titled contact but no verified email, so
  they go the manual LinkedIn route. The other 15 have no person named yet.

**10-lead spot check** (fixed random seed; the sample files are in the scratchpad):

- **Draw 1:** found Tubi on `tubitv.it.com`, a resold domain, so a guessed email would go to
  a stranger. Fixed the rule and re-ran.
- **Draw 2:** found OnHires, a recruiting agency. Fixed the rule and re-ran.
- **Draw 3: clean.** No individual-contributor contacts, no aggregators, no competitors.
  Every named contact is a CTO, founder or VP Engineering. The individual-contributor
  titles in the sample are the job *being hired for*, which is the signal; they are not the
  person contacted.

## 4. Drafts

The MKT-O2 prompts were rewritten (`agents/marketing/outreach_copy.py`, `BUYER_COPY_RULES`):

- The reader is the budget owner.
- Open on the lead's own signal.
- Name the business pain in exactly one unit: dollars, hours or risk.
- One outcome and one low-friction ask.
- **No invented metrics, enforced in code.** `unsourced_metrics()` flags any figure not
  present in the lead's context. The writer retries once, then refuses the draft. Tests:
  `tests/test_route_aware_copy.py`.
- Touch 3 is sent on day 7.
- The legacy and engager prompts no longer demand "a specific dollar amount".

Six drafts were generated at `pending_hitl`. Four are new, and two (Pylon, Loop Returns)
were old-prompt drafts regenerated in place. Polygon Labs' old draft was left for review:
its scraped title carries another person's name.

**Cloud Decoded (2 — the only two buyer contacts it has today):**

- **FreedomPay — Brandon, VP Platform Operations** (LinkedIn). Signal: hiring a Sr. Platform
  Operations Engineer.
  > touch 2: "…the pipeline failures and alert noise your existing team absorbs don't pause
  > — they just cost more senior hours per week with fewer hands to absorb them. … Would it
  > be worth a 15-minute look at how it fits what you're building at FreedomPay?"
- **SandboxAQ — Stefan, VP of Engineering** (LinkedIn). Signal: hiring a Principal TPM,
  Data Platform.
  > touch 2: "…your senior engineers are usually the ones absorbing the platform ops load:
  > triaging pipeline failures, chasing alert noise, watching cloud spend that nobody has
  > formally owned yet. That's roadmap time the business doesn't get back."

**Consulting (3 of 4 shown):**

- **Aerones — Ashley, CTO** (EMAIL). Subject: "your open senior network engineer role at
  Aerones".
  > touch 1: "Every week that Senior Network Engineer seat sits open is a week your existing
  > team either absorbs the work or the roadmap waits. … the infrastructure and network
  > work your open role was meant to cover gets done now … without adding a permanent
  > headcount line to the budget. … Is the network engineering backlog something you'd
  > want to close in the next few weeks rather than waiting on a hire?"
- **15Five — Natalie, CTO** (LinkedIn). Signal: hiring a Senior DevOps Engineer.
  > touch 2: "…every week that seat stays empty is roadmap time your engineering team isn't
  > getting back. … Would it be worth a 15-minute call to see if there's a fit?"
- **Loop Returns — Alex, Founder** (LinkedIn, regenerated). Signal: hiring a Senior
  Platform Engineer.
  > touch 2: "One outcome: the infrastructure work your open role was meant to cover gets
  > done while you keep hiring. Worth a quick conversation?"

**Review notes for you:**

- SandboxAQ's touch 2 ends on the link with no question.
- Pylon's touch 1 has no first-name greeting, and its signal (a PM role) is weak.
- Not one of the six contains an invented figure.

## 5. Send test

One test send, to `KELVIN_EMAIL` only, through the real pipeline:

1. Draft inserted at `pending_hitl` (test lead marked "NOT a prospect").
2. Approved through `approve_dm_sequence` itself, run in-process against prod, which put it
   at `approved_hitl`.
3. **STOP check:** the sendable set across all products was exactly 1 row, addressed to
   Kelvin.
4. `POST /marketing/send-sequences` on production MKT-O5, which sent it through Resend.

| Check | Result |
|---|---|
| audit_log row | `7b639294…`, `win`, `{touch: 1, resend_id: 01a1bce6-cfed-7bad-83f9-2edabef9b96f, email_grade: valid, test_send: true}` |
| Resend | `last_event: delivered`, to `kdav2k5@gmail.com` only, Reply-To Kelvin |
| Footer | Mailing address plus an unsubscribe URL with an HMAC token |
| One-click unsubscribe | `List-Unsubscribe` and `List-Unsubscribe-Post: One-Click` are on the payload builder. Both routes are live (a bad token returns 400). Not clicked, because that would suppress your address. |
| Warm-up | Test sends do not start the clock. Today's ramp cap is 10, and min(env 12, ramp 10) = **10/day** |

The test sequence was closed after touch 1, so touch 2 will never fire. The test lead is
`disqualified`.

**The warm-up ramp** (`core/email_compliance.py`) runs from the first real prospect send:

| Days | Cap per day |
|---|---|
| 1–7 | 10 |
| 8–14 | 15 |
| 15–21 | 20 |
| 22–28 | 25 |
| After that | 30 |

`MARKETING_DAILY_SEND_CAP` remains a hard ceiling on top. It is **12** on prod today, which
would hold weeks 3 and later below the ramp (see your to-do list).

## 6. Follow-ups, replies, bounces, briefs (Phase 6)

**Touches 2 and 3.**

- touch 2 goes out on day 3, then touch 3 on day 7. Both share `_run_follow_up`.
- They stop on:
  - **reply:** a replied sequence's status leaves the polled set;
  - **unsubscribe:** suppression is re-checked before every touch;
  - **bounce:** the lead's status is checked, and `bounced` stops the sequence.
- Migration 070 adds `touch_2_sent`, `touch_3_sending` and `bounced`.
- Proven on staging: all three sender stages ran.

**Bounces and complaints:** `POST /webhooks/resend` (Svix-signed).

- A hard bounce or complaint suppresses the address, marks the lead `bounced` or
  `unsubscribed` (which feeds the 3% pause), and stops in-flight sequences.
- A soft bounce is logged only.
- The endpoint refuses with 503 until its secret is set (see your to-do list).

**Replies:** `POST /marketing/outreach/sequences/{id}/replied` marks an email sequence
replied. Replies land in your inbox, which the service cannot read, so this manual action is
the detection.

**Positioning briefs,** both at `pending_review` and **not approved:**

- **Consulting v2** (`52ba5dd4…`) fixes the unsourced claims:
  - Removed the "$X K/mo" placeholder, the $8K–$18K range, "80–120 hours/month",
    "$48K–$60K" and "weeks vs. quarters".
  - Dropped the trade-press statistics.
  - Repositioned from "agentic AI platform architect" to what outreach actually sells:
    contract cloud and platform work for companies hiring for those roles.
  - Evidence is internal and re-queryable (scan run ids). No price is stated: the rate is
    yours to set.
- **Cloud Decoded v1** (`20368ca6…`): the first brief for this product.
  - The $299/$699/$2,499 tiers come from the platform `CLAUDE.md`. Confirm them against
    Stripe before you approve.

## 7. LinkedIn (Phase 7, `kdavis-agentic-platform` `99b3081`)

**The new mix.**

- `CONTENT_MIX_RATIO` is now 50% Buyer Pain (`pillar_6`), 25% Proof (`pillar_7`) and 25%
  Authority & Opinion (`pillar_8`).
- Pillars 1–5 are no longer in the mix, but still resolve for old rows.
- The stored pillar comes from the slot, never from the model's own label.
- A pillar with no source material that month is dropped, not back-filled from another.

**Voice.** Posts are written to the budget owner, framed in dollars, hours or risk, with no
invented metrics. The prompt no longer implies cloud work at Boeing or Honeywell.

**CTAs.**

| Category | Closing line |
|---|---|
| Buyer Pain | "If this is the gap on your team right now, a 15-minute call is the fastest way to see whether I can close it. Link in the comments." |
| Proof | "Want to see it running against your own stack? Link in the comments." |
| Authority & Opinion | The existing working-with-me line |

**The 5 unpublished posts** were re-classified by pillar label only, with copy untouched: 1
to Buyer Pain and 4 to Authority & Opinion. The Kubernetes certification post is flagged
"IC/job-seeker audience — recommend reject". All 5 have `scheduled_for` dates in July 2026,
already past, so reschedule any you approve.

**Published posts: 46 before, 46 after,** pillars unchanged.

**Five Buyer Pain samples** are in the review queue (`pending_review`, Tier 2, each with an
image): `3ee226cd`, `7e10d7dd`, `d6d30739`, `010c9777` and `ff1c29bd`.

- Each carries a `hitl_notes` flag listing illustrative specifics to verify or cut, such as
  "four months", "20 minutes" becoming "three hours", and "2am".

**Tests:** `tests/test_mkt_li1_buyer_mix.py` (mix 6/3/3 for 12 posts, CTAs, voice, pool
routing) and the updated `test_mkt_li1_on_demand.py`.

## 8. Kelvin's to-do

1. **Review the drafts.**
   - 7 outreach drafts in Approve Drafts: FreedomPay, SandboxAQ, Aerones (email), 15Five,
     Pylon, Loop Returns, and Polygon Labs (check its contact first).
   - Each LinkedIn draft needs a profile URL before it can be approved.
2. **Review the 10 LinkedIn posts.**
   - The 5 Buyer Pain samples: cut the flagged specifics.
   - The 5 re-classified posts: reject the Kubernetes certification post, and reschedule
     the rest.
3. **Review the 2 positioning briefs** (consulting v2 and Cloud Decoded v1). Set the
   consulting rate, and confirm the Cloud Decoded prices against Stripe.
4. **Turn on bounce handling.**
   - In Resend, add a webhook to
     `https://mse-api-production-f8bd.up.railway.app/webhooks/resend` for `email.bounced`
     and `email.complained`.
   - Set the signing secret as `RESEND_WEBHOOK_SECRET` on mse-api (staging first).
   - Until then, bounces are not caught automatically.
5. **Decide `MARKETING_DAILY_SEND_CAP`.** It is 12 today. Raise it to 30 when you want the
   ramp to reach steady state; the ramp governs below that.
6. **Use "Mark replied"** (`/marketing/outreach/sequences/{id}/replied`) when a prospect
   replies by email. It still needs a button in the dashboard (see Remaining gaps).

## 9. Remaining gaps

**Volume.**

- **The source drives volume.** About 350 ATS boards produce roughly 20–30 buyer-grade
  leads a week. Reaching 100 needs more board tokens (Greenhouse, Lever, Ashby discovery),
  not looser rules.
- **Only 1 of 22 leads has a verified buyer email.**
  - SMTP verification works on Railway: 6 `valid` grades today, and the workspace is on
    the Pro plan.
  - But most buyers either have no address found or sit on catch-all domains.
  - Most outreach will therefore be manual LinkedIn until buyer email discovery improves.

**Lead quality.**

- **Headcount is unknown on most leads.** Size is rejected only on open-role evidence, so
  Autotrader, Wikimedia and FloQast pass, and some are larger than the target band. A
  headcount enrichment source would close this.
- **Wrong-person matches.** Polygon Labs' contact came from a scraped title with another
  person's name in it. Contact discovery pairs a nearby name and title, and still needs a
  human to check.

**Checks that don't exist yet.**

- **The LinkedIn agent has no code-enforced invented-metric check.** Spelled-out and
  vignette specifics ("four months", "2am") get through the prompt rule. MKT-O2's
  `unsourced_metrics` could be ported, extended to number words.
- **No dashboard button for "Mark replied"** on email sequences. The API exists; the CEO
  dashboard Conversations lane needs the button.
- **No automatic reply detection.** That would need Resend inbound or mailbox access.

**Other prompts not rewritten.**

- **The builder, launch and mention prompts** in MKT-LI1 were not rewritten for the budget
  owner. Only the evergreen voice was, as the brief scoped.
- **The CEO dashboard's Vercel deploy** of the new fire menu was not verified from here.

**Open item.**

- **The good companies suppressed in Phase 1** (Roboflow, Customer.io, Lumafield and
  others) stay suppressed until you decide which to restore.

## Commits

`kdavis-microsaas-engine` (`main`):

| Commit | Change |
|---|---|
| `d7184a3` and earlier | Phases 0–2 |
| `b5ac1a3` | Consultancy competitors |
| `ee4d0d3` | MKT-O2 buyer prompts and invented-metric refusal |
| `4a274b4` | Warm-up ramp, plus Resend id and grade on audit rows |
| `30217da` | Touch 3, Resend webhook, Mark replied, migration 070 |
| `8c2d7de` | Resold domains and recruiting agencies |

`kdavis-agentic-platform` (`master`):

| Commit | Change |
|---|---|
| `99b3081` | MKT-LI1 v3.0 |
