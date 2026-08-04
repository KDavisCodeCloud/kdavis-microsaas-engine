# BUILD_BRIEF_CLAUDE_CODE.md
# Pulse Message Bridge

---

## 1. Product Name & Pitch

**Product:** Pulse Message Bridge

**Pitch:** Pulse Message Bridge is a flat-rate SMS/WhatsApp orchestration layer that sits between mid-market B2B SaaS support teams and Intercom, replacing unpredictable per-message and per-resolution overages with a single, transparent monthly invoice. Teams using Intercom with 10–50 agents consistently report billing anxiety driven by stacked seat costs, $0.99-per-Fin-resolution charges, and volume-based SMS/WhatsApp fees — a pain point verified across 200+ Capterra reviews and Intercom's own 2026 pricing documentation. Pulse solves this by proxying all outbound SMS and WhatsApp traffic through a managed messaging layer billed at a predictable flat rate ($299–$999/mo), giving support ops leaders the cost certainty they need to forecast, hire, and scale without spreadsheet archaeology every billing cycle.

---

## 2. Target Customer

| Attribute | Detail |
|---|---|
| **Company type** | Mid-market B2B SaaS companies |
| **Support team size** | 10–50 Intercom agents |
| **Primary buyer** | VP/Head of Customer Support or Support Operations Manager |
| **Secondary buyer** | CFO / Finance who reconciles SaaS spend |
| **Current stack** | Intercom (required), Twilio or similar for SMS, WhatsApp Business API |
| **Trigger event** | Monthly Intercom invoice spike; budget planning season; new support ops hire |
| **Geographic scope** | Not specified in research report — do not assume |

---

## 3. Core Feature List (Build Order)

Build in strict sequence. Do not start a phase until prior phase passes smoke tests and `/events` endpoint is logging correctly.

### Phase 0 — Foundation & Auth
1. **Tenant provisioning** — multi-tenant schema, `tenant_id` on every table, Supabase RLS policies enforced at DB layer
2. **Auth** — email/password + magic link via Supabase Auth; JWT propagated to all API calls
3. **CLAUDE.md non-negotiables scaffold** — `/events` POST endpoint live and accepting payloads before any agent action is wired

### Phase 1 — Intercom Integration Core
4. **OAuth connection flow** — connect tenant's Intercom workspace via OAuth 2.0; store tokens encrypted at rest
5. **Webhook ingestion** — receive `conversation.created`, `conversation.user.replied`, `conversation.part.created` events from Intercom
6. **Conversation sync** — pull and store conversation metadata (no PII beyond what is operationally required) scoped by `tenant_id`

### Phase 2 — Messaging Orchestration
7. **SMS provider abstraction layer** — pluggable provider interface (Twilio as default); tenant configures credentials in settings; Pulse proxies outbound calls
8. **WhatsApp provider abstraction layer** — same interface pattern; WhatsApp Business API (Meta) as default
9. **Outbound message router** — receives trigger from Intercom webhook, determines channel (SMS vs WhatsApp) by contact preference rules, dispatches via provider layer, logs delivery receipt
10. **Inbound reply ingestion** — receive SMS/WhatsApp replies via provider webhooks; inject back into correct Intercom conversation thread as a note or reply

### Phase 3 — Billing & Usage Metering
11. **Usage metering engine** — count outbound messages per tenant per billing period; store in `message_events` table; this is for display only — billing is flat-rate, not per-message
12. **Flat-rate subscription billing** — Stripe integration; three tiers mapped to research report range ($299/$599/$999 suggested; exact tier names TBD by operator); webhook handlers for `invoice.paid`, `invoice.payment_failed`, `customer.subscription.deleted`
13. **Billing dashboard** — show current period message volume, flat monthly cost, projected vs. actual Intercom overage savings (calculated from provider cost estimates vs. flat fee paid)

### Phase 4 — Tenant Dashboard & Configuration
14. **Routing rules UI** — per-tenant rules: which Intercom tags/segments trigger SMS vs. WhatsApp vs. no-op
15. **Message templates** — CRUD for approved outbound message templates; template variable substitution (`{{contact.name}}`, `{{ticket.id}}`)
16. **Agent activity feed** — real-time log of all messages routed in current session, with status (queued / sent / delivered / failed)
17. **Team inbox view** — filterable list of all bridged conversations across channels

### Phase 5 — Retention & Analytics
18. **Savings calculator widget** — dashboard widget showing estimated monthly savings vs. Intercom pay-as-you-go SMS; inputs: avg message volume, Intercom's published per-message rate
19. **Monthly digest email** — automated summary of volume, savings, delivery rates; sent on billing cycle close
20. **Anomaly alerts** — if message volume spikes >2× 7-day average, notify tenant admin via email (possible runaway automation)

---

## 4. The 6 Required Retention Loops

*Retention loops must be instrumented with `/events` calls at entry and exit of each loop.*

