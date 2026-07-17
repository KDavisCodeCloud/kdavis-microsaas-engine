# BUILD_BRIEF_CLAUDE_DESIGN.md

## Ninety Nine Comply — Product Design Brief

**Version:** 1.0
**Vertical:** HR / Ops / People Management
**Conservative MRR Target:** $5,400/mo (150 accounts × $39 blended anchor)
**Brief Type:** Micro SaaS Landing Page + Visual System

---

## 1. Visual Personality Statement

Ninety Nine Comply sits at the intersection of **institutional trust** and **human warmth** — the same emotional register that makes Gusto feel like a payroll partner rather than a compliance burden. The product replaces a process that small business owners dread (IRS paperwork, penalty anxiety, spreadsheet chaos) with one that feels *handled* — quietly confident, never clinical.

The brand voice is **the organized friend who happens to know tax law**. Not a government portal. Not a big-four accounting firm. A calm, capable tool that says *"We caught that $600 threshold before you had to think about it."*

Visual mood keywords: **structured relief, quiet authority, human-scale compliance.**

Benchmark brand DNA to channel:
- **Gusto** — warmth in a traditionally cold category, celebration of milestones, never intimidating
- **Rippling** — systematic clarity, data density done cleanly, trust through precision
- **BambooHR** — approachable HR professionalism, muted delight, people-first framing

---

## 2. Palette Application

> **Constraint:** Use only the provided industry palette. No new brand colors are introduced.

| Token | Hex | Role |
|---|---|---|
| `--color-primary` | `#6366f1` | Primary actions, CTA buttons, active nav states, progress indicators, alert badges |
| `--color-secondary` | `#10b981` | Success states, threshold-safe indicators, "W-9 collected ✓" status chips, milestone celebrations, pricing tier highlights |
| `--color-primary-dark` | derived: `#4f46e5` | Hover states on primary buttons, pressed states — darken `#6366f1` by 12% |
| `--color-secondary-dark` | derived: `#059669` | Hover states on success chips — darken `#10b981` by 12% |
| `--color-surface` | `#ffffff` | Card backgrounds, modal surfaces |
| `--color-bg` | `#f8f9ff` | Page background — a near-white with the faintest indigo undertone (mix 2% `#6366f1` into white) |
| `--color-text-primary` | `#111827` | Body copy, headings |
| `--color-text-muted` | `#6b7280` | Supporting copy, labels, helper text |
| `--color-border` | `#e5e7eb` | Card borders, dividers, input strokes |
| `--color-warning` | `#f59e0b` | Threshold approaching alerts (e.g. contractor at $520/$600) — amber used sparingly as a functional signal only |
| `--color-danger` | `#ef4444` | Overdue W-9 status, penalty risk callouts — functional only, never decorative |

### Palette Application Rules

1. **`#6366f1` (indigo-primary)** anchors every primary interactive surface. The main CTA button, the dashboard's active sidebar item, the progress ring on the contractor compliance tracker, the email collection modal header. It is the color of *action*.

2. **`#10b981` (emerald-secondary)** is the color of *completion and safety*. A W-9 that's on file turns green. A contractor safely below the $600 threshold shows a green badge. The "1099s ready" year-end confirmation screen lives in this color. It provides psychological relief — the product's core emotional promise.

3. **Never use `#6366f1` and `#10b981` at equal visual weight on the same component.** One leads, one confirms. Indigo acts; emerald validates.

4. **Backgrounds stay neutral.** The `#f8f9ff` page wash prevents the product from feeling like a color-forward consumer app. This is a compliance tool — the data is the hero. Color draws the eye only to status and action.

5. **Warning amber (`#f59e0b`) is a *product signal*, not a brand color.** Use it only in the threshold alert UI (contractor approaching $600) and never in marketing materials, illustrations, or decorative contexts.

---

## 3. Typography

> **Constraint:** Use only the THD Agentic Systems base design system typefaces. Inter, Roboto, and Arial are excluded.

### Type Stack

```css
--font-display:   'Space Grotesk', sans-serif;
--font-body:      'IBM Plex Sans', sans-serif;
--font-mono:      'JetBrains Mono', monospace;
```

### Roles & Rationale

| Face | Role | Why |
|---|---|---|
| **Space Grotesk** | Hero headlines, section H1s, pricing tier names, above-fold value proposition | Geometric warmth with just enough personality to feel human-first without tipping into playful. Echoes Gusto's approachable authority. Its slightly rounded terminals keep the compliance category from feeling cold. |
| **IBM Plex Sans** | All body copy, subheadings (H2–H4), navigation labels, form labels, FAQ content, testimonials | Engineered by IBM for UI clarity at scale — communicates systematic reliability. Its open apertures improve legibility on contractor lists and data tables. |
| **JetBrains Mono** | Threshold dollar amounts ($600.00), EIN display, IRS form reference codes, payment totals in the dashboard demo screenshot, any code/data strings | Signals that numeric data is precise and machine-generated, not approximated. Converts compliance anxiety into technical confidence. |

