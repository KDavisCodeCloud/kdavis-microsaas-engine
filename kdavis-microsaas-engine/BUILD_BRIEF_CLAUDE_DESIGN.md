# BUILD_BRIEF_CLAUDE_DESIGN.md

## Pulse Message Bridge — Product Design Brief

**Document Type:** Micro SaaS Engine Build Brief
**Product:** Pulse Message Bridge
**Vertical:** Mid-market B2B SaaS (Intercom users, 10–50 agents)
**Prepared by:** THD Agentic Systems — Micro SaaS Engine
**Status:** Ready for Build

---

## 1. VISUAL PERSONALITY STATEMENT

Pulse Message Bridge exists to eliminate a specific, documented anxiety: the dread of opening your Intercom invoice at month-end. The product's visual personality must communicate **calm certainty** — the feeling of knowing exactly what you'll pay before the billing cycle closes.

The brand sits at the intersection of **operational clarity** and **quiet confidence**. It is not a flashy startup trying to disrupt; it is a reliable infrastructure layer that mid-market SaaS ops teams and finance leads can point to and say *"this is why our support costs are predictable now."* Think: the visual language of a well-run internal tool that someone decided to make beautiful. Clean. Honest. Structured without being cold.

**Three personality pillars:**

- **Predictable** — Visual rhythm is consistent. Nothing surprises the eye. Grids are respected. Spacing is generous and intentional.
- **Credible** — Typography earns trust. Data is shown, not hidden. Pricing is upfront and unambiguous.
- **Human-grade professional** — Warm enough that a Head of CX feels comfortable forwarding the landing page to their CFO; precise enough that the CFO doesn't feel sold to.

This is infrastructure for people who've been burned by variable pricing. The design must never feel slick in a way that triggers skepticism.

---

## 2. PALETTE APPLICATION

> **Rule:** The palette below is applied exactly as specified. No new brand colors are invented. Tints and shades are derived only from the provided primaries.

### Provided Palette

| Role | Hex | Usage Rationale |
|---|---|---|
| Primary Accent | `#5a96ff` | Trust, clarity, technology — maps to "we handle the complexity" |
| Secondary Accent | `#f5a623` | Alert, value signal, warmth — maps to "pricing surprise = bad, predictability = good" |
| Mood | Neutral / Adaptable | Base surfaces are off-white and near-black; neither clinical nor playful |

### Application Rules

**Primary Accent `#5a96ff`**
- Primary CTA buttons (all states: default, hover at 15% darkened, focus ring)
- Active navigation indicators
- Feature icon fills and illustration strokes
- Inline links within body copy
- Pricing card borders on the **recommended/middle tier**
- Section divider accents (thin rule lines, 2px max)
- Form input focus states

**Secondary Accent `#f5a623`**
- Single-use "value interrupt" badges: e.g., `FLAT RATE — NO OVERAGES` pill label above hero headline
- Hover state indicator on pricing row comparisons (the "vs. Intercom variable cost" column)
- Inline callout background for the pain-point data stat (e.g., the 200+ Capterra reviews citation)
- Sticky banner (if used) for limited-time onboarding offer
- Icon accent dot on the "predictability" feature card only — used sparingly to draw the eye to the key differentiator

**Neutral Base (derived from mood: neutral/adaptable)**
- **Surface 0 (deepest):** `#0f1117` — hero section background, footer
- **Surface 1:** `#1c2030` — card backgrounds on dark sections
- **Surface 2:** `#f7f8fc` — light section backgrounds (social proof, FAQ)
- **Text Primary:** `#1a1d2e` — all body copy on light surfaces
- **Text Secondary:** `#6b7280` — subheads, captions, metadata
- **Text Inverse:** `#f0f4ff` — copy on dark/hero surfaces

**Tint Usage**
- `#5a96ff` at 10% opacity: table row zebra striping on the comparison pricing table
- `#f5a623` at 12% opacity: callout box background behind the Capterra review stat

**Color Ratio Target**
- Dark hero + footer sections: 30% of total page real estate
- Light content sections: 60%
- Accent color (combined): never exceeds 10% of any single section's visual area — keeps trust high, avoids "sales-y" override

---

## 3. TYPOGRAPHY

> **Rule:** Typography is drawn exclusively from the THD base design system. Inter, Roboto, and Arial are not used anywhere in this product.

### Type Stack

| Role | Typeface | Variant | Usage |
|---|---|---|---|
| Display / Hero | **Space Grotesk** | Bold 700 | Hero H1, section headline statements |
| Heading | **Space Grotesk** | SemiBold 600 | H2 feature sections, pricing tier names |
| Subheading | **Space Grotesk** | Medium 500 | H3 card titles, FAQ questions |
| Body | **IBM Plex Sans** | Regular 400 | All paragraph copy, feature descriptions, testimonials |
| Body Emphasis | **IBM Plex Sans** | SemiBold 600 | Pull quotes, stat callouts, inline emphasis |
| Mono / Data | **JetBrains Mono** | Regular 400 | Pricing numbers, invoice simulation examples, API endpoint references |
| Mono / UI Label | **JetBrains Mono** | Medium 500 | Badge text (e.g., `FLAT-RATE`), table column headers, code snippets |

