# BUILD_BRIEF_CLAUDE_CODE.md

# Series Scheduler Pro

## One-Paragraph Pitch

Series Scheduler Pro is a Calendly add-on that gives therapists, coaches, personal trainers, tutors, consultants, and wellness providers the one feature Calendly explicitly does not offer: recurring appointment series booking. A client clicks once, selects a cadence (weekly, biweekly, monthly), picks a start time, and every session in the series lands on both calendars automatically — no re-booking friction, no dropped clients mid-program. Calendly's own community manager confirmed as of May 2026 that recurring meetings remain unsupported with no announced timeline, leaving a verified gap that Acuity Scheduling currently exploits. Series Scheduler Pro fills that gap as a lightweight overlay, preserving the Calendly UX professionals already know while adding the retention-driving series mechanic their practices depend on.

---

## Target Customer

**Primary:** Independent service professionals already paying for Calendly (Calendly Standard/Teams tier) who run multi-session engagements — therapy series, coaching packages, training blocks, tutoring semesters, wellness programs.

**Buying signal:** They are manually re-sending Calendly links after every session, or they have already complained in the Calendly community forum about the missing recurring feature.

**Anti-customer:** One-time booking businesses (haircuts, single consultations, event speakers) who have no recurring engagement model.

---

## Conservative MRR Potential

**$12,480/month** (as stated in research report). No additional market-size metrics were provided in the source data; none are invented here.

**$4,000 MRR floor** is the non-negotiable launch gate before any feature expansion is approved (see CLAUDE.md §7).

---

## Tier Structure

> ⚠️ No tier structure was included in the research report input. The following is the minimum viable commercial structure required to reach the $4K MRR floor and must be validated with pricing discovery before GA launch.

| Tier | Placeholder Price | Limit |
|---|---|---|
| Solo | TBD | 1 Calendly user, up to X active series |
| Practice | TBD | Up to 5 users, unlimited series |
| Clinic | TBD | 5+ users, white-label booking page |

---

## Core Feature List (Build Order)

Build in strict sequence. Do not begin a feature until all tests for the prior feature pass and the `/events` POST is wired.

### Phase 0 — Foundation (must complete before any user-facing work)

**0.1 — Repo & Environment Scaffold**
- Next.js 14 (App Router) + TypeScript
- Supabase project: enforce RLS from migration `0001`
- Stripe account connected (test mode)
- Vercel project linked
- `.env.example` committed with all required keys documented

**0.2 — CLAUDE.md Non-Negotiables Hardened**
- `tenant_id` column present in every table migration before merge
- RLS policies written and tested in the same migration file
- `/api/events` POST endpoint live and accepting structured payloads
- Stripe webhook handler skeleton deployed

**0.3 — Calendly OAuth Integration**
- OAuth 2.0 flow against Calendly v2 API
- Store `access_token`, `refresh_token`, `calendly_user_uri`, `calendly_org_uri` per tenant
- Token refresh middleware
- Fetch and cache user's existing event types

---

### Phase 1 — Core Booking Engine

**1.1 — Series Definition UI**
- "Create a Series" flow: select base Calendly event type, set cadence (weekly / biweekly / monthly / custom interval), set series length (number of sessions OR end date), set buffer days before first session
- Series saved to `series_templates` table with `tenant_id`
- POST `/events` on every series creation action

**1.2 — Series Booking Link Generator**
- Generate a shareable `series.seriesschedulerpro.com/s/{slug}` booking page per series template
- Slug is human-readable + UUID suffix
- Page renders: practitioner name, session count, cadence, duration, next available start date

**1.3 — Client-Facing Booking Flow**
- Client selects a start time slot (pulled live from Calendly availability API for that event type)
- System calculates all subsequent session datetimes based on cadence
- Show client a full series preview ("Your 8 sessions: Oct 6, Oct 13, Oct 20…") before confirmation
- Client submits name + email

**1.4 — Calendly Invite Cascade**
- On client confirmation: loop through calculated session datetimes, POST one Calendly scheduling event per session via Calendly API (or use `one_off_event_types` if scheduled invites endpoint is available; document fallback)
- Collect all returned `calendly_event_uris` and store in `series_bookings` table
- Send confirmation email to client listing all sessions (use Resend or Postmark)
- POST `/events` for each invite dispatched

**1.5 — Practitioner Dashboard (MVP)**
- List all active series templates
- List all booked series with client name, session count, next session date, completion percentage
- Basic cancel-entire-series action (cancels all remaining Calendly invites via API)
- POST `/events` on every dashboard action

---

### Phase 2 — Billing & Access Control

**2.1 — Stripe Subscription Flow**
- Checkout Session creation per tier
- Webhook handler: `checkout.session.completed` → provision tenant, set `plan_tier`
- Webhook handler: `customer.subscription.deleted` → downgrade tenant, block new series creation (do not delete existing data)
- Customer portal link in dashboard

