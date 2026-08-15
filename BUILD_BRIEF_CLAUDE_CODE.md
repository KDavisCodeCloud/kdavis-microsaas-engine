# BUILD_BRIEF_CLAUDE_CODE.md
# Small Portfolio Hub

---

## Product Name
**Small Portfolio Hub**

---

## One-Paragraph Pitch

Small Portfolio Hub is a property management platform purpose-built for solo landlords who self-manage 3–10 rental units and are tired of being second-class citizens on tools designed for professional property management companies. Where Buildium charges hidden EFT transaction fees ($2.35/transaction on Essential, $1.35 on Growth) and locks live support behind a $192+/month paywall, Small Portfolio Hub offers a single flat-rate plan with transparent all-in pricing, live chat support for every subscriber, and guided onboarding that gets a landlord from sign-up to first rent collection in under 30 minutes — no surprises, no upsell treadmill.

---

## Target Customer

**ICP:** Solo landlord, US-based, self-managing a portfolio of 3–10 residential rental units. Not a property management company. Likely side-income or early semi-retirement. Evaluating or currently on Buildium Essential, Cozy (defunct), or spreadsheets. Pain-aware of hidden fees and poor support access. Willing to pay $49–62/month for simplicity and responsiveness.

---

## Conservative MRR Potential

**$5,880 MRR** (as stated in research report — underlying unit assumptions not provided in source data; do not infer conversion rates or TAM figures beyond this number).

---

## Core Feature List (Build Order)

Build in this sequence. Do not skip ahead. Each phase must be shippable and testable before the next begins.

### Phase 1 — Foundation & Auth
1. **Multi-tenant data model** with `tenant_id` on every table, Row Level Security enforced at the database layer (Postgres/Supabase). No exceptions.
2. **User authentication** — email/password + magic link (no OAuth required at MVP). Single subscription tier per landlord account.
3. **Onboarding wizard** — step-by-step guided flow: Add property → Add unit(s) → Add tenant → Set up rent collection. Completion percentage visible. Target: landlord operational in < 30 minutes.
4. **`POST /events` instrumentation** wired to every agent action from day one (see CLAUDE.md non-negotiables).

### Phase 2 — Property & Tenant Management
5. **Property & Unit CRUD** — property address, unit number, bedrooms/bathrooms, rent amount, status (vacant/occupied).
6. **Tenant profiles** — name, contact info, lease start/end date, monthly rent, security deposit held, linked unit.
7. **Lease document storage** — upload and retrieve PDF leases per tenant (object storage, not in DB).
8. **Maintenance request intake** — tenant-facing web form (shareable link, no tenant login required at MVP) → creates ticket in landlord dashboard with status tracking (Open / In Progress / Resolved).

### Phase 3 — Rent Collection (Flat-Fee, No Hidden Costs)
9. **ACH rent collection via Stripe** — flat per-transaction fee passed through at cost, displayed transparently at setup. Zero markup hidden in pricing. Fee shown on every payment confirmation.
10. **Automated rent reminders** — email to tenant 5 days before due, 1 day before due, and day-of if unpaid.
11. **Payment ledger** — per-unit payment history, exportable CSV.
12. **Late fee calculation** — configurable flat or percentage late fee, auto-logged to ledger (not auto-charged at MVP — landlord confirms before applying).

