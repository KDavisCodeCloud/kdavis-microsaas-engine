# EXECUTION_ORDER.md — Distribution Engine (DIST)

**Subsystem:** DIST — pull-side distribution, sibling to MKT-* (push-side outbound)
**Repo:** `kdavis-microsaas-engine`
**Stack:** FastAPI · LangGraph · Supabase/Postgres · n8n (self-hosted) · Next.js 15 App Router · Vercel · Python 3.11+ · TypeScript
**Status:** Phases 0–7 shipped 2026-08-30/31, live on `microsaas-prod`. Phase 8 gated (see 8.0).
**Owner gate:** Kelvin (Tier 2/3 HITL until Q4 2027)

> Saved here 2026-08-31 — the original spec was pasted directly into a Claude Code session and built from, but never committed as a file until this pass caught the gap while fixing the role-value defect below. Role values in every SQL block here are `'admin'`, corrected from the original spec's `'owner'` (see CLAUDE.md's "Canonical role value" note) — three overnight forks (Phases 0, 1, 7) independently hit this same bug against the live `'admin'`-role account before it was caught.

---

## Why this exists

MSE solves supply — it decides what to build. Nothing in the stack solves demand. The existing MKT-* swarm is entirely push: find leads, sequence, send, gate, log. It is throttled by Google query and SMTP verification rate limits, which means it produces roughly 1–2 customers per product per month and does not improve with more products. At ten products it gets worse, not better, because every pipeline competes for the same throughput ceiling and the same HITL window.

DIST is the pull side: owned surfaces a stranger lands on, plus the measurement layer that says which surfaces worked.

**Design invariant:** DIST never generates a page for a product whose positioning brief is unapproved. Generation at scale in the wrong direction is worse than no generation. This is enforced in code, not convention — see Phase 0.

---

## Phase sequence

| Phase | Name | Blocks | Est. | Status |
|---|---|---|---|---|
| 0 | Positioning gate | everything | 2 sessions | ✅ live |
| 1 | Attribution + funnel instrumentation | 4, 6 | 1 session | ✅ live |
| 2 | Indexation monitor | 4 | 1 session | ✅ live |
| 3 | Competitor registry + freshness loop | 4 | 1 session | ✅ live |
| 4 | Surface generator | 5 | 3 sessions | ✅ live |
| 5 | HITL tiering + n8n wiring | — | 1 session | ✅ live |
| 6 | Backfill existing products | — | 2 sessions | ✅ reviewed |
| 7 | Support drafting layer | — | 2 sessions | ✅ live |
| 8 | Unified HITL queue + CRM surfaces | — | 3 sessions | gated, see 8.0 |

Measurement (1, 2) lands before generation (4) so the generator's first output is legible on day one.

---

# Phase 0 — Positioning Gate

**Blocking. No other phase may proceed without it. This is the phase that exists because of Small Portfolio Hub.**

## 0.1 — Failure mode being corrected

SPH was positioned against Buildium, the category leader. The buyer was actually choosing between Innago, TurboTenant, Avail, and Baselane — all $0 to the landlord. MSE research validated that a market existed and that Buildium had exploitable pricing gaps. It did not enumerate the substitute set, so the entire page argues a price advantage against a competitor the buyer never considered.

**Generalized rule, enforced by this phase:**

> Your competitor is whatever the buyer does if you don't exist. That set always includes doing nothing, and often includes something free. The category leader is frequently not in it.

## 0.2 — Schema

```sql
create table mse_positioning (
  id                  uuid primary key default gen_random_uuid(),
  product_id          uuid not null references mse_products(id) on delete cascade,
  version             int  not null default 1,

  icp                 text not null,
  trigger_event       text not null,
  substitute_set      jsonb not null,
  wedge               text not null,
  wedge_type          text not null check (wedge_type in ('structural','execution','invalid','temporary')),
  wedge_evidence      jsonb not null,
  price_rationale     text not null,
  kill_criteria       text not null,
  moat_risk           boolean not null default false,
  wedge_review_status text not null default 'current'
                        check (wedge_review_status in ('current','needs_rereview')),
  next_review_due     date,
  gross_margin_at_ceiling numeric,
  margin_floor        numeric not null default 0.70,
  margin_override_reason text,

  status              text not null default 'draft'
                        check (status in ('draft','pending_review','approved','rejected','superseded')),
  approved_by         text,
  approved_at         timestamptz,
  created_at          timestamptz not null default now(),

  unique (product_id, version)
);

create unique index mse_positioning_one_approved
  on mse_positioning (product_id) where status = 'approved';

alter table mse_positioning enable row level security;

create policy mse_positioning_tenant_read on mse_positioning
  for select using (
    (auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'
  );
```