**2.2 — Entitlement Guards**
- Middleware checks `plan_tier` before series creation
- Enforce series/user limits per tier
- Upgrade prompt modal with Stripe Checkout deep-link

---

### Phase 3 — Retention Loops (see dedicated section below)

**3.1** — Pre-session reminder engine
**3.2** — Series completion + re-book prompt
**3.3** — Practitioner weekly digest
**3.4** — Incomplete series rescue (client missed booking)
**3.5** — Rescheduling within series
**3.6** — Client portal (series history + self-rebook)

---

### Phase 4 — Polish & Growth

**4.1** — Calendly webhook ingestion (listen for cancellations/reschedules from Calendly side, sync state)
**4.2** — Intake form attachment per series template
**4.3** — Multi-user / team support (Practice/Clinic tiers)
**4.4** — Series template library (shareable templates between team members)
**4.5** — Analytics dashboard: completion rates, cancellation rates, rebooking rates

---

## The 6 Required Retention Loops

> These are wired in Phase 3. Each loop must POST to `/api/events` when triggered.

### Loop 1 — Pre-Session Reminder Sequence
**Trigger:** 48h and 2h before each scheduled session in a series
**Action:** Email (and optionally SMS) to client with session details, practitioner name, and a one-click reschedule link scoped to that single session
**Why it retains:** Reduces no-shows, which are the #1 cause of practitioners abandoning any scheduling tool mid-series

### Loop 2 — Series Completion + Re-Enrollment Prompt
**Trigger:** 24h after the final session in a series is completed (Calendly webhook confirms attendance or session datetime passes)
**Action:** Email to client: "Your [Program Name] series is complete. Ready to book your next block?" with one-click re-enrollment link pre-filled with same cadence
**Why it retains:** Converts single-series users into perpetual re-subscribers; keeps both practitioner and client active in the product

### Loop 3 — Practitioner Weekly Digest
**Trigger:** Every Monday 8am in practitioner's timezone (cron job)
**Action:** Email to practitioner: upcoming sessions this week, series completion rates, clients with expiring series (within 2 sessions of end), clients who have not yet confirmed a follow-on series
**Why it retains:** Makes Series Scheduler Pro the operating heartbeat of their practice; creates daily-driver habit for a product that could otherwise feel passive

### Loop 4 — Abandoned Series Rescue
**Trigger:** Series template created but no booking link shared within 72h, OR booking link opened but client did not complete booking within 48h
**Action:** Email to practitioner: "Your [Series Name] hasn't been booked yet — share this link with [client name if known]." Includes one-click copy of the booking link
**Why it retains:** Prevents silent churn where practitioners create a series, forget to send it, see no value, and cancel subscription

### Loop 5 — Mid-Series Rescheduling Flow
**Trigger:** Client or practitioner cancels a single session within an active series (received via Calendly webhook)
**Action:** Immediately surface a rescheduling UI that proposes alternative time slots and offers to shift the entire remaining series by one week if needed; notify both parties
**Why it retains:** The #1 reason recurring-series workflows fail is that a single cancellation breaks the whole chain. Solving this in-product eliminates the "it's too complicated" cancellation reason

### Loop 6 — Client Portal + Series History
**Trigger:** Passive (always-on client-facing page at `/portal/{client_token}`)
**Action:** Client can view all past and upcoming sessions across all their series with this practitioner, see session notes (if practitioner adds them), and self-initiate a new series booking
**Why it retains:** Creates perceived switching cost — clients have history, context, and continuity stored here. Practitioners cannot easily migrate this relationship context to a competitor

---

## Data Model Sketch

> All tables require `tenant_id UUID NOT NULL` and RLS policies restricting access to `auth.uid()` → `tenants.owner_id` before any other column is defined.

