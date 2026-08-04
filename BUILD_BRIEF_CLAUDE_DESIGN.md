# BUILD_BRIEF_CLAUDE_DESIGN.md

## Showing Signal — Product Design Brief

**Document Type:** Micro SaaS Engine Build Brief
**Prepared By:** THD Agentic Systems
**Vertical:** Real Estate — Buyer's Agents at Independent Teams
**Conservative MRR Potential:** $6,586

---

## 1. VISUAL PERSONALITY STATEMENT

Showing Signal lives at the intersection of **real estate hustle and automation precision** — it is the quiet engine running behind every agent's day, turning ShowingTime's scheduling data into warm leads, timely nudges, and closed deals.

The personality is: **Reliable Infrastructure with Momentum**. Not flashy PropTech bravado. Not sterile SaaS utility. Showing Signal should feel like the most competent agent on the team — one who never forgets a follow-up, moves fast, and earns trust through consistent action rather than loud promises.

**Three brand adjectives:** Responsive · Signal-Clear · Agent-First

The visual language borrows from **air traffic control and broadcast signal** metaphors — precision-timed events flowing through clean pipelines — while maintaining the warm approachability that independent agents expect from tools built *for* them, not sold down to them from enterprise platforms.

---

## 2. PALETTE APPLICATION

> **Rule:** No new brand colors are invented. The palette below is applied as received. All usage guidance derives strictly from the provided `industry_palette` values.

| Token | Hex | Role |
|---|---|---|
| `--color-primary` | `#5a96ff` | Primary accent — signal blue |
| `--color-secondary` | `#f5a623` | Secondary accent — alert amber |
| `--color-neutral-base` | System white `#ffffff` | Page ground / card surfaces |
| `--color-neutral-ink` | System near-black `#111827` | Body copy, headings |
| `--color-neutral-mid` | System gray `#6b7280` | Supporting copy, meta text |
| `--color-neutral-subtle` | System light gray `#f3f4f6` | Section banding, input backgrounds |

### Primary Accent — `#5a96ff` (Signal Blue)

- **Hero CTA buttons** — filled, full-width on mobile
- **Active pipeline step indicators** — the "pulse" animation on the automation flow diagram
- **Inline hyperlinks** — no underline at rest, underline on hover
- **Feature icon fill** — 60% opacity tint behind outlined icons
- **Progress bars** — showing current automation step completion
- **Navigation active state** — left-border indicator on sidebar items

### Secondary Accent — `#f5a623` (Alert Amber)

- **Alert/notification badges** — "3 showings need follow-up today" counter chips
- **Pricing tier highlight ring** — border on the recommended plan card
- **Inline callout blocks** — "Why this matters" sidebar quotes on the landing page
- **Hover state on secondary CTAs** — border color transition from neutral to amber
- **Micro-animation accent** — the final node in the automation flow diagram pulses amber to signal "triggered"
- **Warning/urgency states** — "Showing feedback not yet received" status tags

### Mood: Neutral/Adaptable

Because no benchmark brands were specified and the mood is neutral/adaptable, apply a **light-mode-first** design with generous white space. The palette functions as **signal against white field** — blue and amber are never used together in large adjacent blocks (avoid traffic-light visual conflict). Amber is always the exception, never the foundation.

Background section alternation: `#ffffff` → `#f3f4f6` → `#ffffff` throughout the landing page. Primary and secondary accents appear only as interactive elements, highlights, or iconographic accents — never as full-bleed background fills.

---

## 3. TYPOGRAPHY SYSTEM

> **Rule:** Only Space Grotesk, IBM Plex Sans, and JetBrains Mono are permitted per the base design system. Inter, Roboto, and Arial are explicitly excluded.

### Type Scale

| Role | Typeface | Weight | Size (Desktop) | Size (Mobile) |
|---|---|---|---|---|
| Display / Hero H1 | **Space Grotesk** | 700 Bold | 56px / 3.5rem | 36px / 2.25rem |
| Section H2 | **Space Grotesk** | 600 SemiBold | 40px / 2.5rem | 28px / 1.75rem |
| Card H3 | **Space Grotesk** | 600 SemiBold | 24px / 1.5rem | 20px / 1.25rem |
| Body — Primary | **IBM Plex Sans** | 400 Regular | 17px / 1.0625rem | 16px / 1rem |
| Body — Supporting | **IBM Plex Sans** | 400 Regular | 15px / 0.9375rem | 14px / 0.875rem |
| UI Labels / Buttons | **IBM Plex Sans** | 600 SemiBold | 14px / 0.875rem | 14px / 0.875rem |
| Code / Data / Payloads | **JetBrains Mono** | 400 Regular | 13px / 0.8125rem | 12px / 0.75rem |
| Caption / Meta | **IBM Plex Sans** | 400 Regular | 12px / 0.75rem | 12px / 0.75rem |

