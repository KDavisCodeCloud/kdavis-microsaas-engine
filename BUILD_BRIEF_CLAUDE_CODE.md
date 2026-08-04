# BUILD_BRIEF_CLAUDE_CODE.md
# Showing Signal — Post-Showing Automation Middleware for Buyer's Agents

---

## Product Name
**Showing Signal**

---

## One-Paragraph Pitch

Showing Signal is a post-showing automation middleware that sits between ShowingTime and the tools buyer's agents already use. When a showing is confirmed, rescheduled, or completed — and when feedback arrives — Showing Signal fires the CRM updates, SMS reminders, email sequences, and nurturing workflows that ShowingTime's MLS-coordinator architecture was never built to trigger. ShowingTime has no native Zapier connector and Zillow's business model actively deprioritizes open CRM integration; this gap is architectural, not temporary. Independent buyer's agent teams stop losing warm leads to silence after showings and replace manual follow-up copy-paste with intelligent, timing-sensitive automation that keeps buyers engaged until they write an offer.

---

## Target Customer

**Primary:** Buyer's agents at independent teams (2–12 agents), US market, using ShowingTime for showing scheduling. These teams lack the ops staff to hand-stitch ShowingTime → CRM → SMS manually and cannot afford enterprise ISA solutions. They book 15–60 showings/month across active buyers and feel the drop-off most acutely when feedback arrives but no follow-up fires.

**Buyer persona:** The "Team Lead Agent" who manages junior agents, owns the CRM (Follow Up Boss, kvCORE, LionDesk, or HubSpot), and is personally losing deals to silence.

---

## Conservative MRR Potential

**$6,586 MRR** (as stated in research report — no per-seat or conversion rate breakdown was provided in source data; do not assume underlying metrics).

---

## Core Feature List — Build Order

> Build in strict sequence. Do not begin a phase until the prior phase passes smoke tests.

### Phase 0 — Foundation & Infra
1. **Multi-tenant Postgres schema** — `tenant_id` UUID on every table, Row-Level Security (RLS) policies enforced at DB layer, not application layer
2. **Auth** — Clerk or Supabase Auth; org-scoped JWTs that carry `tenant_id`
3. **`POST /events` endpoint** — central event bus; every agent action, every automation trigger, every webhook receipt MUST fire here before any side-effect executes
4. **CLAUDE.md non-negotiables loaded** (see section below)
5. **Stripe billing scaffold** — three-tier plans wired, webhook listener for `customer.subscription.*` events, plan limits enforced in middleware