| # | Loop Name | Trigger | Engagement Mechanic | Re-engagement Hook |
|---|---|---|---|---|
| 1 | **Savings Realization Loop** | Monthly invoice closes | Dashboard surfaces "You saved $X vs. per-message billing this month" based on volume × Intercom's published rate | If tenant hasn't logged in during billing cycle, email digest forces re-engagement with savings number as subject line |
| 2 | **Routing Rule Optimization Loop** | Delivery failure rate >5% on any channel in a 7-day window | In-app prompt: "3 messages failed on WhatsApp last week — update your routing rules?" with one-click to rules editor | Weekly routing health score email if failures detected |
| 3 | **Template Library Growth Loop** | Agent sends a free-text outbound message (not a saved template) | Post-send prompt: "Save this as a template for your team?" — one click saves to library | Monthly "Your team has X unsaved message patterns" nudge to admins |
| 4 | **Integration Depth Loop** | Tenant has been active 14+ days but has only connected one channel (SMS or WhatsApp, not both) | In-app checklist: "Unlock full coverage — add WhatsApp to your bridge" with estimated incremental reach | Day 21 email: "Teams using both channels report fewer missed replies" (no metric invented — reframe as capability, not statistic) |
| 5 | **Seat Expansion Loop** | Tenant's Intercom conversation volume grows (tracked via webhook volume increase >30% month-over-month) | Prompt: "Your volume has grown — you may be approaching a tier where upgrading saves more" with tier comparison modal | Triggered upgrade suggestion email from billing engine |
| 6 | **Billing Certainty Loop** | Each Stripe `invoice.paid` event | Immediate post-payment confirmation page shows "Your messaging costs are locked for another 30 days" with volume headroom indicator | If payment fails, urgency email: "Your SMS/WhatsApp routing is paused — restore billing to keep conversations flowing" |

---

## 5. Data Model Sketch

> All tables require `tenant_id UUID NOT NULL` and a corresponding RLS policy `USING (tenant_id = auth.jwt() ->> 'tenant_id')`. No exceptions.

```sql
-- TENANTS
tenants (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT now(),
  billing_tier    TEXT CHECK (billing_tier IN ('starter','growth','scale')),
  stripe_customer_id TEXT,
  stripe_subscription_id TEXT,
  subscription_status TEXT
)

-- TENANT MEMBERS
tenant_members (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenants(id),
  user_id     UUID NOT NULL REFERENCES auth.users(id),
  role        TEXT CHECK (role IN ('admin','member')),
  created_at  TIMESTAMPTZ DEFAULT now()
  -- RLS: tenant_id = auth.jwt() ->> 'tenant_id'
)

-- INTERCOM CONNECTIONS
intercom_connections (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id),
  workspace_id    TEXT NOT NULL,
  access_token    TEXT NOT NULL,  -- encrypted at rest
  token_type      TEXT,
  connected_at    TIMESTAMPTZ DEFAULT now(),
  last_verified_at TIMESTAMPTZ
  -- RLS: tenant_id
)

-- PROVIDER CREDENTIALS (SMS / WhatsApp)
messaging_providers (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id),
  provider_type   TEXT CHECK (provider_type IN ('twilio_sms','whatsapp_meta')),
  credentials     JSONB NOT NULL,  -- encrypted; contains account_sid, auth_token, phone_number_id
  is_active       BOOLEAN DEFAULT true,
  created_at      TIMESTAMPTZ DEFAULT now()
  -- RLS: tenant_id
)

-- ROUTING RULES
routing_rules (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id),
  rule_name       TEXT,
  priority        INT DEFAULT 0,
  match_type      TEXT CHECK (match_type IN ('tag','segment','all')),
  match_value     TEXT,
  target_channel  TEXT CHECK (target_channel IN ('sms','whatsapp','none')),
  is_active       BOOLEAN DEFAULT true,
  created_at      TIMESTAMPTZ DEFAULT now()
  -- RLS: tenant_id
)

-- MESSAGE TEMPLATES
message_templates (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id),
  name            TEXT NOT NULL,
  channel         TEXT CHECK (channel IN ('sms','whatsapp')),
  body            TEXT NOT NULL,  -- supports {{variable}} tokens
  created_by      UUID REFERENCES auth.users(id),
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
  -- RLS: tenant_id
)

-- BRIDGED CONVERSATIONS
bridged_conversations (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           UUID NOT NULL REFERENCES tenants(id),
  intercom_conversation_id TEXT NOT NULL,
  contact_external_id TEXT,
  channel             TEXT CHECK (channel IN ('sms','whatsapp')),
  status              TEXT CHECK (status IN ('active','closed','error')),
  created_at          TIMESTAMPTZ DEFAULT now(),
  last_activity_at    TIMESTAMPTZ
  -- RLS: tenant_id
)

-- MESSAGE EVENTS (metering + audit log)
message_events (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id               UUID NOT NULL REFERENCES tenants(id),
  bridged_conversation_id UUID REFERENCES bridged_conversations(id),
  direction               TEXT CHECK (direction IN ('outbound','inbound')),
  channel                 TEXT CHECK (channel IN ('sms','whatsapp')),
  provider_message_id     TEXT,
  template_id             UUID REFERENCES message_templates(id),
  status                  TEXT CHECK (status IN ('queued','sent','delivered','failed','received')),
  failure_reason          TEXT,
  sent_at                 TIMESTAMPTZ,
  delivered_at            TIMESTAMPTZ,
  billing_period          TEXT,  -- YYYY-MM for aggregation
  created_at              TIMESTAMPTZ DEFAULT now()
  -- RLS: tenant_id
)

-- SYSTEM EVENTS (agent action audit log — feeds POST /events)
events (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenants(id),
  event_type  TEXT NOT NULL,
  actor_id    UUID,  -- user or system
  actor_type  TEXT CHECK (actor_type IN ('user','agent','system')),
  payload     JSONB,
  created_at  TIMESTAMPTZ DEFAULT now()
  -- RLS: tenant_id
)

-- BILLING SNAPSHOTS (immutable record per billing cycle close)
billing_snapshots (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           UUID NOT NULL REFERENCES tenants(id),
  billing_period      TEXT NOT NULL,  -- YYYY-MM
  total_messages_sent INT DEFAULT 0,
  total_messages_recv INT DEFAULT 0,
  flat_rate_charged   NUMERIC(10,2),
  stripe_invoice_id   TEXT,
  created_at          TIMESTAMPTZ DEFAULT now()
  -- RLS: tenant_id
)
```