### Typography Rationale

**Space Grotesk** carries all hero and section headings — its slightly geometric, confident letterforms read as "modern infrastructure" without the over-used sterility of Inter. The subtle quirks in letters like `a`, `g`, and `R` give Showing Signal a personality fingerprint.

**IBM Plex Sans** handles all body and UI text. Its dual nature — technical legibility plus editorial warmth — mirrors the product itself: automation precision used by human agents who read it on their phones between showings.

**JetBrains Mono** appears specifically for:
- The "automation trigger" code/payload preview in the product demo section
- Webhook endpoint display in docs/onboarding
- Any live data display (showing timestamps, CRM field names, SMS preview strings)

This use of mono type reinforces the "middleware" technical credibility without making the product feel inaccessible.

### Line Height & Spacing Defaults

```css
--lh-display: 1.1;      /* Space Grotesk hero */
--lh-heading: 1.25;     /* Space Grotesk H2/H3 */
--lh-body: 1.65;        /* IBM Plex Sans body */
--lh-mono: 1.5;         /* JetBrains Mono */
--tracking-display: -0.02em;
--tracking-body: 0em;
--tracking-mono: 0em;
```

---

## 4. LANDING PAGE STRUCTURE

### SECTION 1 — Hero (Above the Fold)

**SXO Priority Zone — All CTA and primary keyword targets must appear here.**

**Layout:** Single-column centered on mobile / Two-column (copy left, product preview right) on desktop ≥ 1024px

**H1 (Space Grotesk 700):**
> "Every Showing You Schedule Deserves a Follow-Up That Actually Fires."

**Subheadline (IBM Plex Sans 400, 18px, `--color-neutral-mid`):**
> Showing Signal connects ShowingTime to your CRM, SMS, and email — automatically. No Zapier. No custom code. No deals left cold.

**Primary CTA Button (`#5a96ff` fill, white IBM Plex Sans 600 label):**
`→ Start Your Free Automation` *(links directly to signup — never to another marketing page)*

**Secondary CTA (text link, `#5a96ff` color):**
`See how it works` *(smooth-scrolls to demo section)*

**Above-Fold Product Signal — Right Column:**
A simplified automation flow diagram (not a screenshot, a designed artifact):
```
[ShowingTime Confirmation]
        ↓  (Signal Blue connector)
[Showing Signal Middleware]
        ↓
  ┌─────┼─────┐
  ↓     ↓     ↓
[CRM] [SMS] [Email]
        ↓  (Amber pulse node)
[Deal Progresses]
```
This diagram uses `#5a96ff` for connector lines and `#f5a623` for the terminal outcome node. Rendered as SVG, animated with a 2s loop pulse on the amber node.

**Trust Signal Bar (below hero, full-width, `#f3f4f6` background):**
Three IBM Plex Sans 500 short statements separated by `·`:
> Works with ShowingTime · Connects any CRM · Setup in under 10 minutes

---

### SECTION 2 — Pain Amplification ("The Gap No One Is Fixing")

**Layout:** Full-width, `#f3f4f6` background, centered content max-width 720px

**H2:** "ShowingTime Was Built for Scheduling. Not for Selling."

**Body copy (3 short paragraphs, IBM Plex Sans 17px):**
Articulates the architectural reality: ShowingTime's MLS-coordinator design means zero native post-showing triggers, no Zapier connection, and Zillow's incentive structure pointing toward Dotloop — not open CRM integration. Confirms the gap is structural, not a roadmap issue.

**Amber callout block (`#f5a623` left border, `#fff` background, IBM Plex Sans italic 16px):**
> "Every hour without a follow-up after a showing is market signal decaying. Buyers tour six homes. Agents who move first, win first."

**No CTA in this section** — this is a conviction-building section, not a conversion moment.

---

### SECTION 3 — Product Mechanics ("What Showing Signal Actually Does")

**Layout:** Three-column feature cards on desktop / single-column stack on mobile

**H2:** "One Middleware. Three Workflows. Zero Manual Steps."

Each card contains:
- Icon (outline style, `#5a96ff` fill at 60% on hover, 40% at rest)
- H3 in Space Grotesk 600
- 2-sentence body in IBM Plex Sans 400

**Card 1 — Trigger Capture**
*Icon: signal receiver*
> ShowingTime confirmation lands → Showing Signal intercepts it. The event timestamp, property address, and buyer profile become structured data, not a buried email.

**Card 2 — CRM Sync**
*Icon: database with arrow*
> Your contact record updates immediately. Deal stage, showing count, property notes — written to your CRM without you opening a tab.

**Card 3 — Sequence Launch**
*Icon: branching workflow*
> SMS reminder fires 30 minutes post-showing. Feedback-request email goes at 3 hours. Nurture sequence begins at 24 hours if no response. All configurable. None manual.