Approval is enforced by a `SECURITY DEFINER` function (`approve_positioning`) checking `role = 'admin'`, plus a trigger rejecting any direct `UPDATE ... status='approved'` that didn't go through it. See `supabase/migrations/20260830000029_dist_phase0_positioning.sql` for the real, live implementation. `wedge_type`'s third value and the `wedge_review_status`/`next_review_due` columns were added by `supabase/migrations/20260831000038_dist_wedge_taxonomy.sql` — see §0.4a. `wedge_type`'s fourth value (`invalid`) and the viability-gate columns (`gross_margin_at_ceiling`, `margin_floor`, `margin_override_reason`) were added by `supabase/migrations/20260831000039_dist_gate_hardening.sql` — see §0.4b and §0.4c.

## 0.3 — `substitute_set` contract

Array, minimum three entries, **must** include one with `kind = 'do_nothing'`. `kind` ∈ `free_tool | paid_tool | do_nothing | manual_process | in_house_build | agency_service`.

**`configurable_options` requirement (added 2026-08-31, migration/prompt change, no schema change — enforced in `positioning_researcher.py`).** Every entry with `kind` of `free_tool` or `paid_tool` must carry a `configurable_options` array: `[{"option", "available", "cost_shift", "source_url", "verified_at"}]`, researched against the substitute's pricing page, FAQ, help docs, and settings documentation — not just its advertised default price. An empty array is only valid alongside an explicit `researched: true` marker, so "looked and found none" is distinguishable from "never looked"; DIST-P1 raises and writes nothing otherwise. Proven necessary by small-portfolio-hub v4 (2026-08-30/31): the brief's entire wedge rested on "substitutes must charge the tenant a fee," never checked against Innago's and TurboTenant's real, existing account-setting toggle that lets the landlord absorb that fee instead — a real, sourced, distinct piece of information P1 never looked for because nothing required it to.

## 0.4 — The wedge taxonomy (amended 2026-08-31, migration 038)

The original 2-tier test (`structural` pass / `temporary` fail) rejected every real brief submitted to it — SPH, DecodedSix, and all three TradesDesk verticals all came back `temporary`. A filter that rejects everything is miscalibrated, not doing its job: `structural` is a **moat** test, and almost no micro-SaaS at this scale clears one (Jobber doesn't, Innago doesn't). A moat matters at $10K MRR and at exit; it is not required to clear the ~33-40-customer $4K first bar. This amendment widens the **pass** condition only — the original three structural questions are unchanged, and `temporary` is not weakened.

`wedge_type` is now one of three values:

| Tier | Meaning | Gate outcome |
|---|---|---|
| `structural` | Closing the gap breaks the substitute's revenue model or costs them a segment | Pass. Full surface generation, all archetypes. |
| `execution` | They could close it and demonstrably have not; the buyer is underserved today, sourced | Pass. Full surface generation, same as `structural`. `moat_risk = true`. Mandatory re-review every 2 quarters (`next_review_due`). |
| `temporary` | They will close it on a normal roadmap cycle and there is no other advantage | Fail. Excluded from the surface generator's competitor-claim archetypes (`vs_competitor`/`alternatives_to`) — unchanged behavior, see §4.3. |

DIST-P2 asks four questions, in order:

