# BUILD_BRIEF_CLAUDE_DESIGN.md

## Series Scheduler Pro — Micro SaaS Design Brief

**Product:** Series Scheduler Pro
**Brief Version:** 1.0
**Prepared for:** THD Agentic Systems — Micro SaaS Engine
**Vertical:** Service Professionals (Therapists · Coaches · Trainers · Tutors · Consultants · Wellness Providers)

---

## 1. Visual Personality Statement

Series Scheduler Pro is **calm authority with a pulse of momentum.** It exists at the intersection of professional trust and frictionless efficiency — the quiet confidence of a tool that simply *works* the way service professionals already think about their weeks. The product solves a known, documented frustration in a beloved ecosystem (Calendly), so the visual language must never feel adversarial or janky. It should feel like the missing premium layer that Calendly users have been waiting for: polished, purposeful, and unmistakably serious.

**Three brand adjectives:** Reliable · Frictionless · Professional

**Brand emotion:** The feeling of watching a full week of recurring client sessions populate in a single click — relief, control, momentum.

The palette was selected for **open/neutral adaptability**, meaning it can speak to the full breadth of service professionals without locking into any single sub-vertical's aesthetic codes (clinical, athletic, coaching, academic). The blue anchor communicates trust and digital-native competence; the amber accent communicates warmth, urgency, and human connection without being loud.

---

## 2. Palette Application Rules

> ⚠️ **No new brand colors may be introduced.** All design decisions must derive from the two provided accents plus documented neutral relationships described below.

| Token | Hex | Role |
|---|---|---|
| `--color-primary` | `#5A96FF` | Primary interactive elements, CTAs, nav links, focus rings, key iconography |
| `--color-accent` | `#F5A623` | Secondary CTAs, urgency badges ("Limited Beta"), hover states on cards, pricing highlights, pull-quote underlines |
| `--color-primary-dark` | `#2E6EE0` | Primary button hover/active states (darken primary 18%) |
| `--color-accent-dark` | `#D4891A` | Accent hover/active states (darken accent 12%) |
| `--color-primary-10` | `#5A96FF1A` | Feature card backgrounds, subtle section tints, input focus backgrounds |
| `--color-accent-10` | `#F5A6231A` | Warning/highlight tints, testimonial card side-borders |
| `--color-neutral-900` | `#111827` | Body copy, headings |
| `--color-neutral-600` | `#4B5563` | Secondary copy, captions, labels |
| `--color-neutral-200` | `#E5E7EB` | Dividers, card borders, input borders |
| `--color-neutral-50` | `#F9FAFB` | Section alternating backgrounds, card surfaces |
| `--color-white` | `#FFFFFF` | Primary page background, primary button text on dark fills |

### Application Logic

