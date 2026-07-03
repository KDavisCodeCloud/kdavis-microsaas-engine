# CLAUDE.md — Micro SaaS Engine
**Read this file completely before every session. No exceptions.**

---

## Who You Are Building For

Kelvin Davis (King Kelz) — Senior Cloud/DevOps Platform Engineer, founder of KDavis Agentic Systems LLC. Stack: Azure, AWS, Kubernetes, Terraform, FastAPI, LangGraph, Next.js 14, Supabase, n8n. Working path: `/mnt/c/Users/Kelvin/projects/` on WSL Ubuntu 22.04. GitHub: `github.com/KDavisCodeCloud`.

Communication style: Direct. No filler. Tradeoffs made concrete. Commands with exact expected output and error handling. Confirmations can be brief. Never use "next generation," "revolutionary," or similar marketing language.

---

## What You Are Building

The Micro SaaS Engine — a research-validated, retention-first software factory inside the Decoded Empire portfolio. This is not a single product. It is a repeatable system for validating, building, launching, and scaling micro SaaS tools.

**Repository:** `kdavis-microsaas-engine`
**Supabase project:** `microsaas-prod` (isolated — never shares tables with other products)
**Domain:** TBD per product
**Billing:** Dedicated Stripe account

---

## Absolute Build Rules

These are non-negotiable. If a task conflicts with any of these rules, stop and flag it before proceeding.

### Rule 1 — Retention Scaffold Ships First
A product is not initialized until all retention infrastructure is confirmed active:
- `usage_events`, `milestones`, `retention_sequences`, `weekly_digest_log` tables created with RLS enabled
- Weekly digest n8n workflow connected and tested
- Re-engagement sequence n8n workflow connected and tested
- `POST /events` FastAPI endpoint operational
- `<UsageTracker />` Next.js component wired into the layout

**No feature work begins until this checklist is complete. Not one component.**

### Rule 2 — Infrastructure Isolation
This product never shares a Supabase instance, database tables, or Stripe account with any other Decoded Empire product. Command Center connects via API only. If you are ever writing a query that joins across product boundaries, stop and flag it immediately.

### Rule 3 — RLS On Every Table
Every Supabase table gets Row Level Security enabled and a policy keyed to `tenant_id` before any data is written. No exceptions. No temporary bypasses.

### Rule 4 — Research Agent Gates Every Build
No product enters active development until the research agent has produced a validated opportunity card with `status: READY_TO_BUILD` in the `opportunity_pipeline` table. If Kelvin asks you to start building a product without this, ask for the opportunity ID first.

### Rule 5 — MRR Floor Is Enforced in Code
The aggregator agent rejects any opportunity where `conservative_mrr_potential < 4000`. This is a hard filter in the quality gate logic, not a soft guideline. Never soften this threshold.

### Rule 6 — Agent Prompts Live in Version Control
Every agent's system prompt lives in `/agents/{agent_name}/prompt.md`. Never hardcode prompts inline in n8n nodes or API calls. If you find one that is, extract it and file it before continuing.

### Rule 7 — MCP Endpoint Ships With Every Product
Every micro SaaS product exposes an MCP server endpoint from day one. This is the integration lock retention mechanism. It is not optional and is not deferred to a later sprint.

### Rule 8 — Stripe Is Per Product
Each micro SaaS product has its own Stripe account and webhook configuration. Never route billing from multiple products through a single Stripe account.

### Rule 9 — Weekly Digest Is Behavioral, Not Marketing
The weekly digest agent generates content from real `usage_events` data for each tenant. It is never a newsletter, never a marketing email, never a generic message. If the digest cannot pull real usage data for a tenant, it does not send for that tenant that week.

### Rule 10 — Exit Readiness Is Always On
Every database table has a corresponding entry in `/docs/data-dictionary.md`. Every architectural decision has a corresponding entry in `/docs/architecture-decisions.md`. These documents are updated in the same commit that creates the table or makes the decision. Diligence readiness is not a pre-exit sprint. It is a continuous practice.

---

## Session Start Protocol

At the start of every Claude Code session, run this sequence:

```bash
# 1. Confirm working directory
pwd
# Expected: /mnt/c/Users/Kelvin/projects/kdavis-microsaas-engine

# 2. Confirm git status
git status

# 3. Confirm Supabase connection
supabase status

# 4. Confirm n8n is reachable
curl -s http://localhost:5678/healthz | jq .

# 5. Read EXECUTION_ORDER.md for current sprint state
cat EXECUTION_ORDER.md
```

Do not proceed with any build work until all five checks pass. If any fail, diagnose and resolve before continuing.

---

## Directory Structure