**Below cards — JetBrains Mono code preview block:**
```
POST /webhook/showing-confirmed
{
  "buyer_id": "cxt_2847",
  "property": "1402 Elm Street",
  "time": "2026-02-14T14:30:00Z",
  "triggered_flows": ["crm_update", "sms_t+30", "email_t+3h"]
}
```
Label above in IBM Plex Sans 12px `--color-neutral-mid`: `// Sample trigger payload — your data, your CRM field names`

---

### SECTION 4 — Social Proof / Credibility ("Agents Using It")

**Layout:** Full-width, white background, centered

**H2:** "Built for Independent Teams Who Can't Afford Missed Follow-Ups"

**Testimonial format (if populated):** IBM Plex Sans 400 italic, 18px, attribution in 13px 600.
**Placeholder structure for launch:**
Two column testimonial cards with agent name, team name, city, and quote. Cards have `#f3f4f6` background, `#5a96ff` top border (3px).

**Metrics bar (if data available):**
Three `--color-primary` numerics in Space Grotesk 700 48px with IBM Plex Sans 400 14px labels beneath:
- `<30 min` / Average setup time
- `3 CRMs` / Supported at launch
- `100%` / ShowingTime event coverage

---

### SECTION 5 — Pricing

**Layout:** Centered, max-width 900px, white background

**H2:** "Pricing That Makes Sense for a 4-Agent Team"

**Card layout:** Two or three tier cards side by side on desktop / stacked on mobile.
Recommended plan card: `#f5a623` border (2px) + Space Grotesk 600 amber label "Most Popular" badge.

**Per-card structure:**
- Tier name in Space Grotesk 700 24px
- Price in Space Grotesk 700 40px + IBM Plex Sans 400 14px "/month"
- Feature list in IBM Plex Sans 400 15px with `#5a96ff` checkmark icons
- CTA button: Primary tiers use `#5a96ff` fill; base tier uses `#f3f4f6` fill with `#111827` text

**Pricing anchoring note:** Align tier structure to `$6,586` conservative MRR ceiling — ensure volume-based or seat-based logic makes that reachable at realistic buyer's agent team sizes.

---

### SECTION 6 — How It Works (Setup Flow)

**Layout:** Numbered steps, alternating left/right on desktop, stacked on mobile. `#f3f4f6` section background.

**H2:** "From Showing Confirmed to Follow-Up Sent in 10 Minutes"

**4-Step Flow:**

1. **Connect ShowingTime** — Auth your ShowingTime account via API credential. (Space Grotesk 600 H3 + IBM Plex Sans body)
2. **Map Your CRM Fields** — Tell Showing Signal which fields matter. Supports custom properties.
3. **Configure Your Sequences** — Set timing, tone, and triggers for SMS and email.
4. **Watch It Run** — Every confirmation becomes a workflow. Every feedback response updates your deal stage.

Step numbers in Space Grotesk 700 48px, `#5a96ff` color, low-opacity (15%) background circle.

**Section-end micro-CTA (IBM Plex Sans 600, `#5a96ff` text link):**
`→ See the full setup guide` — no dead end; links to docs or in-app walkthrough.

---

### SECTION 7 — FAQ (SXO Critical — Addresses Search-Intent Queries)

**Layout:** Accordion format, single column, white background, max-width 720px

**H2:** "Questions From Agents Before They Sign Up"

**Required FAQ entries (target long-tail queries):**

1. Does Showing Signal work with ShowingTime without an API key?
2. Which CRMs does Showing Signal connect to?
3. Can I customize when the SMS fires after a showing?
4. What happens if a showing gets cancelled or rescheduled?
5. Is this different from just using Zapier with ShowingTime?
6. Does this work for buyer's agents specifically, or just listing agents?
7. How does Showing Signal handle buyer feedback requests?
8. What's the cancellation policy?

Accordion items: IBM Plex Sans 400 body, question in 600. Open/close toggle uses `#5a96ff` chevron icon. No external links in FAQ answers — all internal or in-app paths.

---

### SECTION 8 — Final CTA (No Dead Ends Rule)

**Layout:** Full-width, `#5a96ff` background section (only section allowed to use primary as fill — used sparingly for terminal CTA weight)

**H2 (white, Space Grotesk 700):** "Your Next Showing Confirmation Is Already Waiting."

**Body (white 80% opacity, IBM Plex Sans 400):** "Set up Showing Signal before your next showing day. Every follow-up fires automatically from that moment forward."

**CTA Button (white fill, `#5a96ff` text, IBM Plex Sans 600):**
`→ Start Free — No Credit Card Required`

**Secondary link (white 70%, IBM Plex Sans 400 14px underline):**
`Book a 15-minute walkthrough instead` — links to Calendly or equivalent, never a dead end.

---

### FOOTER

- Logo mark + "Showing Signal" wordmark in Space Grotesk 600
- IBM Plex Sans 13px `--color-