### Type Scale (Mobile-first, rem-based)

```
--text-xs:    0.75rem  / 12px   — status chip labels, micro-copy
--text-sm:    0.875rem / 14px   — helper text, form labels (IBM Plex Sans)
--text-base:  1rem     / 16px   — body paragraphs (IBM Plex Sans)
--text-lg:    1.125rem / 18px   — lead paragraph, feature subheads (IBM Plex Sans)
--text-xl:    1.25rem  / 20px   — card headlines (Space Grotesk)
--text-2xl:   1.5rem   / 24px   — section H2s (Space Grotesk)
--text-3xl:   1.875rem / 30px   — section H1s (Space Grotesk)
--text-4xl:   2.25rem  / 36px   — above-fold headline mobile (Space Grotesk)
--text-5xl:   3rem     / 48px   — above-fold headline desktop (Space Grotesk)
--text-mono:  1rem     / 16px   — threshold amounts, data values (JetBrains Mono)
```

---

## 4. Landing Page Structure

### Information Architecture — Section Map

```
┌─────────────────────────────────────────────┐
│  0. Top Nav Bar                             │
├─────────────────────────────────────────────┤
│  1. Hero / Above-Fold                       │  ← SXO PRIMARY CTA
├─────────────────────────────────────────────┤
│  2. Social Proof Bar                        │
├─────────────────────────────────────────────┤
│  3. Pain Agitation                          │
├─────────────────────────────────────────────┤
│  4. Solution Walkthrough (3-Step)           │
├─────────────────────────────────────────────┤
│  5. Live Dashboard Preview                  │
├─────────────────────────────────────────────┤
│  6. Feature Grid                            │
├─────────────────────────────────────────────┤
│  7. Milestone Sequence / Retention Story    │
├─────────────────────────────────────────────┤
│  8. Pricing Tiers                           │  ← SXO SECONDARY CTA
├─────────────────────────────────────────────┤
│  9. Accountant / Bookkeeper CTA Band        │
├─────────────────────────────────────────────┤
│ 10. FAQ (Schema-marked)                     │
├─────────────────────────────────────────────┤
│ 11. Final CTA / Footer                      │  ← SXO TERTIARY CTA
└─────────────────────────────────────────────┘
```

---

### Section 0 — Top Navigation Bar

**Layout:** Sticky, white background, 1px `#e5e7eb` bottom border. Collapses to hamburger at <768px.

**Left:** Wordmark — "Ninety Nine Comply" in **Space Grotesk SemiBold**, `#111827`. Consider a small glyph: a document with a checkmark in `#6366f1`.

**Center (desktop only):** Nav links in **IBM Plex Sans Medium 14px** — `Features` · `Pricing` · `Integrations` · `For Accountants`

**Right:** `Log in` (ghost, `#6b7280`) + `Start Free Trial` (filled button, `#6366f1`, white label, 8px radius).

---

### Section 1 — Hero / Above-Fold

**SXO Requirement:** Primary CTA must be above the fold on all devices. No scroll required to reach the conversion action.

**Headline (Space Grotesk Bold, `--text-4xl` mobile / `--text-5xl` desktop):**
> Stop Losing Hours to 1099 Paperwork.
> Ninety Nine Comply Does It Automatically.

**Sub-headline (IBM Plex Sans Regular, `--text-lg`, `#6b7280`):**
> Collect W-9s, track the $600 IRS threshold, and generate 1099-NEC filings — without spreadsheets, accountant fees, or penalty risk.

**Primary CTA Button:**
- Label: `Collect Your First W-9 Free →`
- Style: `background: #6366f1`, white text, **Space Grotesk SemiBold**, `--text-base`, `px-6 py-3`, `border-radius: 8px`
- Hover: `background: #4f46e5`
- Full-width on mobile; inline on desktop

**Beneath button — micro-trust copy (IBM Plex Sans Regular, `--text-sm`, `#6b7280`):**
> No credit card required · Setup in under 5 minutes · Cancel anytime

**Hero Visual (right column, desktop / below fold on mobile):**
A stylized dashboard preview showing:
- Three contractor rows with W-9 status chips: two `● Collected` in `#10b981`, one `● Pending` in `#f59e0b`
- A cumulative payment tracker: one contractor at `$582.00` with an amber "Approaching $600 threshold" alert in `#f59e0b`
- A "1099s Ready to File" banner in `#10b981` with a download icon
- Dollar amounts rendered in **JetBrains Mono**
- Card background `#ffffff`, page chrome `#f8f9ff`

