# Lead Source Expansion + Buyer Resolver — Report

**Date:** 2026-11-15 (03:40–05:00 UTC, before the Sunday 13:00 UTC scouts).
**Scope:** Cloud Decoded (`777a1852…`) and THD Agentic Systems Consulting (`9b6c8f36…`).
**Baseline:** [`00-baseline.md`](00-baseline.md). Every number below comes from the live prod
database or a live run.

**Where it runs now.** Each commit went to staging first, then prod, by SHA. Both report
their commit on `/health`.

| Service | Environment | Commit |
|---|---|---|
| mse-api | staging | `e0f180f` |
| mse-api | production | `e0f180f` |
| ceo-dashboard (Vercel) | production | platform `db50971` |

**Migrations 072–075** were proven on the staging database before prod. Prod applied them
on boot. Each one only adds tables, nullable columns or a view, or widens a CHECK. No
column was dropped or altered, and no row was rewritten.

---

## 1. Before and after

### Slugs (`mse_ats_board_tokens`, exposed as the `ats_slugs` view)

| | Before (03:40) | After (05:00) |
|---|---|---|
| Total slugs | 643 | **964** |
| Greenhouse | 247 | 326 |
| Ashby | 167 | 227 |
| Workable | 124 | 198 |
| Lever | 105 | 143 |
| Recruitee | not supported | 63 |
| Read per scout run | 200 LRU (`board_tokens_from_cache: 194`) | **all pollable**, oversize boards monthly |
| Full read time | 700–1,200 s for ~350 boards | ~315 s est. for 449 boards (live sample: 102 boards in 52 s) |

The 321 new slugs:
- **314 from weekly slug discovery** (36 Brave queries, about 8.7 new slugs per query). The
  old discovery found about 57 new slugs a week from 40 queries.
- **7 from Wellfound.**

On the first scout run each new slug is fetched and qualified through the normal rules.

### Companies, buyers and emails (per stage, per source)

The first run that uses the new slugs is today's **13:00 UTC scout**. The before/after
company counts for this week are therefore its numbers. They will appear in:
- the Loop Map Step 1 card;
- the `source_yield` field of `GET /marketing/outreach/summary`.

**Last 7 days, at 05:00 UTC** (before that run), in the format the digest uses:

```
Lead sources, last 7 days (target 40 companies / 15 named buyers / 10 verified emails):
  total            : 22 / 7 / 1
  ats_cache        : 16 / 7 / 1
  vc_portfolio     : 6 / 0 / 0
```

**Staging proof of the new path**, a CD scout over 101 newly discovered slugs:
- Discovery was skipped (`discovery_budget: 0`).
- 101 boards were read in parallel.
- **16 companies passed Stage 1.**
- 14 Brave queries, 264 s.
- Staging has no dedup history, so prod will drop some of these as duplicates.

| Source | What it did live | Yield |
|---|---|---|
| ATS slug discovery (Phase 1) | 36 queries, page 1, 20 results each | 706 results, 695 ATS URLs, 462 slugs, **314 new** |
| HN (Phase 2) | November thread, read-only dry run | 262 posts, **27 kept** (was 18), 3 candidate buyers, 12 with an email |
| SEC Form D (Phase 3) | 14 index days (Oct 26 – Nov 13) | 3,640 Form D/D/A, 2,436 skipped as funds/SPVs, **1,204 read, 288 tech**, 1 pipeline match, 0 seeded |
| Wellfound (Phase 4) | 8 queries, titles only | 112 company names, 80 tried, **7 new verified slugs**, 72 unresolved |
| Buyer resolver (Phase 5) | the 15 Find the Buyer cards | 0 auto-resolved, **2 pre-filled**, 13 manual |

---

## 2. Brave usage

**This month: 276 of 900** (stop threshold 720). A ~35-query scout run projects to 311.

| Source | Allocation / month | Used (Nov) |
|---|---|---|
| (unattributed, before this build) | — | 220 |
| `slug_discovery` | 160 | 42 (36 prod + 6 staging proof) |
| `ats_scout` | 200 | 14 (staging proof) |
| `wellfound` | 40 | 10 (8 prod + 2 staging proof) |
| `partner_scout` | 20 | 0 |
| `buyer_snippet` (flag off) | 60 | 0 |

- **Every run is under the 300-per-run cap** (`brave_budget.MAX_QUERIES_PER_RUN`). The
  largest is 36.