### Phase 4 — Communication & Support
13. **Live chat widget** — Intercom or Crisp embedded; available to all subscribers (this is the core competitive differentiator vs. Buildium Essential's email-only support). SLA target: < 4 hour first response during business hours.
14. **Landlord–tenant messaging log** — lightweight in-app message thread per tenant, stored and searchable. Not a replacement for email; a paper trail.
15. **Announcement broadcast** — landlord sends one message to all tenants of a property (e.g., maintenance notice).

### Phase 5 — Reporting & Retention
16. **Portfolio dashboard** — occupancy rate, rent collected this month vs. expected, outstanding balances, open maintenance tickets. All in one screen.
17. **Monthly income/expense summary** — exportable PDF, formatted for Schedule E tax prep handoff (note: not tax advice; label clearly).
18. **Vacancy alert** — when a lease end date is within 60 days, surface a prompt to begin re-listing workflow (integration stub only at MVP; no listing syndication yet).

---

## The 6 Required Retention Loops (Property Management Vertical)

The research report did not enumerate specific retention hooks. The following six are derived from the vertical and ICP:

1. **Rent Collection Habit Loop**
Every month, the platform is the mechanism through which rent arrives. Each successful collection sends a confirmation email to the landlord with a ledger snapshot — reinforcing that the platform is the source of truth. Missed collections trigger a dashboard alert, pulling the landlord back in.

2. **Lease Expiration Countdown**
Surfaced 60 days before each lease ends: "Unit 2A lease expires in 58 days — start renewal or re-list?" Creates a recurring, calendar-driven re-engagement event tied to the landlord's real financial stakes.

3. **Maintenance Ticket Closure Loop**
Open tickets are displayed on every dashboard login with elapsed time since opening. Resolution is satisfying (one-click close with optional note). Landlords return to update status because an unresolved ticket is visually unfinished — zero-inbox psychology applied to property ops.

4. **Monthly Portfolio Snapshot Email**
Automated on the 1st of each month: total rent collected, vacancies, open maintenance, net balance vs. last month. Designed to be the one email a small landlord forward to their accountant or spouse. Sharing reinforces the product's role as the landlord's operating system.

5. **Onboarding Completion Progress Bar**
Persistent until 100% complete (property added, tenant added, first rent collection initiated, first maintenance form shared with tenant). Incomplete progress bars are psychologically aversive — drives early activation across all core features before churn window.

6. **Annual Tax Export Prompt**
Each January, a prominent in-app banner: "Your 2024 income/expense summary is ready — download your Schedule E prep report." Creates a once-a-year high-value touch that makes canceling the subscription feel financially risky in Q4/Q1.

---

## Data Model Sketch

> All tables require `tenant_id UUID NOT NULL` and RLS policy `USING (tenant_id = auth.uid())`. No table is exempt.

```sql
-- ACCOUNTS (one per landlord, maps to auth user)
accounts (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL UNIQUE,  -- = auth.uid()
  owner_name TEXT,
  email TEXT,
  subscription_status TEXT,        -- 'trialing' | 'active' | 'past_due' | 'canceled'
  stripe_customer_id TEXT,
  created_at TIMESTAMPTZ
)

-- PROPERTIES
properties (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  address_line1 TEXT,
  address_line2 TEXT,
  city TEXT,
  state TEXT,
  zip TEXT,
  created_at TIMESTAMPTZ
)

-- UNITS
units (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  property_id UUID REFERENCES properties(id),
  unit_number TEXT,
  bedrooms INT,
  bathrooms NUMERIC,
  monthly_rent NUMERIC,
  status TEXT,                      -- 'vacant' | 'occupied'
  created_at TIMESTAMPTZ
)

-- TENANTS
tenants (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,          -- landlord's tenant_id (naming collision: this is the landlord's RLS scope)
  unit_id UUID REFERENCES units(id),
  full_name TEXT,
  email TEXT,
  phone TEXT,
  lease_start DATE,
  lease_end DATE,
  monthly_rent NUMERIC,
  security_deposit NUMERIC,
  created_at TIMESTAMPTZ
)

-- PAYMENTS
payments (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,          -- landlord's RLS scope
  tenant_profile_id UUID REFERENCES tenants(id),
  unit_id UUID REFERENCES units(id),
  amount NUMERIC,
  stripe_payment_intent_id TEXT,
  fee_charged NUMERIC,              -- actual Stripe fee passed through, stored for transparency
  status TEXT,                      -- 'pending' | 'succeeded' | 'failed'
  due_date DATE,
  paid_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ
)

-- MAINTENANCE REQUESTS
maintenance_requests (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  unit_id UUID REFERENCES units(id),
  submitted_by_name TEXT,           -- from tenant-facing public form, no auth
  description TEXT,
  priority TEXT,                    -- 'low' | 'medium' | 'urgent'
  status TEXT,                      -- 'open' | 'in_progress' | 'resolved'
  resolved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ
)

-- MESSAGES
messages (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  tenant_profile_id UUID REFERENCES tenants(id),
  direction TEXT,                   -- 'landlord_to_tenant' | 'tenant_to_landlord'
  body TEXT,
  created_at TIMESTAMPTZ
)

-- EVENTS (append-only audit + agent instrumentation log)
events (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  event_type TEXT NOT NULL,         -- e.g. 'rent.collected', 'lease.expiring_soon', 'maintenance.opened'
  entity_type TEXT,                 -- 'payment' | 'unit' | 'tenant' | 'maintenance_request'
  entity_id UUID,
  payload JSONB,
  created_at TIMESTAMPTZ
)
```

> **Naming note:** `tenant_id` throughout means the landlord's account ID (RLS scope). Rename `tenants` table to `renter_profiles` in implementation to eliminate confusion. Data model sketch uses `tenants` for readability here.

---

## CLAUDE.md Non-Negotiables

The following rules apply to every file, every migration, every route in this project. Claude Code must re-read these before beginning any new task.

```markdown
# CLAUDE.md — Small Portfolio Hub
# Non-negotiables enforced on every build session.

## 1. TENANT ISOLATION — NO EXCEPTIONS
- Every database table has `tenant_id UUID NOT NULL`.
- Row Level Security is ENABLED on every table.
- Default RLS policy on every table:
    CREATE POLICY "tenant_isolation" ON <table>
    USING (tenant_id = auth.uid());
- No query may be written without a `tenant_id` filter.
- No service-role bypass of RLS except in explicitly marked admin-only
  server functions, which must be code-reviewed before merge.
- Before shipping any feature: manually verify RLS with two test accounts.
  Confirm Account B cannot read Account A's data.

## 2. POST /events ON EVERY AGENT ACTION
- Every state change (payment processed, lease created, maintenance ticket
  opened/closed, reminder sent, tenant added, onboarding step completed)
  MUST fire a POST /events call before the function returns.
- Signature:
    POST /events
    {
      "tenant_id": "<uuid>",
      "event_type": "<domain>.<action>",   // e.g. "rent.collected"
      "entity_type": "<string>",
      "entity_id": "<uuid>",
      "payload": { ...relevant fields }
    }
- Events are append-only. Never update or delete an event row.
- If the events insert fails, log the error but do NOT fail the
  primary transaction. Events are observability, not application logic.

## 3. $4,000 MRR FLOOR — SUBSCRIPTION ENFORCEMENT
- Stripe subscription check runs on every authenticated page load
  (middleware, not per-route).
- subscription_status not in ('trialing', 'active') → redirect to
  /billing with a clear, non-threatening message. No feature access.
- Trial length: 14 days. No credit card required to start trial.
  Card required before trial ends to continue.
- Webhook handlers required for:
    customer.subscription.updated
    customer.subscription.deleted
    invoice.payment_failed
    invoice.payment_succeeded
- All Stripe webhook endpoints must validate the Stripe-Signature header.
  Reject unsigned requests with 400.
- MRR target: $4,000/month minimum before considering the product
  viable. Pricing and conversion flows must be designed with this
  in mind. Do not reduce the price below $49/month without a recorded
  decision.

## 4. TRANSPARENT FEE DISPLAY
- ACH/EFT transaction fees are NEVER hidden.
- Every payment setup screen must show the per-transaction fee
  in plain text before the landlord confirms.
- Fee must appear on payment confirmation emails to landlord.
- No markup above Stripe's actual cost may be added without
  updating this document and the public pricing page simultaneously.

## 5. LIVE CHAT FOR ALL SUBSCRIBERS
- Live chat (Intercom or Crisp) is initialized for every authenticated
  user with an active or trialing subscription.
- It must NEVER be conditionally hidden based on subscription tier
  (there is only one tier; this is the competitive differentiator).
- Chat widget identity: pre-fill user email and account ID on boot.

## 6. NO INVENTED METRICS
- Do not generate, display, or store benchmark data, market averages,
  or comparison figures that are not sourced from real user data.
- "Industry average" banners, fake social proof, or fabricated
  occupancy benchmarks are prohibited.
- Dashboard figures must reflect only actual data for the landlord's
  own portfolio.

## 7. STACK DEFAULTS
- Database: Postgres via Supabase (RLS native)
- Auth: Supabase Auth
- Payments: Stripe (ACH via Stripe + Stripe Billing for subscriptions)
- Frontend: Next.js 14+ App Router, TypeScript strict mode
- Styling: Tailwind CSS
  - Primary accent: #2563eb
  - Secondary accent: #f97316
  - Mood: professional/warm (reference: AppFolio, Buildium, Rentec Direct)
- File storage: Supabase Storage (lease PDFs)
- Live chat: Crisp (preferred) or Intercom
- Hosting: Vercel

## 8. ONBOARDING COMPLETION GATE
- Onboarding wizard progress is stored in `accounts` table as
  a JSONB `onboarding_steps` field.
- Steps: ['property_added', 'unit_added', 'tenant_added',
          'rent_collection_enabled', 'maintenance_link_shared']
- Progress bar visible on dashboard until all 5 steps complete.
- Each step completion fires a POST /events with event_type
  'onboarding.step_completed' and payload.step = <step_name>.

## 9. CODE QUALITY GATES
- TypeScript: `strict: true`, no `any` without a comment explaining why.
- Every server action and API route validates input with Zod before
  touching the database.
- No raw SQL string interpolation. Use parameterized queries or the
  Supabase client exclusively.
- Every migration is reversible (has a down migration).

## 10. WHAT NOT TO BUILD AT MVP
- Tenant login portal (maintenance form is public/unauthenticated link)
- Listing syndication (vacancy alert is a prompt only, no API calls)
- Accounting integrations (QuickBooks, etc.)
- Mobile native app
- Multi-user / team accounts (one landlord = one account)
- Automated late fee charging (landlord manually confirms before applying)
```

---

## Design System Reference

| Token | Value |
|---|---|
| Primary accent | `#2563eb` |
| Secondary accent | `#f97316` |
| Mood | Professional / warm |
| Benchmark brands | AppFolio, Buildium, Rentec Direct |
| Vertical | Real Estate / Property Management |

---

## Open Questions for Founder Before Build

The research report did not provide the following. Resolve before Phase 3 begins:

- [ ] **Exact Stripe ACH fee passthrough amount** — what fee will be shown to landlords? At-cost (0.8%, capped $5 per Stripe pricing) or a flat per-transaction amount?
- [ ] **Trial conversion flow** — card-upfront at trial start or card-required before day 14?