```sql
-- Core tenant / auth
tenants
  id UUID PK
  owner_id UUID FK → auth.users
  plan_tier TEXT  -- 'solo' | 'practice' | 'clinic'
  stripe_customer_id TEXT
  stripe_subscription_id TEXT
  created_at TIMESTAMPTZ

-- Calendly OAuth tokens (one per user within a tenant for multi-user tiers)
calendly_connections
  id UUID PK
  tenant_id UUID FK → tenants   ← RLS
  user_id UUID FK → auth.users
  calendly_user_uri TEXT
  calendly_org_uri TEXT
  access_token TEXT  -- encrypted at rest
  refresh_token TEXT -- encrypted at rest
  expires_at TIMESTAMPTZ
  created_at TIMESTAMPTZ

-- Series templates (the recurring pattern definition)
series_templates
  id UUID PK
  tenant_id UUID FK → tenants   ← RLS
  name TEXT
  calendly_event_type_uri TEXT
  cadence TEXT  -- 'weekly' | 'biweekly' | 'monthly' | 'custom'
  custom_interval_days INT  -- nullable, used when cadence='custom'
  session_count INT
  end_date DATE  -- nullable, alternative to session_count
  booking_slug TEXT UNIQUE
  is_active BOOLEAN
  created_at TIMESTAMPTZ

-- Booked series (one row per client-series engagement)
series_bookings
  id UUID PK
  tenant_id UUID FK → tenants   ← RLS
  series_template_id UUID FK → series_templates
  client_name TEXT
  client_email TEXT
  client_portal_token UUID  -- for passwordless client portal access
  status TEXT  -- 'active' | 'completed' | 'cancelled'
  created_at TIMESTAMPTZ

-- Individual sessions within a booked series
series_sessions
  id UUID PK
  tenant_id UUID FK → tenants   ← RLS
  series_booking_id UUID FK → series_bookings
  session_number INT
  scheduled_at TIMESTAMPTZ
  calendly_event_uri TEXT  -- returned by Calendly after invite creation
  calendly_invitee_uri TEXT
  status TEXT  -- 'scheduled' | 'completed' | 'cancelled' | 'rescheduled'
  rescheduled_to TIMESTAMPTZ  -- nullable
  created_at TIMESTAMPTZ

-- Event log (append-only, never delete)
events
  id UUID PK
  tenant_id UUID FK → tenants   ← RLS
  actor_type TEXT  -- 'practitioner' | 'client' | 'system' | 'webhook'
  actor_id TEXT    -- user UUID or 'system'
  event_type TEXT  -- e.g. 'series.created', 'session.booked', 'reminder.sent'
  entity_type TEXT -- 'series_template' | 'series_booking' | 'series_session'
  entity_id UUID
  payload JSONB
  created_at TIMESTAMPTZ
```

**Indexes required (minimum):**
- `series_templates(tenant_id, is_active)`
- `series_bookings(tenant_id, status)`
- `series_sessions(tenant_id, scheduled_at)` — for reminder cron queries
- `series_sessions(series_booking_id, session_number)`
- `events(tenant_id, created_at DESC)`

---

## CLAUDE.md Non-Negotiables

> These rules are absolute constraints. Claude Code must refuse to generate or merge code that violates any item in this section.

```markdown
# CLAUDE.md — Series Scheduler Pro

## §1 — Tenant Isolation (HARD BLOCK)

Every database table without exception must have:
  - `tenant_id UUID NOT NULL` column
  - A Supabase RLS policy that restricts SELECT/INSERT/UPDATE/DELETE
    to rows where tenant_id matches the authenticated user's tenant

If a migration file is proposed that creates a table WITHOUT tenant_id
and WITHOUT RLS policies in the same file, reject it and do not proceed.

No exceptions. Lookup/reference tables (e.g. cadence_types) are exempt
only if they contain zero user data and are read-only to all tenants.

## §2 — Event Emission (HARD BLOCK)

Every agent action, user action, webhook receipt, cron job execution,
and background task must POST to /api/events before returning a response.

The /api/events endpoint signature:
  POST /api/events
  Body: {
    tenant_id: string,
    actor_type: 'practitioner' | 'client' | 'system' | 'webhook',
    actor_id: string,
    event_type: string,   // dot-notation: 'series.created', 'session.booked'
    entity_type: string,
    entity_id: string,
    payload: object
  }

If a function performs a meaningful state change and does NOT call
POST /api/events, it is incomplete. Do not mark it done.

## §3 — $4,000 MRR Floor Gate

No feature work beyond Phase 2 (Billing) may be prioritized or
announced until Stripe MRR dashboard confirms ≥ $4,000 active MRR.

The $4K gate is not a suggestion. It is the signal that the core
value proposition is validated and retention loops are worth building.

Do not build Phase 4 features before Phase 3 retention loops are live.

## §4 — Calendly API Respect

- Always honor Calendly rate limits; implement exponential backoff
- Never store client PII beyond what Calendly already holds; sync
  only what is needed for Series Scheduler Pro's own session records
- If Calendly's API changes and a feature becomes impossible,
  surface this as a blocker immediately — do not silently degrade

## §5 — Encryption at Rest for OAuth Tokens

calendly_connections.access_token and .refresh_token must be
encrypted before INSERT and decrypted on SELECT.
Do not store raw OAuth tokens in plaintext in the database.
Use AES-256-GCM with a key stored in environment variables (not in DB).

## §6 — No Invented Metrics in User-Facing Copy

Marketing copy, onboarding screens, and email templates must not
cite specific market size numbers, user count claims, or revenue
figures unless they appear in the validated research report.
The only confirmed external data point is:
  "Calendly community manager confirmed recurring meetings
   unsupported as of May 2026 with no announced timeline."
This specific statement may be referenced. Nothing else may be
fabricated as a social proof or market validation claim.

## §7 — Stripe Webhook Idempotency

All Stripe webhook handlers must:
  - Check for duplicate event_id before processing
  - Return 200 on duplicate (do not reprocess)
  - Store processed stripe_event_ids in a dedupe table