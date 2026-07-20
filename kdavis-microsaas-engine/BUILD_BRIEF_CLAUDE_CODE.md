# BUILD_BRIEF_CLAUDE_CODE.md
# Campaign Aware Replenishment

---

## Product Overview

**Product Name:** Campaign Aware Replenishment
**Tagline:** Reorder for what you're about to sell, not what you already sold.
**Price Point:** $49/month (single tier; see Tier Structure below)

### One-Paragraph Pitch

Campaign Aware Replenishment is a Shopify-native app that pulls live Meta Ads and Google Ads spend calendars alongside Shopify sales velocity to generate weekly, per-SKU reorder recommendations sized to *upcoming* campaign demand — not trailing averages. DTC brands running $300K–$3M ARR with 200–5,000 SKUs routinely blow out inventory mid-campaign or over-order post-campaign because their replenishment tool has no visibility into the ad calendar. At $49/month this product delivers the specific differentiator (ad-spend-aware reorder uplift) that Prediko at the same price lacks, and at less than half the cost of Inventory Planner Essentials ($119.99/mo) or its full-featured tier (~$245/mo), while requiring zero spreadsheet work from the merchant.

---

## Target Customer

| Attribute | Spec |
|---|---|
| Platform | Shopify (required; Shopify Plus acceptable) |
| Revenue band | $300K – $3M ARR |
| SKU count | 200 – 5,000 active SKUs |
| Ad channels | Running Meta Ads and/or Google Ads with scheduled campaign budgets |
| Current pain | Stockouts during campaigns, overstock after campaigns, manual spreadsheet reconciliation |
| Current tools | Shopify native inventory, possibly Prediko or Inventory Planner Essentials |
| Sophistication | Has a media buyer or agency; inventory managed by founder or ops hire |

---

## Conservative MRR Potential

**$5,174/month** (from research report)
*Note: The research report does not provide the subscriber count assumption, addressable market size, or conversion rate methodology behind this figure. Do not hard-code this number into any in-app metric. Use it only as the go/no-go floor benchmark ($4,000 MRR minimum required by THD non-negotiables).*

---

## Core Feature List (Build Order)

Build in strict sequence. Do not begin a phase until the prior phase passes its acceptance criteria.

### Phase 0 — Foundation & Auth
1. Shopify OAuth 2.0 install flow (embedded app, Shopify App Bridge 3.x)
2. Multi-tenant Postgres schema with `tenant_id` on every table + Row-Level Security policies enforced at DB layer (see CLAUDE.md Non-Negotiables)
3. `POST /events` endpoint wired and accepting payloads before any agent action is built
4. Billing: Shopify Recurring Application Charge API, $49/month, 14-day free trial, charge activates on day 15
5. CLAUDE.md non-negotiables checklist gating CI — build fails if any check is missing

### Phase 1 — Data Ingestion Connectors
6. **Shopify Sales Velocity Ingestion:** Pull `orders` and `inventory_levels` via Shopify REST/GraphQL Admin API; compute trailing 30/60/90-day velocity per variant/SKU; store in `sku_velocity` table
7. **Meta Ads Calendar Connector:** OAuth to Meta Marketing API; ingest `campaigns`, `ad_sets` with `start_time`, `end_time`, `daily_budget`, `lifetime_budget`; store in `ad_campaigns` table with `platform = 'meta'`
8. **Google Ads Calendar Connector:** OAuth to Google Ads API (v14+); ingest campaigns with date ranges and budgets; store in `ad_campaigns` table with `platform = 'google'`
9. **SKU ↔ Campaign Attribution:** UI for merchant to map ad campaigns → product collections or specific SKU lists; store in `campaign_sku_map` junction table
10. Webhook subscriptions: `orders/create`, `inventory_levels/update`, `products/update` — emit `POST /events` on each receipt

### Phase 2 — Forecasting Engine
11. **Baseline Velocity Model:** Weighted moving average (weight: 60-day > 30-day > 7-day) per SKU; store snapshot in `forecast_baseline`
12. **Campaign Uplift Multiplier:** For each future campaign window with attributed SKUs, compute uplift factor from: (campaign daily budget) × (configurable spend-to-units coefficient, default 1.0, merchant-editable); store in `forecast_uplift`
13. **Composite Forecast:** Merge baseline + uplift into `forecast_weekly` — one row per SKU per ISO week, covering 8 weeks forward
14. **Reorder Quantity Calculator:** `reorder_qty = max(0, forecast_weekly_units × lead_time_weeks + safety_stock_days/7 × avg_daily_velocity − current_inventory)`; lead time and safety stock are merchant-configurable per supplier
15. Emit `POST /events` with `event_type: 'forecast_generated'` after each weekly forecast run

