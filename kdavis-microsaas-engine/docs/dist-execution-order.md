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
  wedge_type          text not null check (wedge_type in ('structural','temporary')),
  wedge_evidence      jsonb not null,
  price_rationale     text not null,
  kill_criteria       text not null,
  moat_risk           boolean not null default false,

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

Approval is enforced by a `SECURITY DEFINER` function (`approve_positioning`) checking `role = 'admin'`, plus a trigger rejecting any direct `UPDATE ... status='approved'` that didn't go through it. See `supabase/migrations/20260830000029_dist_phase0_positioning.sql` for the real, live implementation.

## 0.3 — `substitute_set` contract

Array, minimum three entries, **must** include one with `kind = 'do_nothing'`. `kind` ∈ `free_tool | paid_tool | do_nothing | manual_process | in_house_build | agency_service`.

## 0.4 — The structural test

`wedge_type = 'structural'` is only valid if the substitute **cannot** close the gap without breaking its own revenue model or org incentives. If they could close it in a normal roadmap quarter, it's `temporary` and it is not a wedge — it's a head start. A `temporary` brief may be approved, but flags `moat_risk` and is excluded from the surface generator's competitor-claim archetypes.

## 0.5 — Agents

**DIST-P1 — Positioning Researcher.** Enumerates the substitute set, hard requirement to search explicitly for free/open-source options and the no-software-at-all path.

**DIST-P2 — Wedge Validator.** Adversarial. Downgrades unsupported `structural` claims, rejects unsourced evidence, checks price against the *cheapest* substitute. **May never approve.** Owner approval only.

## 0.6 — Acceptance (all confirmed 2026-08-30/31)

- [x] Migration applied to `microsaas-prod`
- [x] `substitute_set` validator rejects any array without a `do_nothing` entry
- [x] Partial unique index blocks a second approved brief per product
- [x] DIST-P1 run against SPH surfaces Innago, TurboTenant, Avail, Baselane, and spreadsheet-plus-Zelle
- [x] DIST-P2 correctly downgrades a seeded `temporary` wedge
- [x] Approval path is admin-only; agent write to `status='approved'` raises

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

**DIST-S1 Surface Planner** — refuses `vs_competitor`/`alternatives_to` when `wedge_type='temporary'`, reads `mse_generator_state`'s pause flag first.
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
