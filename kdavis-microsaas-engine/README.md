# Micro SaaS Engine — Root Architecture Document
**KDavis Agentic Systems LLC | Decoded Empire Portfolio**
**Owner: Kelvin Davis (King Kelz)**
**Status: Active Build**

---

## What This Is

The Micro SaaS Engine is a research-validated, retention-first software factory. It is not a single product. It is a repeatable system for identifying, building, launching, and scaling micro SaaS tools — each targeting a specific ICP, solving a specific pain point, and generating a minimum of $4,000 MRR before the next product begins.

Every product built inside this engine shares the same infrastructure, the same retention scaffold, and the same exit-ready architecture. Nothing is built blind. Nothing ships without retention infrastructure active. Nothing is built that the research agent has not validated first.

---

## Why This Exists

The traditional micro SaaS failure pattern:
- Build on instinct
- Launch to silence
- Bolt on retention after churn shows up
- Abandon at month 4 when MRR plateaus

This engine eliminates that pattern entirely. The research agent validates market demand before a line of code is written. The retention scaffold is non-negotiable infrastructure, not an afterthought. The $4K MRR floor filter means every build decision is worth the time investment.

This is how you build a software holding company, not a graveyard of half-finished tools.

---

## Portfolio Position

The Micro SaaS Engine is Product #5 in the Decoded Empire portfolio under KDavis Agentic Systems LLC.

```
Decoded Empire
├── Cloud Decoded          — B2B DevOps agentic automation (anchor/exit vehicle)
├── GTA 6 Hub (DecodedSix) — Traffic engine, ad/affiliate revenue, top of funnel
├── CEO Decoded OS         — One-person business agentic OS (deferred)
├── Hustle Decoded         — Brand, education, movement (runs indefinitely)
└── Micro SaaS Engine      — Research-validated software factory (this repo)
```

The Micro SaaS Engine connects to the Decoded Empire Command Center via API only. It does not share a Supabase instance with any other product. This boundary is intentional and non-negotiable. See Exit Architecture below.

---

## The $4,000 MRR Floor Rule

No product enters the build pipeline unless the research agent validates a realistic path to $4,000/month minimum recurring revenue. This is not a stretch goal. It is the entry threshold.

The filter logic in the research agent aggregator rejects any opportunity where:
- Competitor pricing average × realistic customer volume < $4,000/month
- Market saturation score is red (too many established players)
- Build complexity requires non-SaaS operational overhead
- Integration dependency requires expensive proprietary APIs that compress margin below 85%

If it cannot clear $4K MRR floor, it does not enter the pipeline. Period.

---

## Infrastructure Boundaries (Exit Architecture)

This product is designed to be independently acquirable from day one.

**Dedicated infrastructure:**
- Supabase project: `microsaas-prod` (isolated, no shared tables with other products)
- Repository: `kdavis-microsaas-engine` (standalone, fully documented)
- Stripe account: dedicated to this product only
- Domain: independent product domain
- Vercel project: isolated deployment

**Command Center connection:** API only — never shared database access.

**Why this matters:** At exit, a buyer lifts this product cleanly without touching Cloud Decoded, DecodedSix, or any other portfolio asset. The database boundary is the hardest structural decision to change after the fact. It was made correctly on day one.

**Exit multiple context:** B2B SaaS with proprietary data and documented retention infrastructure exits at 3–5x ARR. Every architectural decision in this repo is made with that multiple in mind.

---

## The Research Agent

Before any product is built, the Micro SaaS Intelligence Agent runs a validated scouting cycle across industry verticals. It outputs a structured JSON card per opportunity containing:

- Industry vertical
- Discovered pain point (sourced from real forum/community data)
- ICP (business type, decision maker title, company size)
- Micro SaaS solution concept
- Competitor pricing average
- Conservative MRR potential
- Competition density score (red/yellow/green)
- Build confidence score (composite)
- Retention hooks (weekly value metric, milestone sequence, adjacent pain, natural integration, churn risk window)

**A product is not stamped READY_TO_BUILD until the research agent has produced this output and the aggregator has cleared it through the quality gate.**

The research agent data lives in the `opportunity_pipeline` Supabase table. Every opportunity has a full audit trail from discovery through build through MRR tracking.

---

## Retention Infrastructure — NON-NEGOTIABLE

Retention is not a feature. It is infrastructure. It ships with every product before any feature work begins.

**The six retention loops:**

1. **Weekly Value Delivery** — Automated personalized digest every Sunday night showing measurable value delivered that week. Not marketing. Proof of ROI in their inbox before they think about canceling.

2. **Milestone System** — Business outcome milestones mapped to real customer results. Each milestone creates switching cost and psychological momentum. Canceling means losing the history.

3. **Expansion Loop** — Every product ships with a natural Tier 2 unlock mapped to the adjacent pain the research agent identified. Customers grow into higher tiers, not out of the product.

