# BUILD_BRIEF_CLAUDE_CODE.md
# Ninety Nine Comply

---

## Product Pitch

Ninety Nine Comply is a contractor payment compliance SaaS for small businesses running contractor-heavy teams — agencies, construction firms, staffing companies — that eliminates the 3–5 hours per week owners and office managers waste manually chasing W-9s, eyeballing $600 thresholds, and scrambling through year-end 1099-NEC prep. The product automatically collects W-9s through a branded self-serve portal, syncs contractor payment data from QuickBooks Online or CSV import, fires real-time threshold alerts as contractors approach the IRS $600 filing trigger, and generates ready-to-file 1099-NEC PDFs and IRS FIRE e-file data at year-end — turning a historically painful 5-hour annual ordeal into a 10-minute batch export.

---

## Target Customer

**Primary:** Small business owners and office managers at US-based businesses with 3–50 active contractors and fewer than 50 employees. Highest-density verticals: creative/marketing agencies, construction subcontractors, staffing firms, and consulting practices.

**Secondary (expansion):** Bookkeepers and accountants managing multiple small-business clients (addressed by Scale tier's multi-client dashboard).

**Pricing anchor:** $39/mo referenced in research for conservative modeling; actual tiers are $99 / $199 / $349/mo (see Tier Structure).

**Conservative MRR target:** $5,400/mo at 150 paying accounts (after 10% churn/trial-conversion haircut applied by research). $4,000/mo MRR floor must be maintained at all times.

---

## Tier Structure

| Tier | Price | Key Unlock |
|---|---|---|
| **Starter** | $99/mo | ≤25 contractors, W-9 collection portal, threshold monitoring, 1099-NEC PDF, QuickBooks or CSV import |
| **Growth** | $199/mo | >25 contractors OR IRS FIRE e-file submission OR Plaid bank sync |
| **Scale** | $349/mo | Multi-entity (multiple EINs) OR bulk 1099 mailing service OR bookkeeper multi-client dashboard |

---

## Core Feature List (Build Order)

Build in this sequence. Each phase must be fully working and tested before the next begins.

### Phase 0 — Foundation & Auth
1. **Multi-tenant database scaffold** — every table gets `tenant_id` + Postgres Row-Level Security (RLS) policies enforced at DB layer. No exceptions. See CLAUDE.md Non-Negotiables.
2. **Auth system** — email/password + magic link. Role model: `owner`, `admin`, `bookkeeper` (read-only cross-tenant for Scale tier).
3. **Tenant onboarding flow** — collect business name, EIN, mailing address, fiscal year, tier selection, Stripe subscription activation.
4. **POST /events bus** — internal event emission on every agent/automated action. Schema: `{tenant_id, event_type, actor, payload, timestamp}`. Required before any automation is built.

### Phase 1 — Contractor Registry
5. **Contractor CRUD** — name, email, phone, address, contractor type (individual / business), payment category, status (active / inactive / archived).
6. **CSV import** — bulk contractor upload with validation errors surfaced inline.
7. **QuickBooks Online OAuth integration** — pull vendor list as contractor seed data. Mark sync source. Handle token refresh.
8. **Contractor list view** — sortable/filterable table with W-9 status badge, cumulative YTD payment amount, threshold proximity indicator (color-coded: gray < $400, yellow $400–$550, red > $550).

### Phase 2 — W-9 Collection Portal
9. **W-9 request engine** — owner triggers W-9 request per contractor or in bulk. System sends branded email with unique tokenized link (no login required for contractor).
10. **Contractor-facing W-9 form** — collects: legal name, business name (if applicable), federal tax classification, address, TIN (SSN or EIN), signature (typed + timestamp). Branded with tenant logo/colors.
11. **W-9 PDF generation** — render submitted data onto IRS Form W-9 template. Store as tenant-scoped document. Emit `w9.collected` event.
12. **W-9 status tracking** — `not_requested` → `requested` → `reminded` → `collected` → `expired` (annual re-verification prompt). Track request timestamps, reminder count.
13. **Automated reminder sequence** — Day 3 and Day 7 follow-up emails if W-9 not submitted. Configurable. Emit `w9.reminder_sent` event each time.
14. **W-9 completion webhook/confirmation** — contractor sees branded thank-you screen on submission. Owner gets in-app notification + email.

### Phase 3 — Payment Tracking & Threshold Engine
15. **Manual payment entry** — owner logs payment: contractor, date, amount, payment method, memo. Cumulative YTD total auto-calculated.
16. **QuickBooks payment sync** — pull bill payments and checks linked to contractors. De-duplicate on transaction ID. Emit `payment.synced` event per batch.
17. **CSV payment import** — bulk upload payment history with column mapping UI.
18. **Threshold monitoring engine** — background job runs on every payment write. Calculates cumulative YTD total per contractor per tenant. Compares against $600 IRS threshold.
19. **Threshold alert system** — alert fires at $550 cumulative (configurable warning threshold). Channels: in-app notification + email. Alert payload includes: contractor name, YTD total, remaining headroom. Emit `threshold.alert_sent` event.
20. **Payments ledger view** — per-contractor payment history table with running total. Exportable as CSV.

### Phase 4 — Year-End 1099-NEC Generation
21. **1099-NEC PDF generator** — populate IRS Form 1099-NEC template from W-9 data + cumulative payments for all contractors ≥ $600 YTD. Batch generation for all eligible contractors in one click.
22. **Batch review UI** — owner reviews each generated 1099-NEC before finalizing. Edit fields inline. Flag/exclude individual contractors.
23. **Payer copy + recipient copy output** — generate both copies. Bundle into downloadable ZIP per tenant.
24. **IRS FIRE e-file data export** (Growth+) — generate IRS FIRE-format `.txt` file per Publication 1220 spec. Emit `efile.generated` event.
25. **Bulk 1099 mailing service** (Scale tier) — third-party print/mail API integration (e.g., Lob.com) to physically mail recipient copies. Track mailing status per contractor. Emit `mail.dispatched` event.
26. **Year-end summary report** — PDF summary: total contractors paid, total 1099s filed, total payments, filing deadline status.

### Phase 5 — Dashboard & Retention Surface
27. **Owner dashboard** — hero metric: "W-9 coverage rate" (contractors with current W-9 / total active contractors, as percentage). Secondary metrics: contractors approaching threshold, total YTD payments, 1099s ready to generate.
28. **Weekly digest email** — every Monday: W-9 coverage %, new threshold alerts, payment sync status. Opt-out available. Emit `digest.sent` event.
29. **Compliance health score** — simple 0–100 score based on: W-9 coverage %, threshold alerts actioned, payment data freshness. Displayed on dashboard.
30. **In-app upsell prompts** — context-triggered: contractor #26 added → Growth upsell. Second EIN onboarded → Scale upsell. FIRE e-file requested on Starter → Growth upsell.

### Phase 6 — Settings, Admin & Billing
31. **Stripe billing integration** — subscription management, tier upgrades/downgrades, invoices. Webhook handlers for payment failures → grace period → account suspension flow.
32. **Team member invites** — owner invites additional users with role assignment.
33. **Audit log** — immutable log of all actions (sourced from POST /events bus) viewable by owner. Filterable by date, actor, event type.
34. **Data export** — full tenant data export (contractors, payments, W-9s, 1099s) as ZIP. GDPR/CCPA compliance.
35. **Account deletion** — hard delete with 30-day recovery window. Contractor W-9 data retained per IRS record-keeping guidance (4 years), flagged separately.

---

## Six Required Retention Loops

These are mandatory product mechanics, not marketing tactics. Build each as a named system with its own event emissions.

### Loop 1 — Immediate Value Loop (Week 1)
**Trigger:** Owner adds first contractor and sends W-9 request.
**Mechanic:** System handles all follow-up automatically. When contractor submits W-9 without owner sending a single manual email, surface a celebratory in-app moment: "Your first W-9 was collected automatically — you didn't lift a finger." Emit `milestone.first_w9_auto_collected`.
**Retention function:** Proves time savings in the first week before any skepticism sets in. Counters Day 30–60 churn risk window (see below).

### Loop 2 — Threshold Alert Loop (Month 2+)
**Trigger:** Contractor cumulative payments cross $550.
**Mechanic:** Alert fires via email + in-app with a clear CTA: "Review this contractor's payment history." If no W-9 on file, alert also prompts W-9 collection. Emit `threshold.alert_sent`.
**Retention function:** Delivers concrete compliance value — the core reason the owner paid. Each alert is a product "working for them" moment. Demonstrates ROI before year-end.

### Loop 3 — Weekly Coverage Score Loop (Ongoing)
**Trigger:** Every Monday at 8am tenant-local time.
**Mechanic:** Weekly digest email + dashboard widget showing W-9 coverage % for the week. If coverage dropped (new contractor added without W-9), highlight the gap with one-click "Send W-9 request" CTA. Emit `digest.sent`.
**Retention metric:** "Contractors with up-to-date W-9 on file as % of total active contractors" (this is the designated weekly value metric from research).
**Retention function:** Creates a weekly habit loop. Owner checks coverage every Monday. Score improving = product working. Score declining = action needed (re-engagement trigger).

### Loop 4 — Year-End Payoff Loop (Month 12)
**Trigger:** January 1 of filing year. System identifies all contractors ≥ $600 YTD and surfaces "Your 1099s are ready to generate."
**Mechanic:** One-click batch generation. Clock the time from "Generate" click to ZIP download. Surface: "You just completed your 1099 filing in [X] minutes. Last year without Ninety Nine Comply, this took you hours." Emit `milestone.year_end_batch_generated`.
**Retention function:** The retention-cementing annual moment. Concrete before/after proof point at renewal decision time. Research identifies this as the primary retention milestone.

### Loop 5 — Integration Stickiness Loop (QuickBooks Sync)
**Trigger:** QuickBooks OAuth connected.
**Mechanic:** System syncs contractor payments silently in background. Dashboard shows "Last synced: 2 hours ago." If sync fails or token expires, immediate alert with one-click re-auth. Emit `integration.sync_completed` and `integration.sync_failed`. Research identifies QuickBooks sync reliability as the primary Day 30–60 churn risk — treat sync failures as P0 incidents.
**Retention function:** Once QuickBooks is connected, Ninety Nine Comply becomes a compliance layer on top of the owner's existing bookkeeping. Disconnecting means manually recreating payment data. High switching cost.

### Loop 6 — Expansion Pain Loop (Contractor Compliance Hub)
**Trigger:** Owner has ≥10 contractors with W-9s on file + 90 days of usage.
**Mechanic:** Surface in-app prompt: "You're managing contractor payments. Do you also track contractor agreements and certificates of insurance?" Waitlist CTA for COI Tracking (adjacent pain identified in research). Emit `expansion.coi_interest_captured`.
**Retention function:** Signals product roadmap investment. Owner who joins COI waitlist is signaling they see Ninety Nine Comply as their contractor compliance hub — not a point solution. Reduces churn by anchoring future value. Also serves as validated demand signal for next feature build.

---

## Data Model Sketch

> This is a logical model. Implement in Postgres. Every table **must** include `tenant_id UUID NOT NULL` and RLS policy. See CLAUDE.md Non-Negotiables.

```
tenants
  id UUID PK
  name TEXT
  ein TEXT (encrypted at rest)
  address JSONB
  fiscal_year_end DATE
  tier ENUM('starter','growth','scale')
  stripe_customer_id TEXT
  stripe_subscription_id TEXT
  logo_url TEXT
  created_at TIMESTAMPTZ
  updated_at TIMESTAMPTZ

users
  id UUID PK
  tenant_id UUID FK → tenants (RLS)
  email TEXT UNIQUE
  role ENUM('owner','admin','bookkeeper')
  invited_by UUID FK → users NULLABLE
  created_at TIMESTAMPTZ

contractors
  id UUID PK
  tenant_id UUID FK → tenants (RLS)
  legal_name TEXT
  business_name TEXT NULLABLE
  email TEXT
  phone TEXT NULLABLE
  address JSONB
  tax_classification ENUM('individual','sole_prop','c_corp','s_corp','partnership','llc','other')
  tin TEXT (encrypted at rest)  -- SSN or EIN
  tin_type ENUM('ssn','ein')
  status ENUM('active','inactive','archived')
  source ENUM('manual','csv','quickbooks')
  quickbooks_vendor_id TEXT NULLABLE
  created_at TIMESTAMPTZ
  updated_at TIMESTAMPTZ

w9_requests
  id UUID PK
  tenant_id UUID FK → tenants (RLS)
  contractor_id UUID FK → contractors
  status ENUM('not_requested','requested','reminded','collected','expired')
  token TEXT UNIQUE  -- tokenized URL for contractor portal
  token_expires_at TIMESTAMPTZ
  reminder_count INT DEFAULT 0
  last_reminded_at TIMESTAMPTZ NULLABLE
  collected_at TIMESTAMPTZ NULLABLE
  pdf_url TEXT NULLABLE  -- stored W-9 PDF
  raw_submission JSONB NULLABLE  -- form field snapshot
  created_at TIMESTAMPTZ

payments
  id UUID PK
  tenant_id UUID FK → tenants (RLS)
  contractor_id UUID FK → contractors
  amount_cents INT  -- store in cents, never floats
  payment_date DATE
  payment_method TEXT NULLABLE
  memo TEXT NULLABLE
  source ENUM('manual','csv','quickbooks','plaid')
  external_id TEXT NULLABLE  -- QuickBooks/Plaid transaction ID for dedup
  fiscal_year INT  -- e.g. 2024
  created_at TIMESTAMPTZ

threshold_alerts
  id UUID PK
  tenant_id UUID FK → tenants (RLS)
  contractor_id UUID FK → contractors
  fiscal_year INT
  cumulative_amount_cents INT  -- at time of alert
  threshold_cents INT DEFAULT 55000  -- $550.00 warning
  alert_sent_at TIMESTAMPTZ
  actioned_at TIMESTAMPTZ NULLABLE  -- owner acknowledged
  created_at TIMESTAMPTZ

form_1099_nec
  id UUID PK
  tenant_id UUID FK → tenants (RLS)
  contractor_id UUID FK → contractors
  fiscal_year INT
  box1_nonemployee_compensation_cents INT
  payer_snapshot JSONB  -- tenant EIN, name, address at generation time
  recipient_snapshot JSONB  -- contractor data at generation time
  status ENUM('draft','finalized','mailed','efiled')
  pdf_url TEXT NULLABLE
  efile_included BOOLEAN DEFAULT FALSE
  mailing_status TEXT NULLABLE  -- Lob.com status
  generated_at TIMESTAMPTZ
  finalized_at TIMESTAMPTZ NULLABLE
  created_at TIMESTAMPTZ

events
  id UUID PK
  tenant_id UUID FK → tenants (RLS)
  event_type TEXT  -- e.g. 'w9.collected', 'threshold.alert_sent'
  actor TEXT  -- user_id or 'system'
  payload JSONB
  created_at TIMESTAMPTZ
  -- append-only, no updates, no deletes

integrations
  id UUID PK
  tenant_id UUID FK → tenants (RLS)