1. Could any listed substitute close this gap in one normal roadmap quarter? (Same as before.)
2. Does closing the gap genuinely break the substitute's own revenue model or force it to abandon a segment it serves? If yes → `structural`, stop — Q4 does not apply.
3. Is every claim in `wedge_evidence` sourced? (Same as before, applies regardless of tier.)
4. **New.** Only asked when Q2 fails: is there sourced evidence the substitute has had the opportunity to close this gap and chosen not to? Concrete evidence only — shipped-feature history, a public roadmap, stated positioning, years in market without addressing it. **Absence of evidence is not evidence** — an unsourced or unresearched Q4 answer resolves to `temporary`, never `execution`. A substitute actively shipping comparable features on a normal cadence (e.g. Jobber's ~6-week release cycles) is `temporary` even without the exact feature today, because they're actively closing the gap.

`wedge_evidence.q4_evidence` must be a non-empty array with at least one real `source_url` whenever `corrected_wedge_type` is `execution` — DIST-P2 rejects (raises, writes nothing) an `execution` verdict with empty `q4_evidence` rather than accept a bare LLM assertion of "they haven't shipped it."

**Migration data policy:** rows that were `temporary` under the old 2-tier rule are **not** auto-promoted to `execution` — they're flagged `wedge_review_status = 'needs_rereview'` so DIST-P2 re-evaluates them under the new Q4 rule from real evidence, rather than inheriting a verdict rendered under different criteria.

## 0.4a — Q0 and the fourth tier: `invalid` (added 2026-08-31, migration 039)

Small-portfolio-hub v4 passed Q1–Q4 correctly — it genuinely was `structural` on its own terms — while resting on a wedge ("tenants pay $0 with us") that Innago and TurboTenant had *already shipped* as a configurable landlord-side setting. That's not `temporary` (something a substitute could ship on a normal roadmap cycle); it was already true, today, before the brief was even written. Q1–Q4 have no question that catches this, because they all ask about the *future* (could/would a substitute close the gap) — none of them ask whether one already has.

**Q0, asked before Q1:** *Does any substitute already offer this, including as a configurable option, a settings toggle, or a specific tier — right now, today?* DIST-P2 checks each substitute's own `configurable_options` field first (§0.3), then verifies independently rather than trust it blindly. If Q0 finds a real, sourced yes, `wedge_type = 'invalid'` — a fourth tier, distinct from and more severe than `temporary` — and Q1–Q4 do not run; DIST-P2 must name the substitute and cite the source in `wedge_evidence.q0`.

| Tier | Meaning | Gate outcome |
|---|---|---|
| `invalid` | A substitute already offers this, today, as a real option/setting/tier | Fail, harder than `temporary`. `approve_positioning()` blocks it outright — **no override path**, unlike the margin gate below. Excluded from competitor-claim archetypes exactly like `temporary` (§4.3), and can never reach `status='approved'` in the first place regardless. |

## 0.4b — Viability gate on approval (added 2026-08-31, migration 039)

Q0–Q4 test *defensibility* (is the wedge real). None of them test *viability* (does the unit economics survive at the tier's own ceiling) — which is exactly how v4 was approvable while stating, in its own `price_rationale`, that Core goes margin-negative above ~20 units, negative-51.5% at its own 30-unit ceiling. Defensibility and viability are separate tests; only the first one ran.

`gross_margin_at_ceiling` (numeric, nullable) and `margin_floor` (numeric, default `0.70`) are set on the row before approval — margin computed at the **top of the tier's unit range**, not the average; v4 looked fine on average and was negative at its own ceiling. `approve_positioning(p_id, p_margin_override_reason default null)` raises if `gross_margin_at_ceiling` is null or below `margin_floor`, unless a non-empty `p_margin_override_reason` is passed, which is then recorded on the row as `margin_override_reason` — the owner can still approve a low-margin brief deliberately, but the reason is on the record, not silent. This is the one place in Phase 0 with a real override path; `invalid` (§0.4a) has none, because a wedge a substitute already offers isn't a risk to weigh, it's a fact already true.

## 0.5 — Agents

**DIST-P1 — Positioning Researcher.** Enumerates the substitute set, hard requirement to search explicitly for free/open-source options and the no-software-at-all path, and (§0.3, 2026-08-31) hard requirement to research each `free_tool`/`paid_tool` entry's `configurable_options` — pricing pages, FAQs, help docs, settings documentation, not just the advertised default.

**DIST-P2 — Wedge Validator.** Adversarial, real web-search backed. Runs Q0 first (§0.4a) — does a substitute already offer this as a real option, today — before Q1–Q4. Downgrades unsupported `structural` claims, rejects unsourced evidence, checks price against the *cheapest* substitute, and (§0.4) determines `execution` vs `temporary` via sourced Q4 evidence. **May never approve.** Owner approval only.

## 0.6 — Acceptance (all confirmed 2026-08-30/31)

- [x] Migration applied to `microsaas-prod`
- [x] `substitute_set` validator rejects any array without a `do_nothing` entry
- [x] Partial unique index blocks a second approved brief per product
- [x] DIST-P1 run against SPH surfaces Innago, TurboTenant, Avail, Baselane, and spreadsheet-plus-Zelle
- [x] DIST-P2 correctly downgrades a seeded `temporary` wedge
- [x] Approval path is admin-only; agent write to `status='approved'` raises

**Wedge taxonomy amendment (2026-08-31, migration 038) — confirmed:**

- [x] `wedge_type` CHECK widened to `('structural','execution','temporary')`; existing `temporary` rows flagged `wedge_review_status='needs_rereview'`, none auto-promoted — verified live against `microsaas-prod`
- [x] DIST-P2 Q4 logic tested against all three calibration fixtures (Jobber shipping cadence → `temporary`; long-standing free competitor with no roadmap signal → `execution`; SPH ACH absorption → `structural`)
- [x] `execution` verdict with empty `q4_evidence` is rejected by DIST-P2 (raises, writes nothing) — real test
- [x] `temporary` still excluded from `vs_competitor`/`alternatives_to` generation — regression test re-run, unweakened

**Gate hardening (2026-08-31, migration 039) — confirmed:**

- [x] `wedge_type` CHECK widened again to `('structural','execution','invalid','temporary')`
- [x] `configurable_options` required on every `free_tool`/`paid_tool` substitute_set entry; empty array without `researched: true` rejected by DIST-P1 (raises, writes nothing) — real test, plus a real test confirming `do_nothing`/`manual_process`/`in_house_build`/`agency_service` kinds are exempt (no vendor settings page to check)
- [x] Q0 fixture (a substitute already offering the wedge as a setting) resolves to `invalid`, not `temporary` — real test; a mismatched model output (`q0.already_offered=true` with `corrected_wedge_type` not `invalid`, or `already_offered=true` missing `substitute`/`source_url`) is rejected outright — real test; `q0.already_offered=false` and a missing `q0` key (backward compatibility) both leave the existing three-tier behavior untouched — real test
- [x] `gross_margin_at_ceiling`/`margin_floor`/`margin_override_reason` added; `approve_positioning()` raises on null-or-below-floor margin with no override, succeeds with a recorded override reason, succeeds outright above the floor, and blocks `invalid` unconditionally (no override path) — four real fixture tests run live against `microsaas-prod` via simulated-JWT SQL, not mocks
- [x] Regression: `invalid` produces zero `vs_competitor`/`alternatives_to` archetypes in `surface_planner.py`, identically to `temporary`; `structural` still produces both — real tests (`tests/test_dist_surface_planner_wedge_gate.py`)
- [x] Real bug caught and fixed during this migration's own live testing: `create or replace function approve_positioning(p_id uuid, p_margin_override_reason text default null)` does not replace a function whose *signature* changed — it adds a second overload, making any 1-argument call ambiguous. Fixed with an explicit `drop function if exists approve_positioning(uuid)` before the `create or replace`, both in the live migration and the committed `.sql` file.
- [x] Five pending briefs (`decodedsix`, `tradesdesk`, `tradesdesk-hvac`, `tradesdesk-plumbing`, `tradesdesk-electrical`) re-run through Q0 live against `microsaas-prod`. One dead: `tradesdesk-plumbing` → `invalid`, sourced directly from BSI Online's own site (already in that brief's own substitute_set) already generating and auto-submitting jurisdiction-specific backflow reports — the brief's "no *generic FSM* does this" framing was true but irrelevant, since BSI Online was never generic FSM. Four remain `temporary`/`pending_review` but flagged `wedge_review_status='needs_rereview'` as genuine research gaps, not confirmed dead: `tradesdesk-electrical` (PermitFlow already does the core capability at scale, but its self-serve pricing/fit for a solo electrician is unconfirmed — flagged as the single highest-risk open item), `tradesdesk-hvac` (two named substitutes, RefriTrak/RefriComply, could not be verified as real products — a data-quality issue in the original P1 brief, separate from Q0 itself), `tradesdesk` (Service Fusion is a real flat-rate/unlimited-user substitute missing from the original substitute_set — doesn't clearly invalidate the wedge for a true solo-operator ICP given its higher price point, but makes "every paid substitute" a false absolute), `decodedsix` (no source found confirming any one free tool combines a map location layer with a payout-sorted daily-reset queue — inconclusive, not confirmed either way). TradesDesk's separately-completed MotionOps analysis (`docs/motionops-competitive-analysis.md`) folded in as a citation on the base `tradesdesk` brief, not re-run.
- [x] small-portfolio-hub v5's `gross_margin_at_ceiling` backfilled to `1.0` (its real, tested figure per `docs/unit-economics.md`'s restored fee model) once v5 existed; v4 (approved before this migration) left untouched, not retroactively gated
- [x] `published_surfaces` confirmed `0`, `approved_positioning` confirmed `1` (unchanged — small-portfolio-hub v4, approved before this pass) — nothing newly approved by this work
- [x] Six pending briefs (SPH v2, DecodedSix v2, `tradesdesk`, `tradesdesk-hvac`, `tradesdesk-plumbing`, `tradesdesk-electrical`) re-evaluated under the amended rule as new versions at `pending_review`; zero approved