- **Usage is now logged per source.** Each `usage_events` row carries
  `{month, count, source}`.
- **Staging proofs share the real Brave account,** so their queries were written to the prod
  ledger too.

**What changes the weekly spend:**
- The scouts now skip their own discovery whenever slug discovery has run that week. That
  saves about 40 queries a week.
- Slug discovery costs 36 a week.
- Net weekly Brave spend is roughly flat. It now buys new slugs instead of re-finding old
  ones.

---

## 3. Resolver results

**Gate: the 15 real Find the Buyer cards on prod** (11 companies; 4 appear under both
products).

| Outcome | Count | Companies |
|---|---|---|
| Auto-resolved (verified email + buyer title → contact → MKT-O2 → HITL) | **0** | — |
| Pre-filled (name found; unverified email or ambiguous title) | **2** | Multiply Labs, CentralReach |
| Still manual | **13** | Pinely, CARMA, Autotrader ×2, Cape ×2, Chai Discovery ×2, Sunday ×2, Wikimedia, FloQast, OneApp |

**All examples found.** The brief asked for 10; these are all there are.

1. **Multiply Labs (consulting): Alice Melocchi, Co-Founder & CSO.**
   - Source: `company_page` (`https://multiplylabs.com/team`), medium confidence.
   - An SMTP pattern probe graded `unknown`, so the card is pre-filled, not saved.
2. **Multiply Labs: Federico Parietti, Chief Executive Officer.**
   - Source: `form_d` (Form D filed 2026-11-05, $52,250,260 sold). The filing names him
     "Chief Executive Officer, President, and Secretary".
   - Title verdict `ambiguous`: a CEO is a buyer only at under 50 people.
   - Multiply Labs also got `funding_date` 2026-11-05 and a +0.10 consulting intent boost.
3. **Multiply Labs: Manny Guerrero and Enrico Racca, "Executive Officer (Form D)".**
   - Source: `form_d`, low confidence.
   - The filing gives no title, so the buyer gate reads them as ambiguous.
4. **CentralReach (Cloud Decoded): Yagnesh Vadgama, CISO.**
   - Source: `company_page` (`https://centralreach.com/leadership`). Pre-filled.
5. **HN dry run, ThreatMark: Krystof Hilar, Co-Founder.**
   - Source: `hn_poster`, medium confidence. The post itself named him.
6. **HN dry run, SadServers and Proofline: "founder".**
   - Source: `hn_poster`, low confidence. The poster said they were the founder but gave no
     name, so no name was invented.

**Why 13 stayed manual.** Every source was tried and logged on each card
(`buyer_resolution.sources_tried`):
- **Form D:** no filing for these names in the 14 days read so far. The 180-day backfill
  continues on weekdays, at 10 index days per run, and is complete in about 3 weeks.
- **Team pages:** 404, or no name-and-title pair on the page.
- **GitHub:** no org whose own `blog`/`email` is on the company's domain. The source was
  checked live against PostHog (org verified, founders found), so the zero is real.
- **HN poster:** no HN leads are in the backlog. All 52 were purged on 2026-11-07.
- **Search snippet:** off by default, as the brief requires.

**Quality fixes the gate forced** (`e0f180f`):
- "Coming Soon / CEO" from Multiply Labs' team page had been suggested as the buyer. The
  name parser predates this build, and its output now reaches the card. Placeholder copy is
  now refused.
- A Form D sentence clarification had been used verbatim as a title. Only its title phrase
  is now used.

---

## 4. Blocked, skipped and changed, and why

**SmartRecruiters: STOPPED**
- `api.smartrecruiters.com/robots.txt` is `User-agent: * / Disallow: /`, with an allow only
  for LinkedInBot. That is a brief stop condition, so the provider is not added at all.
- Workable was already supported. Recruitee was added, and its robots.txt is honoured per
  company host.

**EDGAR company search: not used**
- `www.sec.gov/robots.txt` disallows `/cgi-bin`, which rules out `browse-edgar`.
  `efts.sec.gov` refused a robots.txt read and is not a documented API.
- Form D therefore uses only the daily index and `primary_doc.xml`. Pipeline companies are
  matched against our own index of filings by name.
- Requests carry a declared User-Agent with a contact email, at no more than 5 per second.
  One 403 came back on 1,219 requests.

**Form D seeding: 0 of 80 resolved**
- Tech filers are mostly very small companies with no public ATS, or one whose slug isn't
  their legal name.
