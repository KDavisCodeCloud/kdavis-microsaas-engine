# Lead Source Expansion — Phase 0 Baseline

**Taken:** 2026-11-15 ~03:40 UTC, read-only, against the production database (`.env` →
prod Supabase) and the deployed code on `main` (`abadac1`).
**Scope:** Cloud Decoded (`777a1852…`, "CD") and THD Agentic Systems Consulting
(`9b6c8f36…`, "CO"). The partner lane is out of scope; it is listed only for its Brave usage.

Every number below is from a live query; the scripts are in this session's scratchpad
(`ls/p0.py`, `ls/p0b.py`).

---

## 1. Current sources

| Source | Product | Cadence | Trigger | Brave? | Code |
|---|---|---|---|---|---|
| ATS scout (`cloud_decoded_job_signal`) | CD | weekly, Sun 13:00 UTC | n8n → `POST /leads/scout` | yes, ≤40/run | `mkt_lead_finder.run_scraper_v2_scout` → `company_first_sourcing.find_company_first_signals` |
| ATS scout (`job_posting_signal`) | CO | weekly, Sun 13:00 UTC | same | yes, ≤40/run | same |
| Hacker News (`hn_thread`) | CO | monthly, on the 3rd | in-process `lead_schedule` | no | `hn_scout.find_hn_leads` (official HN Firebase API) |
| Partner scout / VC scout | partners | monthly, 1st | `lead_schedule` | partner ≤20 | out of scope |
| Pain rescan | CD, CO | weekly, after the scout | `lead_schedule` | no | re-reads leads; finds no companies |
| `brave_search` (v1) | CD | retired 2026-09-27 | — | — | 55 rows, all `rejected_wrong_icp` |

### ATS slugs (`mse_ats_board_tokens`)

The table the prompt calls `ats_slugs` already exists (migration 057): provider, token,
company, company_domain, created_at (first seen), last_fetched_at (last checked),
open_role_count, last_status, consecutive_failures, discovered_from.

**643 tokens**, unique on (provider, token), shared by both products:

| Provider | Tokens | ok | empty | failed |
|---|---|---|---|---|
| Greenhouse | 247 | 220 | 16 | 11 |
| Ashby | 167 | 158 | 4 | 5 |
| Workable | 124 | 96 | 25 | 3 |
| Lever | 105 | 95 | 7 | 3 |
| **Total** | **643** | 569 | 52 | 22 |

- **Pollable** (fewer than 3 consecutive failures): 635.
- **Discovered from:** `brave_ats_discovery` 235, `vc_portfolio` 74, NULL 334 (rows the
  fetch-upsert wrote, which never sets the column).
- **First seen by week:** 295 on 10-01, then 7 / 29 / 21 / 40+54 / 23 / 117 / 57 a week.
  The 2026-11-08 run added **57**.
- **Size:** median board has 19 open roles. **187 boards have more than 40 open roles**, so
  the size proxy rejects them on every run while the scout still polls them every week.
- SmartRecruiters and Recruitee are **not supported** (the provider CHECK allows only the
  four above).

### Refresh logic (how a run reads slugs)

1. `load_cached_board_refs(limit=200)` loads the **200 least-recently-fetched** tokens, then
   drops boards with 3+ failures. Every run in the window reports exactly
   **`board_tokens_from_cache: 194`**. The cache holds 643, so a normal week reads under a
   third of it from cache.
2. Brave discovery: `ATS_DISCOVERY_TEMPLATES` (5 hosts) × `SCRAPER_V2_KEYWORDS` (CD 4
   keywords, CO 5), capped at **20 queries**. So CO never reaches its 5th keyword
   ("devops engineer"). Each query asks for **10 results** (Brave allows 20) and always reads
   **page 1**, so the same queries return mostly the same boards every week:
   `ats_urls_seen` ~190, `board_tokens_discovered` ~150–176, of which ~57 were new on 11-08.
3. Union → fetched **sequentially** within a 900 s wall clock, 1–2.5 s pause between
   boards. Runs take 700–1,200 s and poll 265–361 boards.
4. The CD and CO runs start together at 13:00 and read the **same 200** LRU tokens, so each
   board is polled twice and the two runs together still cover only ~360 distinct boards.

---

## 2. Last 4 weekly runs (`funnel_stats`)

ATS scout runs (the 11-08 20:19 runs were the post-fix re-run, kept for completeness):

| Run | Date | Product | Boards polled | Companies considered | No matching role | Duplicate company | Competitor excluded | Size proxy (>40 roles) | No domain | **Stage 1 passed** | Contact found | Brave |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `e881987b` | 11-08 20:19 | CD | 361 | 361 | 176 | 56 | 69 | 61 | 5 | **3** | 2 | 32 |
| `958e5efd` | 11-08 20:19 | CO | 346 | 346 | 178 | 57 | 89 | 46 | 2 | **1** | 1 | 27 |
| `d19ccc99` | 11-08 13:00 | CD | 346 | 346 | 161 | 47 | 70 | 58 | 4 | **17** | 6 | 37 |
| `9f065085` | 11-08 13:00 | CO | 324 | 324 | 159 | 45 | 75 | 46 | 2 | **19** | 6 | 37 |
| `c008ca35` | 11-01 | CD | 351 | 351 | 175 | 39 | 74 | 55 | 3 | **20** | 8 | 39 |
| `8c5de95a` | 11-01 | CO | 299 | 299 | 157 | 42 | 74 | 40 | 0 | **8** | 1 | 28 |
| `71ed4e2e` | 10-25 | CD | 293 | 293 | 142 | 42 | 57 | 54 | 5 | **5** | 0 | 34 |
| `ed1737cd` | 10-25 | CO | 265 | 265 | 130 | 43 | 61 | 41 | 3 | **6** | 0 | 31 |