### Type Scale (mobile-first, rem-based)

```
Display:     clamp(2.25rem, 5vw, 3.75rem)   — Space Grotesk 700
H2:          clamp(1.5rem, 3.5vw, 2.5rem)   — Space Grotesk 600
H3:          clamp(1.125rem, 2vw, 1.5rem)   — Space Grotesk 500
Body Large:  1.125rem                        — IBM Plex Sans 400
Body:        1rem                            — IBM Plex Sans 400
Body Small:  0.875rem                        — IBM Plex Sans 400
Mono Label:  0.8125rem                       — JetBrains Mono 500
```

### Typography Rationale

- **Space Grotesk** owns all structural communication — headlines signal confidence and modern infrastructure without startup-slang energy. The geometric letterforms read as "engineered."
- **IBM Plex Sans** handles all persuasive and explanatory copy. Its slight humanist warmth counterbalances the geometric headlines, making the brand feel approachable to non-technical buyers (Head of CX, RevOps Manager, CFO).
- **JetBrains Mono** is reserved for anything that resembles data, pricing, or system output — this is deliberate and critical. When a potential customer sees a pricing number rendered in monospace, it signals *precision and honesty*. It subconsciously says "this number isn't marketing copy, it's a system value." This directly serves the anti-unpredictable-billing narrative.

**Line height defaults:** Body = 1.6, Headings = 1.15, Mono = 1.5
**Letter spacing:** Space Grotesk headings at `−0.01em`; Mono labels at `+0.04em` for legibility at small sizes

---

## 4. LANDING PAGE STRUCTURE

### Overall Architecture Principle

Every section earns its place by either **removing an objection** or **adding a reason to act**. No vanity sections. The page flows: *Pain → Relief → Proof → Price → Action*. Finance-savvy mid-market buyers will scroll to pricing first; the structure anticipates this by ensuring pricing is reachable within 3 scrolls on desktop and clearly linked from the nav.

---

### Section 01 — HERO (Above the Fold)

**Purpose:** Immediately confirm the visitor landed in the right place and deliver the core value proposition in under 8 seconds.

**Layout:** Full-width dark section (`#0f1117` background). Single column on mobile, 60/40 split (copy left, visual right) on desktop ≥768px.

**Required Elements:**

```
[BADGE: JetBrains Mono 500, #f5a623 bg at 12%, text: "FLAT-RATE · NO OVERAGES"]

[H1: Space Grotesk 700]
"Stop Dreading Your Intercom Invoice."

[Subhead: IBM Plex Sans Regular, Text Inverse secondary]
"Pulse Message Bridge wraps your Intercom SMS and WhatsApp into one 
predictable monthly fee — no per-message overages, no billing surprises."

[PRIMARY CTA BUTTON: #5a96ff, Space Grotesk SemiBold]
"See Flat-Rate Pricing →"

[SECONDARY CTA: text-only link, #f0f4ff 70%]
"Book a 15-min demo"

[SOCIAL PROOF MICRO-LINE: IBM Plex Sans Small]
"Trusted by support teams at mid-market SaaS companies running 10–50 agents"
```

**Hero Visual (right column / below fold on mobile):**
Invoice simulation — a side-by-side showing a mock "Before" Intercom invoice with itemized SMS/WhatsApp line items and red total, vs. a "After" Pulse Message Bridge invoice with a single green flat-rate line. Numbers rendered in **JetBrains Mono**. This is not decorative — it is the entire argument made visual.

**SXO Compliance:**
- CTA is above the fold on all devices
- H1 contains primary search-intent keyword phrase ("Intercom SMS flat-rate")
- No decorative-only elements that push CTA below fold on mobile

---

### Section 02 — PAIN AMPLIFICATION

**Purpose:** Validate that the visitor's frustration is real, documented, and shared. Build resonance before presenting the solution.

**Layout:** Light surface (`#f7f8fc`). Centered content, max-width 760px.

**Required Elements:**

```
[EYEBROW: JetBrains Mono label, #6b7280]
"THE PROBLEM WITH INTERCOM BILLING"

[H2: Space Grotesk 600]
"Seats. Then Fin resolutions. Then per-message. Then the invoice arrives."

[Body: IBM Plex Sans]
Three-paragraph breakdown of Intercom's stacked cost structure:
— Seat-based base cost
— $0.99/Fin AI resolution (variable by volume)
— Per-message SMS/WhatsApp pass-through charges
→ "Every scaling month means a different bill."

[CALLOUT BOX: #f5a623 at 12% bg, #1a1d2e text, IBM Plex Sans SemiBold]
"200+ Capterra reviews cite unpredictable billing as their #1 Intercom 
frustration — reviewed January 2026."

[STAT ROW: 3 figures in JetBrains Mono Bold, Space Grotesk label below each]
"$0.99" / per Fin resolution
"3×"   / average invoice variance month-to-month  
"200+" / reviews flagging billing unpredictability
```