### Phase 1 — ShowingTime Ingestion
6. **ShowingTime webhook receiver** — `POST /integrations/showingtime/webhook`; verify HMAC if available; parse confirmation, cancellation, reschedule, feedback-received event types
7. **Email polling fallback** — IMAP/OAuth Gmail/Outlook connector to parse ShowingTime confirmation emails for teams where webhook access is not available (primary workaround given ShowingTime's closed API posture)
8. **Event normalization layer** — canonicalize all ShowingTime inputs into internal `ShowingEvent` schema regardless of ingestion path
9. **Deduplication** — idempotency key per showing ID + event type; prevent double-triggers on retry

### Phase 2 — CRM Connectors (write-side)
10. **Follow Up Boss adapter** — POST contact note, update lead stage, tag with showing outcome
11. **kvCORE adapter** — REST API integration, activity logging
12. **LionDesk adapter** — activity + drip campaign trigger
13. **HubSpot adapter** — deal stage update, note creation, enrollment in workflow via HubSpot API
14. **Generic webhook output** — for CRMs not natively supported; user configures endpoint + payload template

### Phase 3 — Automation Engine
15. **Trigger → Action rule builder** — visual if/then: `[ShowingTime Event Type]` + `[Conditions]` → `[Actions]`
16. **Action types:** Send SMS (Twilio), Send email (SendGrid), Update CRM record (Phase 2 adapters), Add to drip sequence, Notify agent (Slack or email), Wait/delay step
17. **Feedback routing** — when ShowingTime feedback arrives, classify sentiment (positive/neutral/negative) and branch automation accordingly
18. **Drip sequence engine** — multi-step, time-delayed sequences; day 0 / day 2 / day 5 / day 14 cadences pre-built per vertical
19. **Template library** — 12 pre-written SMS and email templates for post-showing scenarios (thank-you, feedback follow-up, re-engagement, price-drop alert hook)

### Phase 4 — Retention & Reporting
20. **Showing activity dashboard** — per-tenant: showings ingested, automations fired, response rates, lead outcome tracking
21. **Automation audit log** — every fired action logged with timestamp, payload snapshot, delivery status
22. **Agent-level reporting** — team leads see per-agent showing volume and follow-up compliance
23. **Weekly digest email** — automated Sunday summary to team lead (fires via `POST /events` → SendGrid)
24. **Health monitor** — alert tenant if ShowingTime ingestion has been silent >48h (connector may be broken)

### Phase 5 — Growth & Onboarding
25. **Guided setup wizard** — 5-step onboarding: connect ShowingTime → connect CRM → configure first automation → send test showing event → confirm delivery
26. **Referral engine** — agent referral codes, tracked via `POST /events`; discount applied in Stripe
27. **Public API** — documented REST API for power users and integration partners

---

## 6 Required Retention Loops (Real Estate — Buyer's Agent Vertical)

Retention loops must be instrumented. Each loop fires a `POST /events` call tagged with `loop_id`.

| # | Loop Name | Mechanism | Trigger | Why It Sticks |
|---|-----------|-----------|---------|---------------|
| 1 | **First Automation Win** | When the system fires its first successful SMS/email on behalf of a new tenant, send the agent a "Your first automation just fired" push + email showing exactly what went out and when | Showing ingested + automation executed within first 7 days | Creates visceral "it works" moment before trial fatigue; replaces skepticism with proof |
| 2 | **Feedback-to-Action Loop** | Every time ShowingTime feedback arrives and Showing Signal routes it into a branched sequence, the agent sees a real-time feed card: "Negative feedback on 123 Main → re-engagement sequence started for [Buyer Name]" | Feedback webhook received | Agents credit the system, not themselves, for follow-up speed; creates dependency |
| 3 | **Weekly ROI Digest** | Sunday 7am local-time email: showings processed, automations fired, response events logged, estimate of "hours saved" (based on automation count × configurable minutes-per-manual-task) | Cron, weekly | Gives team lead a number to justify the subscription to their broker/spouse; churn requires un-seeing that number |
| 4 | **CRM Hygiene Score** | Monthly score (0–100) measuring how consistently the team's CRM records are updated post-showing vs. showing volume; visible on dashboard, emailed to team lead | Monthly cron + dashboard always-visible | Creates a metric the team lead owns and defends; dropping score triggers re-engagement |
| 5 | **Drip Sequence Graduation** | When a buyer completes a full nurture sequence without converting, Showing Signal surfaces a "This buyer went cold — here's a re-activation playbook" prompt with one-click sequence restart | Sequence completion event with no conversion signal | Closes the loop on leads that fell through; agent associates the rescue moment with the product |
| 6 | **Team Expansion Wedge** | When a solo agent adds a second agent to their account (or a team lead invites a junior), both receive onboarding sequences, and the team lead sees a "Team Performance" tab unlock on their dashboard | `agent_added` event | Seats create switching cost; team visibility creates political cost of churning (team lead must explain why they're removing the tool their team uses) |

---

## Data Model Sketch

```
tenants
  id              UUID PK
  name            TEXT
  plan_tier       ENUM('starter','growth','team')
  stripe_customer_id TEXT
  created_at      TIMESTAMPTZ

agents
  id              UUID PK
  tenant_id       UUID FK → tenants (RLS)
  name            TEXT
  email           TEXT
  phone           TEXT
  role            ENUM('owner','admin','agent')
  created_at      TIMESTAMPTZ

integrations
  id              UUID PK
  tenant_id       UUID FK → tenants (RLS)
  provider        ENUM('showingtime_webhook','showingtime_email','followupboss','kvcore','liondesk','hubspot','generic_webhook')
  credentials     JSONB  -- encrypted at rest
  status          ENUM('active','error','paused')
  last_ingested_at TIMESTAMPTZ
  created_at      TIMESTAMPTZ

showing_events
  id              UUID PK
  tenant_id       UUID FK → tenants (RLS)
  external_id     TEXT  -- ShowingTime's own identifier
  event_type      ENUM('confirmed','cancelled','rescheduled','feedback_received','completed')
  property_address TEXT
  showing_time    TIMESTAMPTZ
  buyer_name      TEXT
  buyer_email     TEXT
  buyer_phone     TEXT
  feedback_text   TEXT
  feedback_sentiment ENUM('positive','neutral','negative','unclassified')
  raw_payload     JSONB
  idempotency_key TEXT UNIQUE
  created_at      TIMESTAMPTZ

automation_rules
  id              UUID PK
  tenant_id       UUID FK → tenants (RLS)
  name            TEXT
  trigger_event_type TEXT
  trigger_conditions JSONB
  is_active       BOOLEAN DEFAULT true
  created_by      UUID FK → agents
  created_at      TIMESTAMPTZ

automation_actions
  id              UUID PK
  rule_id         UUID FK → automation_rules
  tenant_id       UUID FK → tenants (RLS)
  step_order      INTEGER
  action_type     ENUM('send_sms','send_email','update_crm','enroll_drip','notify_agent','wait')
  action_config   JSONB
  delay_minutes   INTEGER DEFAULT 0

automation_executions
  id              UUID PK
  tenant_id       UUID FK → tenants (RLS)
  rule_id         UUID FK → automation_rules
  showing_event_id UUID FK → showing_events
  status          ENUM('queued','running','completed','failed','skipped')
  started_at      TIMESTAMPTZ
  completed_at    TIMESTAMPTZ
  error_message   TEXT

action_logs
  id              UUID PK
  tenant_id       UUID FK → tenants (RLS)
  execution_id    UUID FK → automation_executions
  action_id       UUID FK → automation_actions
  status          ENUM('sent','delivered','failed','bounced')
  provider_response JSONB
  fired_at        TIMESTAMPTZ

events
  id              UUID PK
  tenant_id       UUID FK → tenants (RLS)  -- NULL allowed for system events
  event_name      TEXT  -- e.g. 'automation.fired', 'showing.ingested', 'agent.added'
  loop_id         TEXT  -- retention loop tag if applicable
  actor_type      ENUM('system','agent','webhook','cron')
  actor_id        UUID
  payload         JSONB
  created_at      TIMESTAMPTZ

drip_sequences
  id              UUID PK
  tenant_id       UUID FK → tenants (RLS)
  name            TEXT
  steps           JSONB  -- ordered array of {delay_days, channel, template_id}
  created_at      TIMESTAMPTZ

templates
  id              UUID PK
  tenant_id       UUID FK → tenants (RLS)  -- NULL = system template
  channel         ENUM('sms','email')
  name            TEXT
  subject         TEXT  -- email only
  body            TEXT  -- supports {{buyer_name}}, {{property_address}}, {{agent_name}} tokens
  is_system       BOOLEAN DEFAULT false
  created_at      TIMESTAMPTZ
```

**RLS policy pattern (apply to every table with `tenant_id`):**
```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <table>
  USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

---

## CLAUDE.md Non-Negotiables

> Copy this block verbatim into `CLAUDE.md` at repo root. Claude Code must read this before writing any code.

```markdown
# CLAUDE.md — Showing Signal Non-Negotiables

## These rules are not suggestions. Do not ship code that violates them.

### 1. tenant_id + RLS on Every Table
- Every database table MUST have a `tenant_id UUID NOT NULL` column.
- Row-Level Security MUST be enabled on every table.
- RLS policies MUST enforce tenant isolation at the database layer.
- Application-layer tenant filtering (WHERE tenant_id = ?) is ADDITIONAL to RLS, never a replacement.
- No exceptions. No junction/pivot tables without tenant_id. No lookup tables without assessing whether they need tenant_id.
- Before creating any migration, ask: "Does this table need tenant_id?" If uncertain, add it.

### 2. POST /events on Every Agent Action
- Every action taken by the system, any agent, any webhook, any cron job MUST emit a structured event to the internal `POST /events` endpoint before or concurrent with the side effect.
- "Agent action" includes: automation fired, showing ingested, CRM record updated, SMS sent, email sent, drip sequence enrolled, agent added, integration connected/disconnected, plan changed, login, referral triggered.
- The events table is the audit log, the analytics source, and the retention loop instrumentation. It is non-optional.
- Event emission failures must be logged but MUST NOT block the primary action (fire-and-log pattern).
- Every event MUST carry: tenant_id, event_name, actor_type, actor_id, payload, created_at.
- Retention loop events MUST carry loop_id matching the 6 defined loops.

### 3. $4,000 MRR Floor Gate
- The product must not be considered "launchable" until the billing and plan enforcement scaffold can demonstrably support reaching $4,000 MRR.
- This means: Stripe integration is live (not mocked), plan tiers are enforced (automation rule limits, agent seat limits, integration counts), and the subscription lifecycle (trial → paid → cancel → reactivate) is fully handled.
- The conservative MRR target for this product is $6,586. Do not architect for fewer customers than that floor implies.

### 4. Encryption of Credentials
- All values stored in `integrations.credentials` MUST be encrypted at rest using application-level encryption (e.g., libsodium secretbox or Postgres pgcrypto) before INSERT.
- Decryption happens only at the time of use in the integration adapter.
- Never log decrypted credentials. Never return them via API.

### 5. Idempotency on All Inbound Webhooks
- ShowingTime webhook and email parser results MUST write an `idempotency_key` (showing_id + event_type) before processing.
- Duplicate keys MUST be silently skipped (return 200, take no action).
- This prevents double-automation fires on retry storms.

### 6. No Invented Metrics
- Do not generate, display, or store "estimated" or "projected" metrics unless they are derived from real event data in the events table or action_logs table.
- The "hours saved" digest calculation MUST be clearly labeled as an estimate and based on a user-configurable minutes-per-task value, defaulting to a clearly labeled assumption.
- Do not hardcode industry conversion rates, average close rates, or income projections into the product UI.

### 7. Tech Stack Defaults (override only with documented reason)
- **Database:** Postgres (Supabase or self-hosted)
- **Backend:** Node.js/TypeScript (Fastify) or Python (FastAPI) — choose one, do not mix
- **Frontend:** Next.js 14+ App Router, Tailwind CSS
- **Auth:** Clerk (preferred) or Supabase Auth
- **SMS:** Twilio Programmable Messaging
- **Email:** SendGrid
- **Job Queue:** BullMQ (Redis-backed) for delayed/scheduled automation steps
- **Payments:** Stripe Billing

### 8. Brand Colors
- Primary accent: #5a96ff
- Secondary accent: #f5a623
- Mood: neutral/adaptable
- Do not use these as hard-coded strings