HN: `4870b507` (11-03) read 163 posts and kept 18 (130 had no infra term, 53 were
dead or deleted, 8 were freelancers selling, 4 were duplicates, 1 was a consultancy).
`7b9cde43` (10-19) kept 34.

**Where companies drop out, in order of size:**
1. **No matching role, ~50%.** The board has no DevOps/SRE/platform role this week.
   Expected; it is the signal filter.
2. **Competitor excluded, ~20%.** Consultancies, MSPs, staffing, resellers and cloud
   partners. Expected.
3. **Size proxy, ~15%.** More than 40 open roles. These boards are re-polled weekly for
   nothing.
4. **Duplicate company, ~13%.** Already in `mse_leads` under any status, or suppressed.
   201 companies sit in `mse_suppressed_companies`, 145 of them as
   `purged_20261107_contact_not_buyer`: the *contact* was wrong, not the company.
5. **No domain, ~1%.**

**Stage 1 passed: 3–20 a run.** The 13:00 runs of 11-08 found 36 across both products, and
the post-fix re-run 7 hours later found 4. The registry is drained within a week. That
is the volume ceiling the prompt describes.

---

## 3. Find the Buyer

**Count: 15 cards** (CD 8, CO 7; `contact_status='pending'`, not purged), across
**11 distinct companies**: Chaidiscovery, Sunday, Autotrader and Cape appear under both
products. The prompt's "16 or more" was close. The 2026-11-08 report counted 21 by
including 6 leads that have a named contact but an unverified email. Those are now
`pending_email` / `manual_linkedin`, not on the Find the Buyer card.

| Product | Company | Domain | Age | Why no buyer |
|---|---|---|---|---|
| CO | Pinely | pinely.com | 6 d | no person named; 0 automated attempts |
| CO | CARMA | carma.com | 6 d | no person named; 0 attempts |
| CO | Autotrader | autotrader.com | 6 d | no person named; 0 attempts |
| CO | Cape | cape.com | 6 d | no person named; 0 attempts |
| CO | Chaidiscovery | chaidiscovery.com | 6 d | 1 attempt, nothing found |
| CO | Multiplylabs | multiplylabs.com | 6 d | 1 attempt; a pattern email graded `invalid` |
| CO | Sunday | sunday.ai | 6 d | 1 attempt, nothing found |
| CD | Chaidiscovery | chaidiscovery.com | 6 d | 0 attempts |
| CD | Wikimedia | wikimedia.org | 6 d | 1 attempt, nothing found |
| CD | Floqast | floqast.com | 6 d | 1 attempt, nothing found |
| CD | Oneapp | withoneapp.com | 6 d | 0 attempts |
| CD | Sunday | sunday.ai | 6 d | 1 attempt, nothing found |
| CD | Autotrader | autotrader.com | 6 d | 1 attempt, nothing found |
| CD | Cape | cape.com | 6 d | 0 attempts |
| CD | Centralreach | centralreach.com | 6 d | 0 attempts |

All 15 came from the 2026-11-08 13:00 run. Every older card was purged on 11-07.
**Why they have no buyer:**
- The scout's buyer search runs only inside the scout, under a Brave decision-maker budget
  of 5–14 queries per run. The 5-lead retry pass covers earlier leads.
- Free company-page discovery (`/about`, `/team`, `/leadership`) found a person on 1–11
  pages per run, and mostly 404s.
- Nothing else tries: no filing data, no GitHub, no HN poster.

Autotrader and Wikimedia are far above both size bands, and nothing in the data records a
headcount to stop them (`headcount_band` is NULL on all 15).

### HN leads: all purged

All **52** `hn_thread` leads are `rejected_wrong_icp` (`wrong_icp_purge_20261107`). Run
through today's `buyer_gate.evaluate_lead`, **40 of them would pass to Find the Buyer**. The
other 12 have no domain. They were purged in bulk with the old leads. Restoring them is
Kelvin's call, the same as the good companies the 11-07 report flagged. This build does not
restore them.

---

## 4. Brave usage

| Month | Used | Notes |
|---|---|---|
| 2026-10 | 598 | |
| 2026-11 (to date) | **220** | 87 on 11-01 and 133 on 11-08 (the 11-08 total includes the re-run) |

- **Stop threshold:** 720 (80% of 900, `brave_budget.py`).
- **Remaining Sunday scouts in November:** 11-15, 11-22 and 11-29, about 70 each, so about
  210 more. That leaves about **290** under the threshold for the new sources this month.
- **Per-source usage is not recorded.** `usage_events.brave_search_queries_used` carries
  `{month, count}` only.

---

## 5. What this means for the build

- **Phase 1 is mostly an existing table to extend, not a new one.**
  - Add SmartRecruiters and Recruitee.
  - Read *all* pollable slugs each week instead of 200, fetched in parallel per provider so
    each host is still paced.
  - Spend Brave only on new slugs: wider role terms, 20 results per query, and a rotating
    result page.
  - Stop re-polling oversize boards every week.
- **Phase 2 already exists** (`hn_scout.py`). The missing pieces are:
  - contact parsing (email, name and role) from the post;
  - the poster-as-candidate-buyer rule;
  - the first-weekday cadence.
- **Phases 3–5 are new:** Form D, the Wellfound signal and the buyer resolver.
- **Find the Buyer gate:** run the resolver on the 15 cards above.