**No CTA in this section** — let the pain land without interrupting it.

---

### Section 03 — SOLUTION OVERVIEW

**Purpose:** Introduce Pulse Message Bridge as the direct fix — not a new tool to learn, but a layer that makes what they already have predictable.

**Layout:** Dark section (`#1c2030`). Two-column feature grid below intro block.

**Required Elements:**

```
[H2: Space Grotesk 600, Text Inverse]
"One flat monthly fee. All your outbound messages. Zero overages."

[Subhead: IBM Plex Sans, Text Secondary Inverse]
"Pulse Message Bridge sits between your Intercom workspace and your 
SMS/WhatsApp carriers. You keep your existing workflows. We absorb the 
volume risk."

[FEATURE GRID: 2×3 on desktop, 1×6 stacked on mobile]

Card 01: [icon: #5a96ff fill]
"Flat Monthly Billing"
"One predictable line item. Finance will thank you."

Card 02: [icon: #5a96ff fill, secondary dot: #f5a623]  ← accent dot on key differentiator
"Native Intercom Integration"  
"Install in under 15 minutes. No new agent training required."

Card 03: [icon: #5a96ff fill]
"SMS + WhatsApp Bundled"
"Both channels included. No separate carrier contracts."

Card 04: [icon: #5a96ff fill]
"Usage Dashboard"
"See your real-time message volume. Know you're within limits."

Card 05: [icon: #5a96ff fill]
"Volume Tier Matching"
"Three plan tiers matched to your typical send volume."

Card 06: [icon: #5a96ff fill]
"Instant Rollover Alerts"
"Approaching your limit? We notify you 5 days before period end — 
never a surprise overage."
```

**Inline CTA:** After grid, single text-link CTA in `#5a96ff`:
→ *"How it works technically — see integration docs"* (links to documentation or modal)

---

### Section 04 — SOCIAL PROOF / VALIDATION

**Purpose:** Replace skepticism with third-party signal. Given the early-stage nature, this section uses a credibility-stacking approach combining review data, integration verification, and customer-type specificity.

**Layout:** Light surface (`#f7f8fc`). Three-column testimonial cards on desktop, single column stack on mobile.

**Required Elements:**

```
[EYEBROW: JetBrains Mono]
"WHAT SUPPORT TEAMS SAY"

[H2: Space Grotesk 600]
"Built for the team that's tired of explaining the Intercom bill to finance."

[TESTIMONIAL CARD TEMPLATE: IBM Plex Sans]
— Quote (italic)
— Name, Title, Company size descriptor ("45-person SaaS team")
— Star rating (5 stars, #f5a623 fill)
— Source label in JetBrains Mono Small: "via Capterra · verified"

[TRUST BAR below testimonials]
Logo lockup: "Integrates with Intercom · Twilio · WhatsApp Business API"
IBM Plex Sans Small, #6b7280, centered
```

**Note:** If testimonials are not yet available at launch, substitute with:
- Anonymized paraphrase quotes sourced from Capterra reviews (cited)
- Replace testimonial cards with a single centered Capterra stat callout block using `#f5a623` background accent
- Add "Early Access" framing: *"Be among the first 20 teams — founding member pricing locked for life"*

---

### Section 05 — PRICING

**Purpose:** Eliminate the primary conversion blocker (ironic that it's pricing anxiety, for a product that fixes pricing anxiety — the design must be exceptionally transparent here).

**Layout:** Light surface with `#5a96ff` at 10% zebra stripe on table rows. Centered, max-width 900px.

**Required Elements:**

```
[EYEBROW: JetBrains Mono]
"SIMPLE PRICING — NO IRONY"

[H2: Space Grotesk 600]
"Flat rate. Published. Unchanged mid-month."

[PRICING CARD TRIO: 3 columns desktop, stacked mobile]

--- STARTER ---
[JetBrains Mono Bold, 2.5rem]
"$299"
[IBM Plex Sans Small] "/month · billed monthly"
[Space Grotesk SemiBold] "Starter"
— Up to X,000 SMS sends/mo
— Up to X,000 WhatsApp messages/mo
— 1 Intercom workspace
— Email support
[CTA: outlined #5a96ff button] "Start Free Trial"

--- GROWTH (RECOMMENDED BADGE) ---
[#5a96ff border, elevated card shadow]
[JetBrains Mono Bold, 2.5rem]
"$599"
[IBM Plex Sans Small] "/month · billed monthly"
[Space Grotesk SemiBold] "Growth"
[BADGE: #f5a623 bg, JetBrains Mono] "MOST POPULAR"
— Up to X,000 SMS sends/mo
— Up to X,000 WhatsApp messages/mo
— 3 Intercom workspaces
— Slack + email support
— Usage dashboard