### Phase 3 — Recommendations UI
16. **Weekly Reorder Dashboard:** Embedded Shopify app page; table of SKUs with columns: SKU, current stock, forecasted demand (8-week), next reorder date, recommended reorder qty, linked campaign(s), confidence indicator
17. **Campaign Calendar View:** Timeline visualization of upcoming ad campaigns with overlaid inventory runway per attributed SKU collection; highlight SKUs with <14-day runway during a campaign window
18. **Reorder Export:** One-click CSV export of current week's reorder list (SKU, qty, supplier); future: PO draft push
19. **Alert Banner:** In-app alert when any SKU's projected runway drops below merchant-set threshold (default: 7 days) during a funded campaign window

### Phase 4 — Retention Loops (see dedicated section below)
20. Weekly email digest (reorder summary + campaign countdown)
21. Slack webhook alert (stockout risk during live campaign)
22. Spend-to-stock health score widget (embeddable)
23. Historical accuracy report (forecast vs. actual)
24. Supplier lead time learning (auto-adjust from PO close dates)
25. Benchmark nudge (anonymous peer comparison)

### Phase 5 — Reliability & Scale
26. Background job queue (Sidekiq or equivalent) for all API ingestion and forecast runs
27. Ingestion rate-limit handling for Meta and Google Ads APIs (exponential backoff, dead-letter queue)
28. Shopify API rate-limit middleware (leaky bucket)
29. Per-tenant ingestion schedule (default: nightly at 2am merchant local time, configurable)
30. Audit log table (`events_log`) — append-only, `tenant_id`-scoped, stores all `POST /events` payloads

---

## The 6 Required Retention Loops

*Retention loops must be built as discrete, testable modules — not bolted onto the UI.*

### Loop 1 — Weekly Reorder Digest Email
**Trigger:** Every Monday 7am merchant local time (or configurable day)
**Content:** Top 10 SKUs to reorder this week, qty, days of stock remaining, upcoming campaigns that drive the recommendation
**Why it works:** Pulls merchant back into the app on a cadence tied to their buying workflow; the email is only accurate if the app stays connected to ad accounts
**Implementation:** Sendgrid (or Resend); unsubscribe must honor; emit `POST /events { event_type: 'digest_sent', tenant_id }`

### Loop 2 — Live Campaign Stockout Alert (Slack + Email)
**Trigger:** Real-time (or next ingestion cycle) — fires when a campaign transitions to `ACTIVE` status AND any attributed SKU has projected runway < threshold
**Content:** "Your [Campaign Name] goes live in X days. [SKU] has [N] units — estimated sellout in [M] days at projected demand."
**Why it works:** High urgency, directly tied to money being spent on ads; creates Pavlovian association between ad launch and opening the app
**Implementation:** Slack incoming webhook (merchant provides URL); fallback to email; emit `POST /events { event_type: 'stockout_alert_sent' }`

### Loop 3 — Spend-to-Stock Health Score (Dashboard Widget)
**Trigger:** Computed nightly; displayed on app home screen
**Content:** A single 0–100 score representing the ratio of funded campaign demand to covered inventory across all active SKU-campaign pairs; color-coded red/yellow/green
**Why it works:** Gives merchant a single number to check daily; shareable with ops/media buyer; score dropping creates urgency
**Implementation:** Computed in `tenant_health_score` table; embed in app home; emit `POST /events { event_type: 'health_score_computed' }` nightly

### Loop 4 — Forecast vs. Actual Accuracy Report (Monthly)
**Trigger:** First day of each month, covering prior month
**Content:** Per-SKU table of forecasted units vs. actual units sold, MAPE (mean absolute percentage error), campaigns that ran, and whether stock was sufficient
**Why it works:** Demonstrates value concretely; merchants who see accuracy report are reminded the app was "right" — increases trust and reduces churn at renewal
**Implementation:** Computed from `forecast_weekly` vs. `orders` actuals; delivered via email + in-app report page; emit `POST /events { event_type: 'accuracy_report_generated' }`

### Loop 5 — Supplier Lead Time Learning Nudge
**Trigger:** When merchant marks a reorder as "received" (manual input or future PO integration), system compares expected lead time vs. actual days elapsed
**Content:** "Your supplier [Name] delivered in 12 days vs. your 7-day setting. Updating your lead time improves forecast accuracy. [Update Now]"
**Why it works:** Prompts active configuration engagement; each update improves forecast quality, which increases the merchant's perceived value; creates data flywheel
**Implementation:** `supplier_lead_time_log` table; nudge shown in-app and via email; emit `POST /events { event_type: 'lead_time_nudge_shown' }`