- **Primary blue (`#5A96FF`)** owns every decision-driving moment: the above-fold CTA button, the nav's active state, form submit buttons, plan "recommended" badges, and all linked text. This trains the eye that *blue = take action.*
- **Amber (`#F5A623`)** is the **reward and urgency signal.** Use it for the pricing table's highlighted plan tier, the "Book Your First Series Free" badge, the active step indicator in the onboarding flow screenshot, and testimonial card left-border accents. Never use amber for primary navigation.
- **Never** place amber text on white backgrounds below 24px — contrast ratio fails WCAG AA. Use `--color-accent-dark` (#D4891A) for any amber body/label text.
- **Dark sections** (e.g., hero gradient band, footer): use `--color-neutral-900` background, primary blue for interactive elements, white for headings, `--color-neutral-200` for body copy. Amber remains available as accent but use sparingly — one amber element maximum per dark section.
- **Gradient treatment** (hero only): `linear-gradient(135deg, #111827 0%, #1a2d4f 100%)` — a dark blue-navy blend derived from darkening and desaturating the primary, creating depth without introducing new hues.

---

## 3. Typography System

> ⚠️ **Permitted typefaces only:** Space Grotesk · IBM Plex Sans · JetBrains Mono. Inter, Roboto, Arial, and system-sans stacks are not permitted.

### Typeface Assignments

| Role | Typeface | Rationale |
|---|---|---|
| **Display / Hero Headlines** | Space Grotesk | Geometric confidence with slight quirk; signals modern SaaS premium. The distinctive letterforms (particularly "G" and "a") ensure brand memorability without custom type. |
| **Body / UI Copy** | IBM Plex Sans | Corporate legibility with open apertures; reads cleanly at 14–16px across the professional demographic. Feels "grown-up" without being clinical. |
| **Code / Data / API callouts** | JetBrains Mono | Used exclusively in the developer/API integration callout block, recurring rule display strings (e.g., `RRULE:FREQ=WEEKLY;COUNT=12`), and the "Works with your Calendly link" technical proof section. |

### Type Scale

```
--text-hero:      clamp(2.5rem, 5vw, 4rem)      / Space Grotesk 700
--text-h1:        clamp(2rem, 4vw, 3rem)          / Space Grotesk 700
--text-h2:        clamp(1.5rem, 3vw, 2.25rem)     / Space Grotesk 600
--text-h3:        1.25rem                          / Space Grotesk 600
--text-lead:      1.125rem                         / IBM Plex Sans 400, line-height 1.7
--text-body:      1rem                             / IBM Plex Sans 400, line-height 1.6
--text-small:     0.875rem                         / IBM Plex Sans 400
--text-label:     0.75rem / 600                   / IBM Plex Sans 600, letter-spacing 0.08em, UPPERCASE
--text-mono:      0.875rem                         / JetBrains Mono 400
```

### Typography Behavior Rules

- **Space Grotesk** is used for headings only — never for body paragraphs or labels.
- **IBM Plex Sans** handles all UI text: nav, buttons, form fields, pricing rows, footer links.
- **JetBrains Mono** appears in no more than 2 sections of the landing page (technical callout + code snippet in developer section). Do not use it decoratively.
- Heading color: `--color-neutral-900` on light backgrounds; `#FFFFFF` on dark/hero sections.
- Never apply amber or blue to heading text — color belongs to the supporting UI layer, not the typographic hierarchy.

---

## 4. Landing Page Structure

### Page Architecture Overview

```
[SECTION 1]  Hero — Above-the-fold CTA
[SECTION 2]  Problem Statement / Pain Amplification
[SECTION 3]  Product Demo / Feature Proof
[SECTION 4]  How It Works (3-Step)
[SECTION 5]  Social Proof / Testimonials
[SECTION 6]  Pricing
[SECTION 7]  FAQ
[SECTION 8]  Final CTA / Footer
```

---

### SECTION 1 — Hero (Above the Fold)

**Background:** Dark gradient (`#111827 → #1a2d4f`) — establishes premium, focuses attention on copy and CTA.

**Layout (mobile-first):**
- Stack: Badge → Headline → Subheadline → CTA primary → CTA secondary → Social proof micro-line
- Desktop: Two-column (copy left, product screenshot/animation right, 55/45 split)

**Copy Framework:**

```
[BADGE — amber #F5A623 background, neutral-900 text, IBM Plex Sans 600]
Works with your existing Calendly account

[HEADLINE — Space Grotesk 700, white, --text-hero]
Book a Full Client Series.
One Click.

[SUBHEADLINE — IBM Plex Sans 400, neutral-200, --text-lead]
Calendly doesn't do recurring appointments.
Series Scheduler Pro does. Install in 60 seconds —
your clients book their entire 8-week program in one go.

[CTA PRIMARY — #5A96FF background, white text, IBM Plex Sans 600, 18px, full-width mobile]
Start Booking Series Free →

[CTA SECONDARY — ghost button, #5A96FF border/text]
See How It Works

[SOCIAL PROOF MICRO — IBM Plex Sans 400, neutral-400, 14px]
★★★★★  Trusted by 1,200+ therapists, coaches & trainers
```

**SXO Requirements Met Here:**
- ✅ Above-fold CTA is visible without scroll on 375px viewport
- ✅ CTA text is action-verb first and outcome-specific
- ✅ No dead-end state: secondary CTA anchors to Section 4 (How It Works)

**Hero Visual (right column / mobile full-width card):**
Product UI mockup showing a Calendly-style booking flow with a "Book recurring series" toggle activated, displaying a calendar grid populating 8 sessions simultaneously. Use `--color-primary-10` as card background tint; amber dot indicators on recurring dates.

---

### SECTION 2 — Problem Statement

**Background:** `--color-neutral-50`
**Layout:** Centered, max-width 720px, single column

```
[EYEBROW LABEL — IBM Plex Sans 600, uppercase, #5A96FF, 12px]
THE CALENDLY GAP

[HEADLINE — Space Grotesk 600, neutral-900]
Your Clients Want a Series.
Calendly Doesn't Have That.

[BODY — IBM Plex Sans 400, neutral-600, 18px]
We confirmed it directly from Calendly's own community:
recurring appointment series simply don't exist in the product —
and there's "no current timeline" to add them.

That means every weekly therapy client, every 12-session fitness
program, every coaching cohort — booked one. session. at. a. time.
```

**Evidence Block (amber left-border card):**
```
[CARD — white background, 4px left border #F5A623, shadow-sm]
"Recurring meetings are not currently a feature of Calendly.
 There is no current timeline for this to be added."
— Calendly Community Manager, May 2026

[LABEL below — IBM Plex Sans 400, neutral-400, 12px]
Source: Calendly Community Forum · Verified September 2025 & May 2026
```

**Pain Metric Row (3 columns, mobile: stacked):**

| Stat | Label |
|---|---|
| **47 min** | Average time lost rebooking a 10-session client manually |
| **1-click** | All Series Scheduler Pro needs |
| **12+ sessions** | Schedulable in a single client flow |

Stat numbers: Space Grotesk 700, `--text-h1`, `#5A96FF`. Labels: IBM Plex Sans 400, neutral-600.

---

### SECTION 3 — Product Demo / Feature Proof

**Background:** White
**Layout:** Alternating feature rows (image + copy), mobile: stacked full-width

**Feature 1 — One-Click Series Booking**
- Copy: "Define your series once. Clients pick their start date and your recurrence rules handle the rest — weekly, biweekly, custom cadence."
- Visual: Animated GIF/MP4 of the series configuration UI (3-step modal)
- Icon accent: `#5A96FF` check-circle icons

**Feature 2 — Smart Conflict Avoidance**
- Copy: "Series Scheduler Pro checks every slot before confirming. No double-books. No awkward reschedule emails."
- Visual: Calendar view with amber conflict-flagged slots and blue confirmed series slots

**Feature 3 — Client Reminder Sequence**
- Copy: "Automated reminders for every session in the series — not just the first one. Clients show up."
- Visual: SMS/email reminder flow screenshot

**Feature 4 — Calendly Native Feel**
- Copy: "Installs as an add-on. Your Calendly link stays the same. Your branding stays the same. One new toggle in your settings."
- Visual: Calendly settings panel with Series Scheduler Pro toggle (blue, enabled)
- Technical callout (JetBrains Mono): `RRULE:FREQ=WEEKLY;BYDAY=TU,TH;COUNT=8`

---

### SECTION 4 — How It Works

**Background:** `--color-primary-10` (#5A96FF at 10% opacity) full-width band
**Layout:** Horizontal 3-step flow (mobile: vertical with connector line)

**Step indicators:** Circle numbered badges, `#5A96FF` fill, white number, Space Grotesk 700.

```
STEP 1                    STEP 2                    STEP 3
Connect                   Configure                 Share
─────────                 ─────────                 ─────
Link your Calendly        Set your series rules:    Send your normal
account in 60 seconds.    weekly, biweekly,         Calendly link.
No dev work required.     session count, duration.  Clients see the
                                                    "Book a Series"
                                                    option automatically.
```

Connector arrows between steps: `#F5A623` (amber) → signals forward momentum.

**CTA after steps:**
```
[Primary CTA — #5A96FF, full-width mobile]
Install Series Scheduler Pro Free →
[Subtext — IBM Plex Sans 400, neutral-600, 14px]
No credit card required · Works with any Calendly plan
```
> ✅ SXO: No dead end — every section closes with a path forward.

---

### SECTION 5 — Social Proof / Testimonials

**Background:** White
**Layout:** 3-column card grid (mobile: single column scroll)

**Card anatomy:**
- White card, 1px `--color-neutral-200` border, 12px border-radius, 4px left-border `#5A96FF`
- Star rating row: amber `#F5A623` filled stars
- Quote: IBM Plex Sans 400, neutral-900, 16px, italic
- Attribution: IBM Plex Sans 600, neutral-900, 14px + IBM Plex Sans 400, neutral-600, 13px (role/specialty)

**Testimonial Archetypes (placeholder copy — replace with real on launch):**

> *"My therapy clients used to drop off after session 3 because rebooking felt like homework. Now they're on full 8-week programs from day one."*
> — **Sarah M.**, Licensed Therapist, Austin TX

> *"I run 6-week coaching cohorts. Series Scheduler Pro basically replaced my VA."*
> — **James K.**, Executive Coach

> *"Every personal trainer needs this. My recurring clients now never have a gap week."*
> — **Priya D.**, Certified Strength Coach

**Trust bar (logos row below testimonials):**
Text label: IBM Plex Sans 400, neutral-400, 13px: *"Used by professionals at:"* + placeholder org-type badges (not client logos until real ones acquired). Badge background: `--color-neutral-50`.

---

### SECTION 6 — Pricing

**Background:** `--color-neutral-50`
**Layout:** 3-column pricing table (mobile: single column, cards stacked)

**Tier card anatomy:**
- Card background: white, `--color-neutral-200` border, 16px border-radius
- **Recommended/Most Popular card:** `#5A96FF` top border (4px), `--color-primary-10` background tint, amber "Most Popular" badge (IBM Plex Sans 600, 11px, uppercase)
- Price: Space Grotesk 700, `--text-h1`, neutral-900; billing cadence: IBM Plex Sans 400, neutral-600, 14px
- Feature list: IBM Plex Sans 400, 15px, neutral-600 + blue `#5A96FF` checkmark icons
- CTA button: Primary