```
kdavis-microsaas-engine/
├── CLAUDE.md                          ← This file
├── README.md                          ← Architecture, philosophy, exit design
├── EXECUTION_ORDER.md                 ← Current sprint state, what was built, what's next
│
├── agents/
│   ├── orchestrator/
│   │   ├── prompt.md                  ← Orchestrator system prompt
│   │   └── agent.py
│   ├── healthcare-intel/
│   │   ├── prompt.md
│   │   └── agent.py
│   ├── legal-intel/
│   │   ├── prompt.md
│   │   └── agent.py
│   ├── ecommerce-intel/
│   │   ├── prompt.md
│   │   └── agent.py
│   ├── realestate-intel/
│   │   ├── prompt.md
│   │   └── agent.py
│   ├── hr-ops-intel/
│   │   ├── prompt.md
│   │   └── agent.py
│   ├── finance-intel/
│   │   ├── prompt.md
│   │   └── agent.py
│   └── aggregator/
│       ├── prompt.md
│       └── agent.py
│
├── core/
│   ├── sanitization.py                ← DataSanitizationShield (upstream of every LLM call)
│   ├── supabase_client.py
│   ├── llm_router.py                  ← Haiku for scraping, Sonnet for analysis
│   └── retention/
│       ├── digest_generator.py        ← Weekly value digest logic
│       ├── milestone_detector.py      ← Milestone threshold checker
│       └── reengagement_trigger.py    ← Behavioral re-engagement logic
│
├── api/
│   ├── main.py                        ← FastAPI entry point
│   ├── routers/
│   │   ├── events.py                  ← POST /events
│   │   ├── milestones.py              ← GET /milestones/{tenant_id}
│   │   ├── digest.py                  ← POST /digest/preview/{tenant_id}
│   │   ├── pipeline.py                ← Opportunity pipeline CRUD
│   │   └── mcp.py                     ← MCP server endpoint
│   └── middleware/
│       ├── auth.py
│       └── tenant_context.py
│
├── frontend/
│   ├── app/
│   │   ├── dashboard/                 ← Main product dashboard
│   │   ├── pipeline/                  ← Opportunity pipeline CRM
│   │   └── research/                  ← Research agent trigger + results
│   └── components/
│       ├── UsageTracker.tsx           ← Non-negotiable, in root layout
│       ├── MilestoneToast.tsx
│       └── WeeklySnapshot.tsx
│
├── supabase/
│   ├── migrations/
│   │   ├── 001_core_schema.sql        ← All retention tables + RLS
│   │   └── 002_opportunity_pipeline.sql
│   └── seed/
│       └── milestone_definitions.sql
│
├── n8n/
│   ├── weekly-digest-workflow.json    ← Exported n8n workflow
│   └── reengagement-workflow.json     ← Exported n8n workflow
│
├── docs/
│   ├── data-dictionary.md             ← Every table, every column, why it exists
│   └── architecture-decisions.md      ← Why decisions were made (exit diligence doc)
│
└── legal/
    ├── EULA.md
    ├── privacy-policy.md
    └── dpa-template.md
```

---

## Database Schema — Initialize First

Run these in order before any application code is written:

```sql
-- Migration 001: Core retention schema

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Tenants
CREATE TABLE tenants (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name TEXT NOT NULL,
  tier TEXT NOT NULL DEFAULT 'starter' CHECK (tier IN ('starter', 'growth', 'scale')),
  stripe_customer_id TEXT UNIQUE,
  stripe_subscription_id TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'churned')),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Usage events (the heartbeat of retention)
CREATE TABLE usage_events (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_usage_events_tenant_created ON usage_events(tenant_id, created_at DESC);

-- Milestones
CREATE TABLE milestones (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
  milestone_key TEXT NOT NULL,
  threshold INTEGER NOT NULL,
  achieved_at TIMESTAMPTZ,
  notified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(tenant_id, milestone_key)
);

-- Retention sequences
CREATE TABLE retention_sequences (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
  sequence_type TEXT NOT NULL CHECK (sequence_type IN ('reengagement_7d', 'reengagement_21d', 'prebilling')),
  current_step INTEGER DEFAULT 0,
  last_triggered_at TIMESTAMPTZ,
  status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'suppressed')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Weekly digest log
CREATE TABLE weekly_digest_log (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
  sent_at TIMESTAMPTZ DEFAULT NOW(),
  open_at TIMESTAMPTZ,
  click_at TIMESTAMPTZ,
  value_metrics JSONB NOT NULL DEFAULT '{}',
  skipped_reason TEXT
);

-- RLS: Enable on all tables
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE milestones ENABLE ROW LEVEL SECURITY;
ALTER TABLE retention_sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE weekly_digest_log ENABLE ROW LEVEL SECURITY;

-- RLS Policies
CREATE POLICY tenant_isolation ON usage_events
  USING (tenant_id = current_setting('app.tenant_id')::UUID);

CREATE POLICY tenant_isolation ON milestones
  USING (tenant_id = current_setting('app.tenant_id')::UUID);

CREATE POLICY tenant_isolation ON retention_sequences
  USING (tenant_id = current_setting('app.tenant_id')::UUID);

CREATE POLICY tenant_isolation ON weekly_digest_log
  USING (tenant_id = current_setting('app.tenant_id')::UUID);
```

