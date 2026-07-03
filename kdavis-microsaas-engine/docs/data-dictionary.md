# Data Dictionary — Micro SaaS Engine
Updated whenever a table is created or a column is added. Exit diligence requirement.

---

## tenants
The master record for each paying customer organization.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | Tenant identifier, used as RLS key throughout |
| name | TEXT | Business name |
| tier | TEXT | `starter` / `growth` / `scale` — maps to Stripe price IDs |
| stripe_customer_id | TEXT UNIQUE | Stripe customer object ID |
| stripe_subscription_id | TEXT | Active subscription ID |
| status | TEXT | `active` / `paused` / `churned` |
| created_at | TIMESTAMPTZ | Account creation date |
| updated_at | TIMESTAMPTZ | Last record modification |

---

## usage_events
The behavioral heartbeat. Every meaningful product interaction writes here. Powers milestones, digest, and re-engagement.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | Event identifier |
| tenant_id | UUID FK | References tenants.id, RLS key |
| event_type | TEXT | Namespaced event slug (e.g. `invoice_created`, `report_generated`) |
| metadata | JSONB | Freeform context for the event (amounts, entity IDs, etc.) |
| created_at | TIMESTAMPTZ | When the event occurred |

Index: `idx_usage_events_tenant_created` on `(tenant_id, created_at DESC)` — used by weekly digest query and milestone detector.

---

## milestones
Tracks customer progress toward outcome milestones. Creates psychological switching cost and drives upgrade conversations.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | Milestone record identifier |
| tenant_id | UUID FK | References tenants.id |
| milestone_key | TEXT | Slug identifying the milestone (e.g. `first_event`, `hundred_events`) |
| threshold | INTEGER | Number of events required to achieve |
| achieved_at | TIMESTAMPTZ | When threshold was crossed — NULL if not yet achieved |
| notified_at | TIMESTAMPTZ | When customer was notified — NULL if not yet sent |
| created_at | TIMESTAMPTZ | When the milestone record was created for this tenant |

Unique constraint: `(tenant_id, milestone_key)` — one record per milestone per tenant.

---

## retention_sequences
Tracks the state of automated re-engagement sequences per tenant. n8n reads this to determine next step.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | Sequence record identifier |
| tenant_id | UUID FK | References tenants.id |
| sequence_type | TEXT | `reengagement_7d` / `reengagement_21d` / `prebilling` |
| current_step | INTEGER | Which email step is next (0-indexed) |
| last_triggered_at | TIMESTAMPTZ | When the last step was sent |
| status | TEXT | `active` / `completed` / `suppressed` |
| created_at | TIMESTAMPTZ | When the sequence started |

---

## weekly_digest_log
Audit log for every weekly digest attempt. Powers open/click tracking and delivery analytics.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | Log record identifier |
| tenant_id | UUID FK | References tenants.id |
| sent_at | TIMESTAMPTZ | When the digest was sent |
| open_at | TIMESTAMPTZ | When tenant opened the email (Resend webhook) |
| click_at | TIMESTAMPTZ | When tenant clicked a link (Resend webhook) |
| value_metrics | JSONB | The `by_type` event summary used to generate this digest |
| skipped_reason | TEXT | `no_usage` if skipped — NULL if sent |

---

## opportunity_pipeline
Research agent output. Every validated micro SaaS opportunity lives here from discovery through MRR tracking.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | Opportunity identifier |
| vertical | TEXT | Industry vertical (e.g. `Healthcare / Medical Front Desk`) |
| pain_point | TEXT | Specific documented pain, sourced from observable data |
| icp | JSONB | Ideal customer profile: business_type, decision_maker, company_size, revenue_range |
| solution_concept | TEXT | One-sentence description of the micro SaaS tool |
| mrr_calculation | TEXT | Show-your-math string for MRR potential |
| competitor_pricing_avg | NUMERIC(10,2) | Average monthly price across observed competitors |
| conservative_mrr_potential | NUMERIC(10,2) | Realistic MRR within 90 days. DB constraint: >= 4000 or rejected |
| competition_density | TEXT | `red` / `yellow` / `green` |
| competition_density_reason | TEXT | Why it was scored this way |
| build_confidence_score | INTEGER | Composite 0–100 score: search volume (25) + WTP (25) + density (25) + stack (25) |
| build_confidence_reason | TEXT | Explanation of the score |
| retention_hooks | JSONB | weekly_value_metric, milestone_sequence, adjacent_pain, natural_integration, churn_risk_window |
| competitor_examples | JSONB | Array of {name, url, monthly_price, notable_weakness} |
| source_urls | JSONB | Evidence URLs for the pain point |
| tier_structure | JSONB | Tier 1/2/3 pricing and unlock triggers |
| mcp_integration_surface | TEXT | What this tool exposes via MCP |
| stack_compatible | BOOLEAN | Whether it can be built on the standard stack |
| stack_compatibility_notes | TEXT | Any constraints or dependencies |
| estimated_build_weeks | INTEGER | Estimated weeks to MVP |
| status | TEXT | `discovered` → `validated` → `READY_TO_BUILD` → `building` → `launched` → `tracking_mrr` |
| rejection_reason | TEXT | Why it was rejected (aggregator output) |
| owner | TEXT | `Kelvin` / `Son` / `TBD` |
| mrr_actual | NUMERIC(10,2) | Actual MRR once launched (tracked monthly) |
| notes | TEXT | Kelvin's freeform notes |
| created_at / updated_at | TIMESTAMPTZ | Standard timestamps |