4. **Integration Lock** — Every product exposes an MCP endpoint from day one and connects to at least one system the customer already depends on. Switching cost becomes real once data flows both ways.

5. **Re-engagement Sequences** — Behavioral triggers at day 7 (no login), day 21 (usage drop 50%), and pre-billing (low usage before renewal). Automated in n8n. Never manually managed.

6. **Community Layer** — Activated at 10+ customers. Private Slack/Discord across all Micro SaaS Engine products. Identity signal. Ops-managed. Near-zero cost, measurable retention impact.

**Build gate:** A product scaffold is not considered initialized until all six retention tables are created and the weekly digest and re-engagement n8n workflows are confirmed active. Claude Code enforces this via CLAUDE.md.

---

## Database Schema — Core Tables (All Products)

Every micro SaaS product built in this engine initializes with these tables:

```sql
-- Tenant management
tenants (id, name, tier, stripe_customer_id, created_at, status)

-- Usage event stream
usage_events (id, tenant_id, event_type, metadata, created_at)

-- Milestone tracking
milestones (id, tenant_id, milestone_key, threshold, achieved_at, notified_at)

-- Retention sequence state
retention_sequences (id, tenant_id, sequence_type, current_step, last_triggered_at, status)

-- Weekly digest log
weekly_digest_log (id, tenant_id, sent_at, open_at, click_at, value_metrics_json)

-- Opportunity pipeline (research agent output)
opportunity_pipeline (id, vertical, pain_point, icp_json, solution_concept, mrr_potential, confidence_score, retention_hooks_json, status, created_at)
```

Row Level Security is enabled on all tables, keyed to tenant_id. No tenant can access another tenant's data.

---

## Weekly Build Rhythm

```
Monday     — Cloud Decoded + career
Tuesday    — DecodedSix + son (generational handoff session)
Wednesday  — Planning + Command Center
Thursday   — Agent build night (one agent per week, rotating)
Friday     — Light review
Saturday   — Major sprint
Sunday     — Rest
```

Micro SaaS Engine agent builds happen on Thursday nights. One vertical agent per week. Stack verticals until the full intelligence layer covers all target industries.

The research agent is the first Thursday build after Stripe billing is live on Cloud Decoded.

---

## Agent Build Sequence (Thursday Nights)

| Week | Agent | Purpose |
|------|-------|---------|
| 1 | Research Orchestrator | Fan-out controller, manages vertical agents |
| 2 | Healthcare/Medical Desk Intel Agent | Dental, chiro, clinical front desk vertical |
| 3 | Legal/Professional Services Intel Agent | Solo attorneys, small firm ops vertical |
| 4 | E-commerce/Retail Intel Agent | Shopify, DTC, inventory ops vertical |
| 5 | Real Estate Intel Agent | Property mgmt, agent ops, lead mgmt vertical |
| 6 | HR/Ops Intel Agent | SMB hiring, onboarding, scheduling vertical |
| 7 | Finance/Accounting Intel Agent | Bookkeeping, invoicing, cash flow vertical |
| 8 | Aggregator + Quality Gate Agent | Cross-vertical filter, MRR floor check, READY_TO_BUILD stamp |

---

## Monetization Model

**Per product:**
- Tier 1: $49–$99/month (core pain solved)
- Tier 2: $149–$299/month (expansion unlock, usage ceiling hit)
- Tier 3: $499+/month (multi-location or team)

**MRR floor:** $4,000/month per product before next product begins

**Gross margin target:** 93–95% (infrastructure cost stays flat while revenue scales)

**Exit threshold per product:** $10K MRR = $360K–$600K exit at 3–5x ARR with clean data history and documented retention infrastructure

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14, Tailwind CSS, shadcn/ui |
| Backend | FastAPI, Python |
| Database | Supabase (PostgreSQL + RLS + pgvector) |
| Auth | Supabase Auth (magic link) |
| Agent Orchestration | LangGraph + n8n (self-hosted) |
| LLM Routing | Haiku 4.5 (scraping), Sonnet 4.6 (content/analysis) |
| Billing | Stripe (dedicated account per product) |
| Email | Resend |
| Deployment | Vercel (frontend), Railway or Render (FastAPI) |
| MCP Server | Custom endpoint per product |
| Monitoring | Prometheus + Grafana |

---

## Son's Role (Generational Handoff)

Tuesday sessions are designated generational handoff sessions. The Micro SaaS Engine is the primary vehicle for this. The goal is for him to independently own and extend products on the shared infrastructure — not as an employee, but as a builder with ownership.

The research agent dashboard, vertical agent configuration, and opportunity pipeline CRM are the starting handoff points. He learns the system by using the system.

---

## Document Maintenance

This document is the source of truth for why architectural decisions were made. It is updated when:
- A new product enters the pipeline
- A retention loop is added or modified
- Infrastructure boundaries change
- Exit architecture decisions are made

Last updated: July 2026