- A guessed board is accepted only when it names the company. The seed stays on, but its
  yield is near zero.

**Form D sizes: none**
- Form D has no headcount field, so size comes from a headcount stated in the JD.
- `size_estimate_method` records how each size was found.
- A team-page headcount was not built: a leadership page lists 5–15 people, so a count from
  it would understate company size.

**Cloud Decoded freshness rule: changed**
- The 2026-10-01 rule downweighted CD roles open 31–60 days. The brief's "roles open 30+
  days rank higher" supersedes it.
- CD now gives full weight across the 60-day window.
- Both products add +0.10 intent for a role open 30 or more days, measured from Greenhouse's
  `first_published`; the code used `updated_at` until now.

**8am email: not applied to the live n8n**
- The API now serves `source_yield_lines`, and the exported workflow JSON in the repo prints
  them (`n8n/exported-live/linkedin-outreach-daily-8am-mst.json`).
- The live n8n was **not** edited. That node chain also triggers the 8am MKT-O2 drafting,
  n8n has no staging environment, and an import or DB edit means deactivating or restarting
  the workflow.
- The Loop Map Step 1 card shows the yield live already.

**HN leads purged on 11-07: not restored**
- 40 of the 52 purged leads would pass today's gate. Restoring them is your call, the same
  as the good companies flagged in the 2026-11-08 report.

---

## 5. What you need to do (Kelvin)

1. **Look at today's 13:00 UTC run.** It is the first scout over 964 slugs.
   - Check the Loop Map Step 1 card: the "This week: X companies / Y named buyers / Z
     verified emails" line, against the 40/15/10 target.
   - Check Find the Buyer: two cards now show a suggested buyer with a **use suggestion**
     button. It fills in the name and title; you still paste the profile URL.
2. **Apply the 8am digest change** (6 lines), or tell me to.
   - In n8n, open *LinkedIn Outreach — Daily 8am MST* → **Build Digest Body**.
   - Paste the `jsCode` from `n8n/exported-live/linkedin-outreach-daily-8am-mst.json`, then
     save.
   - It is backward-compatible: it prints the yield block only when the API sends one.
3. **Optional: set `SEC_EDGAR_USER_AGENT`** on mse-api, as `"THD Agentic Systems LLC
   you@monitored-inbox"`.
   - Until you do, it uses the business sending address from `RESEND_FROM_EMAIL`, which the
     SEC accepts.
   - Use an inbox you read if you want SEC to be able to reach you.
4. **Decide on the search-snippet resolver.**
   - It is off by default (`ENABLE_SEARCH_SNIPPET_RESOLVER`). It is the source most likely
     to name buyers for the 13 manual cards.
   - Cost: about 1 Brave query per card a month (its share is 60).
   - It reads Brave result titles only and never opens a page.
5. **Decide on restoring the 40 purged HN leads** (see section 4).

## 6. What runs on its own now

| Job | When | Brave |
|---|---|---|
| `slug_discovery` | Saturdays from 12:00 UTC (catch-up Sun before 12:00) | ≤36 |
| ATS scouts (n8n) | Sundays 13:00 UTC; read every pollable slug, no discovery | domains + buyers only |
| `form_d` | weekdays from 11:00 UTC; 10 index days per run until 180 days are filled | 0 |
| `buyer_resolver` | daily from 14:00 UTC (before the 8am MST drafting); rechecks a card after 14 days | 0 (snippet off) |
| `hn_scout` | 2nd weekday of the month from 14:00 UTC | 0 |
| `wellfound_signal` | 1st–5th of the month from 15:00 UTC | ≤8 |

## Commits

`kdavis-microsaas-engine` (`main`): `99e94fc` (phase 0), `3a3c327` (1), `5d79556` (2),
`60e8edf` (3), `fbb7dc2` (4), `bb2ee16` (5), `10bd622` (6), `04e0b4c` + `a2cd2f9` (7), and
`e0f180f` (gate fixes).
`kdavis-agentic-platform` (`master`): `db50971` (Find the Buyer suggestion + signal chips).

**Rollback** (mse-api prod deployment ids, newest first):
- `66dba6c4` (`e0f180f`)
- `15274d2d` (`a2cd2f9`)
- `eb1acb54` (`10bd622`)
- `c7d578cb` (`5d79556`)
- `9d1856ea` (`abadac1`, before this build)

**Tests:** 1,928+ passed. The 3 `test_leads_api` failures also fail on `abadac1`.