### Loop 6 — Anonymous Peer Benchmark Nudge
**Trigger:** Monthly, after accuracy report
**Content:** "Merchants in your category (apparel/beauty/etc.) reorder an average of [N] days before campaign launch. You're reordering [M] days before. [See Recommendations]"
**Why it works:** Social proof and competitive anxiety; drives merchants to engage with recommendations tab to close the gap; benchmarks are computed only from opted-in tenant data
**Implementation:** Aggregate query across consenting tenants (opt-in checkbox during onboarding); never expose individual tenant data; emit `POST /events { event_type: 'benchmark_nudge_shown' }`
*Note: Research report does not provide actual benchmark figures — do not hard-code any percentages or day counts; compute from real tenant data at runtime.*

---

## Data Model Sketch

```sql
-- All tables require tenant_id (UUID, NOT NULL) and RLS policy.
-- RLS: CREATE POLICY on each table WHERE tenant_id = current_setting('app.current_tenant')::uuid

tenants (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  shopify_shop_domain TEXT NOT NULL UNIQUE,
  shopify_access_token TEXT NOT NULL,          -- encrypted at rest
  subscription_status TEXT NOT NULL DEFAULT 'trial', -- trial | active | cancelled
  trial_ends_at       TIMESTAMPTZ,
  created_at          TIMESTAMPTZ DEFAULT NOW()
)

-- NO tenant_id on tenants table itself (it IS the tenant anchor)

sku_velocity (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  shopify_variant_id BIGINT NOT NULL,
  sku             TEXT NOT NULL,
  product_title   TEXT,
  velocity_7d     NUMERIC(10,4),   -- units/day
  velocity_30d    NUMERIC(10,4),
  velocity_60d    NUMERIC(10,4),
  current_stock   INT,
  updated_at      TIMESTAMPTZ DEFAULT NOW()
)
-- RLS: ENABLE ROW LEVEL SECURITY; CREATE POLICY tenant_isolation ON sku_velocity USING (tenant_id = ...)

ad_campaigns (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  platform        TEXT NOT NULL CHECK (platform IN ('meta', 'google')),
  external_id     TEXT NOT NULL,              -- platform's campaign ID
  campaign_name   TEXT NOT NULL,
  status          TEXT,                        -- ACTIVE | PAUSED | SCHEDULED
  start_date      DATE,
  end_date        DATE,
  daily_budget_usd NUMERIC(12,2),
  lifetime_budget_usd NUMERIC(12,2),
  synced_at       TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(tenant_id, platform, external_id)
)

campaign_sku_map (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  ad_campaign_id  UUID NOT NULL REFERENCES ad_campaigns(id) ON DELETE CASCADE,
  shopify_variant_id BIGINT NOT NULL,
  sku             TEXT NOT NULL,
  uplift_coefficient NUMERIC(6,4) DEFAULT 1.0  -- merchant-editable
)

forecast_baseline (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  shopify_variant_id BIGINT NOT NULL,
  sku             TEXT NOT NULL,
  weighted_velocity_daily NUMERIC(10,4),      -- composite of 7/30/60d
  computed_at     TIMESTAMPTZ DEFAULT NOW()
)

forecast_weekly (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  shopify_variant_id BIGINT NOT NULL,
  sku             TEXT NOT NULL,
  iso_week        INT NOT NULL,                -- YYYYWW format
  iso_year        INT NOT NULL,
  forecasted_units NUMERIC(10,2),
  has_campaign_uplift BOOLEAN DEFAULT FALSE,
  reorder_qty_recommended INT,
  confidence      TEXT CHECK (confidence IN ('high','medium','low')),
  generated_at    TIMESTAMPTZ DEFAULT NOW()
)

suppliers (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  supplier_name   TEXT NOT NULL,
  lead_time_days  INT NOT NULL DEFAULT 7,
  safety_stock_days INT NOT NULL DEFAULT 3
)

supplier_sku_map (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  supplier_id     UUID NOT NULL REFERENCES suppliers(id),
  shopify_variant_id BIGINT NOT NULL,
  sku             TEXT NOT NULL
)

supplier_lead_time_log (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  supplier_id     UUID NOT NULL REFERENCES suppliers(id),
  expected_days   INT,
  actual_days     INT,
  recorded_at     TIMESTAMPTZ DEFAULT NOW()
)

tenant_health_score (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  score           INT CHECK (score BETWEEN 0 AND 100),
  computed_at     TIMESTAMPTZ DEFAULT NOW()
)

-- Append-only event log — stores every POST /events payload
events_log (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,              -- not FK — must survive tenant delete for audit
  event_type      TEXT NOT NULL,
  payload         JSONB,
  emitted_at      TIMESTAMPTZ DEFAULT NOW()
)
-- RLS on events_log: tenant_id isolation; service role bypasses for internal writes
```

---

## Tier Structure

*The research report does not provide a validated multi-tier structure or per-tier pricing rationale beyond the $49/month anchor.*

**Implemented for launch: Single tier at $49/month.**

Placeholder for future tiers (do not build at launch; stubs only in billing code):
- `starter` — $49/month (current)
- `growth` — price TBD; unlock: PO push integrations,