---

## 6. CLAUDE.md Non-Negotiables

The following rules are absolute constraints. Claude Code must refuse to generate code that violates them and must flag any PR or scaffold that omits them.

```markdown
# CLAUDE.md — Pulse Message Bridge
# Non-Negotiable Engineering Constraints

## 1. TENANT ISOLATION — EVERY TABLE
- Every database table MUST have `tenant_id UUID NOT NULL`.
- Every table MUST have a Supabase Row Level Security (RLS) policy:
  `USING (tenant_id = (auth.jwt() ->> 'tenant_id')::uuid)`
- RLS must be ENABLED on the table (`ALTER TABLE x ENABLE ROW LEVEL SECURITY`).
- No migration is valid without both the column and the policy.
- There are NO exceptions for lookup tables, template tables, or event tables.

## 2. POST /events ON EVERY AGENT ACTION
- Every action taken by an automated agent, background job, or system process
  MUST emit a POST to the internal `/events` endpoint before the action completes.
- Payload schema (minimum):
  {
    "tenant_id": "<uuid>",
    "event_type": "<snake_case_string>",
    "actor_type": "agent" | "user" | "system",
    "actor_id": "<uuid or null>",
    "payload": { ...action-specific context }
  }
- /events must be instrumented BEFORE any feature that triggers agent actions ships.
- Failure to emit to /events is a blocking bug, not a warning.

## 3. $4,000 MRR FLOOR
- The conservative MRR potential for this product is $6,400/mo (from research report).
- The system must support billing tiers that, at minimum, can reach $4,000 MRR.
- Stripe integration is NOT optional. It is a Phase 3 blocker — no feature beyond
  Phase 2 ships without Stripe subscription webhooks active and tested.
- Trial periods, if offered, must have a hard cutoff enforced server-side via
  Stripe subscription status check on every authenticated request.
- The billing engine must handle `invoice.payment_failed` by suspending outbound
  message routing (not by silently continuing service).

## 4. ENCRYPTED CREDENTIALS
- `intercom_connections.access_token` and `messaging_providers.credentials`
  MUST be encrypted at rest before insertion. Use Supabase Vault or equivalent.
- Never log raw tokens. Mask in all application logs as `[REDACTED]`.

## 5. NO INVENTED METRICS
- The savings calculator widget may only use Intercom's publicly documented
  per-message rates as inputs. Do not hardcode estimated savings figures.
- All dashboard copy that references cost savings must be clearly labeled
  as "estimated" and sourced from user-configurable or publicly documented rates.

## 6. PROVIDER ABSTRACTION IS MANDATORY
- SMS and WhatsApp providers must be accessed through the abstraction interface,
  never called directly from route handlers or webhook processors.
- This ensures provider swap (e.g., Twilio → Vonage) requires only one file change.

## 7. MULTI-TENANCY IS NOT A FEATURE FLAG
- There is no "single tenant mode." Every code path assumes multi-tenancy.
- Do not write any helper that skips tenant_id filtering "for simplicity."
```

---

## 7. Key Research Report Flags

| Item | Status |
|---|---|
| Conservative MRR potential | **$6,400/mo** (from report) |
| Tier pricing exact figures | **Not specified** in report — $299/$599/$999 are illustrative range endpoints only; operator must define before billing goes live |
| Geographic markets | **Not specified** — do not hardcode country codes or currency assumptions beyond USD |
| Specific retention metrics (churn rate, NPS, etc.) | **Not provided** — retention loop copy must not cite