---

# Phase 1 — Attribution + Funnel Instrumentation

## 1.1 — Schema

```sql
create table mse_attribution_touches (
  id            uuid primary key default gen_random_uuid(),
  product_id    uuid not null references mse_products(id),
  anon_id       text not null,
  tenant_id     uuid,
  touch_index   int  not null,
  channel       text not null,
  surface_slug  text,
  utm           jsonb,
  referrer      text,
  occurred_at   timestamptz not null default now()
);

create table mse_funnel_events (
  id          uuid primary key default gen_random_uuid(),
  product_id  uuid not null references mse_products(id),
  tenant_id   uuid not null,
  step        text not null check (step in
                ('signup','email_verified','activated','trial_started','paid','churned')),
  occurred_at timestamptz not null default now(),
  metadata    jsonb,
  unique (tenant_id, step)
);
```

RLS on this and every DIST table checks `(auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'` for owner-facing reads — never `'owner'`.

## 1.4 — Acceptance (confirmed 2026-08-30)

- [x] Migrations applied to `microsaas-prod`
- [x] Real cookie → signup → `tenant_id` backfill flow, live-tested end to end against a real Supabase Auth user (cleaned up after)
- [x] CEO Decoded panel renders per-product funnel with drop-off
- [x] SPH wired; TradesDesk N/A (no frontend exists yet)