**Background:** `#f8f9ff` full bleed. No hero image. Data UI is the hero.

---

### Section 2 — Social Proof Bar

**Layout:** Horizontal strip, `#ffffff`, `border-top` and `border-bottom` in `#e5e7eb`, `py-6`.

**Content options (render what's available; placeholder structure for pre-launch):**
- Left: `"Trusted by 150+ small businesses"` in **IBM Plex Sans Medium**
- Center: 4–5 company logo lockups (grayscale, `#9ca3af`)
- Right: Star rating (★★★★★) + `"4.9 on Capterra"` or equivalent

**Pre-launch fallback:** Replace logos with three stat chips:
- `66M+ 1099-NECs filed with the IRS annually`
- `$600 threshold — the number every contractor business must track`
- `$1,500 avg. accountant fee this tool replaces`

All stat labels in **IBM Plex Sans Medium**, numbers in **Space Grotesk Bold**, `#6366f1`.

---

### Section 3 — Pain Agitation

**Headline (Space Grotesk Bold, `--text-3xl`):**
> If you pay contractors, the IRS is already watching.

**Layout:** Two-column on desktop, single-column mobile. Left column: pain narrative. Right column: penalty callout card.

**Left — Body copy (IBM Plex Sans Regular, `--text-base`, `#374151`):**

> Every year, small business owners face the same Q4 scramble: hunting down W-9s from contractors who've gone quiet, manually tallying payments in spreadsheets, guessing who crossed $600, and either paying an accountant $500–$1,500 or filing incorrectly and hoping the IRS doesn't notice.
>
> The IRS does notice. The penalty for a single missing 1099-NEC starts at **$60 per form**. Late or incorrect filings compound. And that's before the hours — industry estimates put manual 1099 compliance at **3–5 hours per week** for contractor-heavy teams.

**Right — Penalty Callout Card:**
- Background: `#ffffff`, `border: 1px solid #e5e7eb`, `border-radius: 12px`
- Top accent bar: `4px solid #ef4444` (danger-functional)
- Card headline: `The Real Cost of Doing This Manually` (**Space Grotesk SemiBold**, `--text-xl`)
- Three line items:
  - `⏱ 3–5 hrs/week` · *Chasing W-9s and updating spreadsheets* — **IBM Plex Sans**
  - `💸 $60–$310 per form` · *IRS penalty per missing 1099-NEC* — **IBM Plex Sans**
  - `📋 $500–$1,500` · *What accountants charge to do this at year-end* — **IBM Plex Sans**
- Footer: `Ninety Nine Comply eliminates all three.` in **IBM Plex Sans SemiBold**, `#6366f1`

---

### Section 4 — Solution Walkthrough (3-Step)

**Headline (Space Grotesk Bold, `--text-3xl`):**
> Compliance on autopilot, from first payment to final filing.

**Layout:** Three-column cards on desktop, vertical stack mobile.

| Step | Icon | Headline | Body |
|---|---|---|---|
| **01** | Document icon in `#6366f1` bg circle | **Branded W-9 Portal, Sent Automatically** | Add a contractor. We send them a branded collection link. They fill out their W-9 digitally. You never draft a single email. |
| **02** | Bell icon in `#6366f1` bg circle | **Threshold Monitoring That Never Sleeps** | Connect your QuickBooks or import a CSV. Every payment is tracked against the $600 IRS trigger. You get an alert at $550 — before the requirement kicks in, not after. |
| **03** | Checkmark icon in `#10b981` bg circle | **1099-NECs Generated in Under 10 Minutes** | At year-end, every form is pre-filled from collected W-9 data. Export ready-to-file PDFs or submit directly to the IRS FIRE system. Done. |

**Step number:** **Space Grotesk Bold**, `--text-3xl`, `#e5e7eb` (large, behind the card heading as a ghost element — design texture only).

**Card style:** `background: #ffffff`, `border: 1px solid #e5e7eb`, `border-radius: 12px`, `padding: 32px`, subtle `box-shadow: 0 1px 3px rgba(0,0,0,0.06)`.

**Bottom of section — inline CTA (no dead end):**
> Ready to see it working? → `Start Free Trial` [button: `#6366f1`]

---

### Section 5 — Live Dashboard Preview

**Headline (Space Grotesk Bold, `--text-2xl`):**
> Your entire contractor compliance picture, in one place.

**Layout:** Full-width annotated screenshot or high-fidelity UI mockup of the main dashboard