```sql
-- Migration 002: Opportunity pipeline (research agent output)

CREATE TABLE opportunity_pipeline (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  vertical TEXT NOT NULL,
  pain_point TEXT NOT NULL,
  icp JSONB NOT NULL,
  solution_concept TEXT NOT NULL,
  competitor_pricing_avg NUMERIC(10,2),
  conservative_mrr_potential NUMERIC(10,2) NOT NULL,
  competition_density TEXT CHECK (competition_density IN ('red', 'yellow', 'green')),
  build_confidence_score INTEGER CHECK (build_confidence_score BETWEEN 0 AND 100),
  retention_hooks JSONB NOT NULL DEFAULT '{}',
  source_urls JSONB DEFAULT '[]',
  status TEXT DEFAULT 'discovered' CHECK (status IN ('discovered', 'validated', 'rejected', 'READY_TO_BUILD', 'building', 'launched', 'tracking_mrr')),
  rejection_reason TEXT,
  owner TEXT,
  mrr_actual NUMERIC(10,2),
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Hard filter: MRR floor enforced at DB level as a check
ALTER TABLE opportunity_pipeline
  ADD CONSTRAINT mrr_floor_check
  CHECK (conservative_mrr_potential >= 4000 OR status = 'rejected');
```

---

## LLM Routing Rules

| Task | Model | Reason |
|------|-------|--------|
| Web search scraping, raw data ingestion | `claude-haiku-4-5` | High volume, low cost |
| Structured analysis, JSON extraction, scoring | `claude-sonnet-4-6` | Quality output required |
| Complex multi-step reasoning (rare) | `claude-sonnet-4-6` | Default ceiling, no Opus for routine tasks |

Never use a more expensive model where a cheaper one produces acceptable output. Log model used in every agent run for cost tracking.

---

## FastAPI Endpoints — Required Before Feature Work

```
POST   /events                          Log a usage event
GET    /milestones/{tenant_id}          Get milestone state
POST   /digest/preview/{tenant_id}      Preview weekly digest
GET    /pipeline                        List opportunity pipeline
POST   /pipeline                        Create opportunity (research agent output)
PATCH  /pipeline/{id}/status            Update opportunity status
POST   /pipeline/{id}/stamp             Stamp as READY_TO_BUILD (aggregator only)
GET    /health                          Health check
```

All endpoints require tenant context middleware. All endpoints return structured error responses with `error_code`, `message`, and `detail`.

---

## n8n Workflow Specifications

### Weekly Digest (Cron: Sunday 20:00 MST)
```
Trigger: Cron
→ Query Supabase: SELECT all active tenants
→ For each tenant:
   → Query usage_events (last 7 days)
   → If zero events: skip, log skipped_reason = 'no_usage'
   → If events exist:
      → Call Sonnet 4.6: generate personalized digest from events
      → Send via Resend to tenant email
      → Log to weekly_digest_log
```

### Re-engagement Trigger (Cron: Daily 09:00 MST)
```
Trigger: Cron
→ Query Supabase: tenants with no usage_event in last 7 days → fire reengagement_7d
→ Query Supabase: tenants with usage drop >50% week-over-week → fire reengagement_21d
→ Query Supabase: tenants within 5 days of billing renewal with low usage → fire prebilling
→ For each match:
   → Check retention_sequences: is sequence already active?
   → If not: insert sequence, send step 0 email via Resend, update last_triggered_at
   → If active: check current_step, send next step if 48h elapsed
```

---

## Git Commit Convention

```
feat: add healthcare intel agent prompt and agent.py
fix: correct RLS policy on usage_events table
chore: update data dictionary with milestones table
docs: add architecture decision for isolated Supabase project
retention: wire UsageTracker into root layout
agent: add aggregator MRR floor filter logic
```

Every commit that creates a table must include a corresponding `docs:` commit updating the data dictionary in the same push.

---

## What Not to Do

- Do not share Supabase projects across products
- Do not hardcode prompts in n8n nodes or Python files — they go in `/agents/{name}/prompt.md`
- Do not start feature development before retention scaffold is confirmed
- Do not build a product the research agent has not validated
- Do not soften the $4K MRR floor filter
- Do not use `service_role` Supabase key on the frontend — ever
- Do not skip the DataSanitizationShield before any LLM call that processes user or external data
- Do not conflate this repo with the Cloud Decoded repo — they are separate products with separate infrastructure

---

## Current Sprint State

See `EXECUTION_ORDER.md` for what was built last session, what is in progress, and what is next.

When a session ends, update EXECUTION_ORDER.md before closing. The next session starts by reading it.