---

# Phase 2 — Indexation Monitor

DecodedSix already demonstrated the failure this prevents: 44 pages crawled-not-indexed.

## 2.1 — Schema

```sql
create table mse_indexation (
  id            uuid primary key default gen_random_uuid(),
  product_id    uuid not null references mse_products(id),
  url           text not null,
  surface_slug  text,
  indexed       boolean,
  coverage_state text,
  impressions   int default 0,
  clicks        int default 0,
  avg_position  numeric,
  first_seen_at timestamptz not null default now(),
  checked_at    timestamptz not null default now(),
  unique (product_id, url)
);
```

**Alert rule:** any surface `first_seen_at` older than 21 days and `indexed = false` raises an `mse_monitoring_events` row. Three or more in one product pauses that product's generator (`mse_generator_state`) until reviewed.

## 2.3 — Acceptance

- [x] Sync handles a property with zero verified access without crashing (real dependency-injected fake, GSC OAuth credentials don't exist — disclosed, not faked)
- [x] 21-day rule fires against seeded data
- [x] Generator pause on 3+ failures
- [ ] DecodedSix backfill — no real GSC data exists anywhere to backfill from; not fabricated

---

# Phase 3 — Competitor Registry + Freshness Loop

## 3.1 — Schema

```sql
create table mse_competitors (
  id                uuid primary key default gen_random_uuid(),
  product_id        uuid not null references mse_products(id) on delete cascade,
  name              text not null,
  kind              text not null,
  pricing_url       text,
  pricing_snapshot  jsonb,
  positioning       text,
  known_weaknesses  jsonb,
  last_verified_at  timestamptz not null default now(),
  last_changed_at   timestamptz,
  unique (product_id, name)
);
```

Seeded from the real (not necessarily approved) `mse_positioning.substitute_set` — 24 real rows across all 4 products, confirmed live.

## 3.2 — DIST-C1 Competitor Monitor

Monthly. Real fetch, robots.txt-respecting, rate-limited. Change → new snapshot, `mse_monitoring_events` row, cascades `mse_content_surfaces.stale = true` for every surface citing that competitor.

**Known limit**: JS-client-rendered pricing pages (confirmed with innago.com/pricing) aren't visible to a plain HTTP fetch — a future run against such a site needs headless-browser fetch, not built yet.

## 3.3 — Acceptance

- [x] Registry seeded from real substitute_set data
- [x] Price-change diff detected against a real live fetch
- [x] Cascade to `mse_content_surfaces.stale` (tested against the real table once Phase 4 landed)
- [x] Respects robots.txt, rate-limited
- [x] Monthly n8n workflow committed as `dist_competitor_monitor.json`

---

# Phase 4 — Surface Generator

## 4.1 — Schema

```sql
create table mse_content_surfaces (
  id            uuid primary key default gen_random_uuid(),
  product_id    uuid not null references mse_products(id) on delete cascade,
  archetype     text not null check (archetype in
                  ('vs_competitor','alternatives_to','jurisdiction',
                   'jtbd','calculator','faq_block')),
  slug          text not null,
  title         text not null,
  body_mdx      text,
  data_payload  jsonb,
  competitor_id uuid references mse_competitors(id),
  hitl_tier     int not null,
  status        text not null default 'draft'
                  check (status in ('draft','pending_review','approved','published','stale','archived')),
  quality_score jsonb,
  published_at  timestamptz,
  created_at    timestamptz not null default now(),
  unique (product_id, slug)
);
```

## 4.2 — Archetypes

| Archetype | Source | Volume | HITL tier |
|---|---|---|---|
| `vs_competitor` | `mse_competitors` | 1 per competitor | 3 |
| `alternatives_to` | `mse_competitors` | 1 per competitor | 3 |
| `jurisdiction` | jurisdiction dataset | 50 states / N cities | 3 |
| `jtbd` | `mse_positioning.trigger_event` | 5–10 per product | 2 |
| `calculator` | `mse_positioning.price_rationale` | 1–3 per product | 2 |
| `faq_block` | AEO question set | embedded on all pages | 1 |

## 4.3 — Agents

**DIST-S1 Surface Planner** — plans `vs_competitor`/`alternatives_to` for `wedge_type` in `('structural','execution')`, refuses them for `'temporary'` (§0.4), reads `mse_generator_state`'s pause flag first.
**DIST-S2 Surface Writer** — every competitor claim sourced from `mse_competitors`.
**DIST-S3 Quality Gate** — 5 real checks: original-value, substance floor, duplicate detection (pgvector, `search_path='public'`), claim audit (source + 90-day freshness), schema validity. 3rd rejection of the same archetype pauses it + raises HITL.

Publishing is admin-only (`publish_surface()`, mirrors `approve_positioning()`) with one narrow exception: a service-role-callable `auto_publish_tier1_surface()` function, hardcoded to `hitl_tier=1` rows only, for Tier 1 auto-publish (Phase 5).

## 4.5 — Acceptance

- [x] Generator refuses to run for a product with no approved positioning — live-verified against all 4 real products, all correctly refused (nothing is approved)
- [x] Duplicate detection catches a near-copy (test); real embedding calls credential-blocked (`GEMINI_API_KEY` unset)
- [x] Stale-competitor surfaces excluded from sitemap
- [ ] "Full run for SPH produces 5+5+50 drafts" — cannot be satisfied with real data; nothing is approved. Demonstrated against a test fixture only.
- [x] **Zero published pages until approval — verified against live DB**: `mse_content_surfaces` had 0 total rows as of the last real check.

---

# Phase 5 — HITL Tiering + n8n Wiring

## 5.1 — Tiers

- **Tier 1 — auto-publish.** `faq_block`, calculator UI copy.
- **Tier 2 — batch review.** `jtbd`, `calculator`.
- **Tier 3 — owner-only, individual.** `vs_competitor`, `alternatives_to`, `jurisdiction`. Never batched, never auto-published, never agent-approved.

## 5.2 — Workflows

| File | Trigger | Status |
|---|---|---|
| `dist_surface_plan.json` | monthly, 1st, 6am MST | committed, not activated (no n8n instance access) |
| `dist_surface_generate.json` | daily 7am MST | committed, not activated |
| `dist_competitor_monitor.json` | monthly, 15th | committed, not activated |
| `dist_indexation_sync.json` | weekly, Mon 6am MST | committed, not activated |

## 5.3 — Acceptance

- [x] Tier routing correct per archetype
- [x] Tier 3 cannot be approved by any agent identity
- [x] Batch review UI in CEO Decoded supports bulk approve/reject on Tier 2 (`SurfaceReviewPanel.tsx`)
- [x] All four workflows committed; **activation is a manual follow-up**, no n8n instance access from a Claude Code session
- [x] Generator pause honored when indexation circuit breaker is open

---

# Phase 6 — Backfill Existing Products

| Order | Product | Real finding (2026-08-30/31) |
|---|---|---|
| 1 | Small Portfolio Hub | v1 rejected (Buildium-only). v2 `pending_review` — real free-tool substitute set, structural wedge (tenant-pays-nothing + support), requires ACH absorption + reprice decision. **Owner approval still pending.** |
| 2 | TradesDesk FSM | Not launched. Generic-FSM brief downgraded `temporary` (Orcatec ships free integrated FSM) — see Task 4 follow-up for vertical-narrowed candidates. |
| 3 | DecodedSix map | Original map-focused brief downgraded `temporary` (free map competitors exist) — see Task 3 follow-up for the daily-economy-companion reframing. |
| 4 | thdagentic consulting | 6 unsourced claims flagged by P2. Brief correctly fails `wedge_evidence` until a real case study exists — the system working as designed. |

## 6.1 — Acceptance

- [x] Four briefs at a real, honest status with reasons recorded
- [x] Rejected/downgraded briefs have a recorded remediation path
- [ ] "SPH regenerated surfaces argue against free tools" — no surfaces exist yet, nothing approved. Correctly blocked, not a gap.
- [x] Attribution live on SPH before any generated page could publish (trivially true — nothing has published)

---

# Phase 7 — Support Drafting Layer

## 7.0 — Constraint

SPH's positioning wedge is "tenants pay nothing and a human answers." A bot replying directly to the customer **is the free-tool experience with a price tag** — fails the structural test in that product's own brief.

> **Invariant: draft, don't deflect.** Agents draft. A human approves. The reply goes out under a human name. Tier 1 auto-answer is the single exception, gated in code (7.5).

## 7.2 — Schema

`mse_support_tickets`, `mse_support_drafts` (owner-read only, tenant-unreachable — real RLS-tested), `mse_support_kb` (pgvector, `google-genai==0.3.0` pinned for a real dependency conflict, `search_path='public'` required for the `<=>` operator to resolve inside `SECURITY DEFINER`).

## 7.5 — Tier 1 auto-answer gate

```sql
alter table mse_products
  add column support_autoanswer_enabled boolean not null default false,
  add column support_autoanswer_threshold numeric not null default 0.92,
  add column paying_customer_count int not null default 0;
```

Requires ≥100 paying customers, ≥200 resolved tickets, ≥30 days at ≥95% approve-without-edit, explicit admin action. **No agent identity may set this flag — real, live-tested with a fresh admin JWT.**

## 7.8 — Acceptance

- [x] `mse_support_drafts` unreachable under a tenant JWT — real RLS test
- [x] SUP-D1 forces Tier 3 on refund/cancel/chargeback/legal keywords
- [x] SUP-D2 returns an explicit "not in KB" draft rather than fabricating policy
- [x] No agent identity can set `support_autoanswer_enabled`
- [x] Draft → approve → send round trip verified against live production — real ticket, real delivery, confirmed
- [x] SPH SLA copy updated everywhere — required two follow-up passes after the first was reported done but incomplete (see SESSION notes); final state confirmed via live HTML grep, not status codes

---

## Phase 8 — Unified HITL Queue + CRM Surfaces

**Gated — see the standing session instructions for the exact split (additive work vs. the `mse_leads` ALTER, which requires a branch-and-review or an explicit human-applied step, never an unattended direct alter against `microsaas-prod`).**

**No external CRM.** Every record DIST and Phase 7 produce already lives in `microsaas-prod`; Decoded Empire OS is the CRM, backed by `mse_leads` + `mse_activities`.

### App split

| App | Auth | Contents |
|---|---|---|
| **CEO Decoded** | owner (`admin`) only | Agent dispatch, system health, indexation, monitoring events, MRR intelligence |
| **Decoded Empire OS** | Kelvin, wife, son (magic link) | Unified HITL queue, lead pipeline, support inbox, surface approvals, positioning briefs |

### Unified HITL queue

```sql
create table mse_hitl_items (
  id            uuid primary key default gen_random_uuid(),
  product_id    uuid references mse_products(id),
  source_table  text not null,
  source_id     uuid not null,
  tier          int  not null check (tier between 1 and 3),
  summary       text not null,
  assigned_to   text,
  status        text not null default 'pending'
                  check (status in ('pending','approved','edited','rejected','expired')),
  decided_by    text,
  decided_at    timestamptz,
  due_at        timestamptz,
  created_at    timestamptz not null default now(),
  unique (source_table, source_id)
);
```

**Sync is service-layer, inside one transaction — not triggers.** Producers insert on creation; the decision endpoint writes both the queue row and the domain row in one transaction, rolling back both on partial failure.

### CRM columns on `mse_leads` — THE GATED ALTER

```sql
alter table mse_leads
  add column owner            text,
  add column stage            text not null default 'new'
    check (stage in ('new','contacted','replied','qualified','demo','won','lost')),
  add column next_action      text,
  add column next_action_due  date,
  add column last_activity_at timestamptz;
```

`mse_leads` is written to by the Sunday lead-finder run — this specific statement never runs directly against `microsaas-prod` inside an unattended session.

### Activity log — append-only, no exceptions

```sql
create table mse_activities (
  id           uuid primary key default gen_random_uuid(),
  product_id   uuid references mse_products(id),
  subject_type text not null check (subject_type in ('lead','tenant')),
  subject_id   uuid not null,
  kind         text not null,
  body         text,
  actor        text not null,
  occurred_at  timestamptz not null default now()
);
```

No update or delete path may be exposed, at any layer, to any role.

### Live per-product MRR

```sql
create table mse_product_mrr (
  id             uuid primary key default gen_random_uuid(),
  product_id     uuid not null references mse_products(id),
  snapshot_date  date not null,
  mrr_cents      bigint not null,
  active_subs    int not null,
  new_mrr_cents  bigint default 0,
  churn_mrr_cents bigint default 0,
  unique (product_id, snapshot_date)
);
```

Daily Stripe pull — inert without `STRIPE_SECRET_KEY`, built and fixture-tested regardless.

### Panels

| Panel | App | Reads | Write? |
|---|---|---|---|
| Unified HITL queue | OS | `mse_hitl_items` | yes |
| Lead pipeline | OS | `mse_leads` + `mse_activities` | yes |
| Support inbox | OS | `mse_support_tickets` + `_drafts` | yes |
| Positioning briefs | OS | `mse_positioning` | yes — admin only |
| Surface queue | OS | `mse_content_surfaces` | yes |
| Competitor registry | OS | `mse_competitors` | yes |
| Funnel + attribution | CEO Decoded | `mse_attribution_summary` | no |
| Indexation health | CEO Decoded | `mse_indexation` | no |
| MRR vs. target ladder | CEO Decoded | `mse_product_mrr` | no |

Positioning approval is admin-only in the OS regardless of who is logged in — enforced by role check, not UI hiding. The wife's magic-link login can reach lead pipeline, support inbox, surface queue; **cannot** reach positioning approval or agent dispatch — tested against real RLS with a real JWT, not UI state.

---

## Explicitly out of scope

- **More outbound.** The ceiling is Google and SMTP rate limits, not tooling.
- **External CRM.** Decided in Phase 8.
- **Paid acquisition.** Already deferred until 10 paying customers and real testimonials.
- **Voice agents in the generation path.** Apollo and NOVA aren't crisp enough yet and this path has legal exposure.

---

## The limit of this system

DIST makes positioning *checkable* and generation *repeatable*. It does not make the positioning *right*. DIST-P2 can prove a wedge is temporary; it cannot invent a real one where none exists. Phase 0 is a gate, not an oracle — its job is to make sure a bad call fails loudly at brief-approval time instead of quietly, eighteen months later, in the conversion rate.

That judgment stays human. Keep it there.
