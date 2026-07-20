# BUILD_BRIEF_CLAUDE_DESIGN.md

## Campaign Aware Replenishment
### Shopify-Native Ad-Spend-Aware Inventory Forecasting · $49/month

---

## 1. Visual Personality Statement

**Campaign Aware Replenishment** occupies the precise intersection of performance marketing and operational logistics — two disciplines that DTC founders navigate simultaneously but have never seen unified in a single affordable tool. The visual language must communicate **analytical confidence without intimidation**: this is not enterprise BI software, and it is not a scrappy MVP. It is a focused, opinionated instrument built for operators who run Meta and Google campaigns every week and still lose sleep over stockouts.

The personality is **calm precision with commercial urgency**. Think a seasoned media buyer who also runs the warehouse: organized, direct, numbers-forward, with flashes of energy when a campaign insight demands attention. The interface and marketing surfaces should feel like a Bloomberg terminal that actually respects the user's time — clean grids, purposeful color, no decorative noise.

**Emotional target:** The user opens their weekly reorder email on Monday morning and feels *oriented* — not overwhelmed. They see exactly which SKUs need to be purchased before their Black Friday campaign goes live and they act immediately because the recommendation is obviously correct.

**Three-word brand voice:** Precise. Actionable. Timely.

---

## 2. Palette Application

> **Constraint:** Only the supplied palette colors are used for brand expression. No additional brand colors are introduced.

| Token | Hex | Role |
|---|---|---|
| `--color-primary` | `#5a96ff` | Primary interactive elements, CTAs, active nav states, data highlight rings, chart accent lines for "reorder now" status |
| `--color-secondary` | `#f5a623` | Warning/urgency signals, campaign countdown badges, "at-risk stockout" flags, hover accent on secondary CTAs |
| `--color-bg-base` | `#f8f9fc` | Page canvas — near-white, never pure white, reduces eye fatigue in data-dense views |
| `--color-surface` | `#ffffff` | Card surfaces, modal backgrounds, table rows |
| `--color-surface-alt` | `#eef2fa` | Alternating table rows, sidebar backgrounds, input field fills (derived tint of primary at 8% opacity — not a new brand color) |
| `--color-text-primary` | `#1a1f2e` | All body copy, table data, headings |
| `--color-text-secondary` | `#6b7280` | Labels, subtext, placeholder copy |
| `--color-border` | `#e2e6ef` | Dividers, card borders, input outlines |

### Application Rules

**`#5a96ff` (Primary):**
- The single CTA button color across all surfaces (`Install Free · 14-Day Trial`)
- Active state for navigation tabs (SKU list, Campaign Calendar, Reorder Queue)
- Sparkline color for "healthy velocity" trend lines in the product UI screenshots
- Link color on all marketing pages
- Focus rings on all interactive elements (accessibility: 3:1 contrast minimum against white surface)

**`#f5a623` (Secondary):**
- Reserved exclusively for **urgency and alert states** — this color should trigger a mild physiological "act now" response
- Stockout-risk badges (`⚠ 6 days of stock · Q4 campaign starts in 9 days`)
- Campaign countdown chip in the hero section
- The horizontal accent bar above the pricing card (draws eye to conversion moment)
- Hover state on the secondary CTA (`See How It Works`)
- Never used for decorative purposes — every appearance of amber carries informational weight

**Mood (Neutral/Adaptable):**
- Background surfaces stay achromatic — the two accent colors do all the heavy lifting
- Data visualizations use the primary blue for positive/healthy states and secondary amber for warning states; no red is introduced (red signals error, not urgency — this product surfaces opportunities, not failures)
- Dark mode variant: `--color-bg-base` shifts to `#0f1117`, surfaces to `#1a1f2e`, accent colors remain identical

---

## 3. Typography

> **Constraint:** Typography is drawn exclusively from the base design system stack. Inter, Roboto, and Arial are not used.

### Typeface Assignments

| Role | Family | Weight | Size Range | Usage |
|---|---|---|---|---|
| Display / Hero | **Space Grotesk** | 700 | 48–72px | Hero headline, section titles, pricing callout |
| UI Headings | **Space Grotesk** | 600 | 20–36px | Card headers, feature section titles, modal headings |
| Body / Marketing | **IBM Plex Sans** | 400 / 500 | 15–18px | Landing page prose, feature descriptions, testimonials |
| UI Labels & Nav | **IBM Plex Sans** | 500 | 12–14px | Navigation items, badge text, form labels, table column headers |
| Data / Code | **JetBrains Mono** | 400 / 500 | 13–15px | SKU codes, spend figures in UI mockups, API references, reorder quantity outputs |

### Type Rationale

**Space Grotesk** carries the brand voice at display scale — its slightly quirky geometry signals "built by practitioners, not a design agency," which matches the DTC operator audience who trusts tools that look like tools, not brand campaigns.

**IBM Plex Sans** provides workhorse readability for all prose and UI utility text. Its origins in IBM's technical communication heritage reinforce the analytical, data-forward personality without veering into cold enterprise territory.

**JetBrains Mono** appears whenever a number, SKU, or system output is displayed. This is a deliberate signal: *this data is precise and machine-generated, not estimated.* When users see `SKU-00431 · Reorder: 240 units · Confidence: 94%` in monospace, they read it as system output — authoritative and trustworthy.

### Typographic Scale (CSS Custom Properties)

```css
--text-display:   clamp(2.5rem, 5vw, 4.5rem);   /* Hero H1 · Space Grotesk 700 */
--text-h1:        clamp(2rem, 3.5vw, 3rem);       /* Section H2 · Space Grotesk 600 */
--text-h2:        clamp(1.5rem, 2.5vw, 2rem);     /* Card Title · Space Grotesk 600 */
--text-h3:        1.25rem;                         /* Sub-section · Space Grotesk 500 */
--text-body-lg:   1.125rem;                        /* Lead copy · IBM Plex Sans 400 */
--text-body:      1rem;                            /* Body · IBM Plex Sans 400 */
--text-label:     0.875rem;                        /* UI Label · IBM Plex Sans 500 */
--text-mono:      0.9375rem;                       /* Data Output · JetBrains Mono */
--text-mono-sm:   0.8125rem;                       /* SKU/Badge · JetBrains Mono */
```

### Line Height & Spacing

- Display/H1: `line-height: 1.1` — tight, confident
- Body: `line-height: 1.65` — generous for prose readability
- Mono data: `line-height: 1.5` — standard terminal feel
- Letter-spacing: `0.01em` on Space Grotesk display only; all other faces at `normal`

---

## 4. Landing Page Structure

> Mobile-first. All sections defined at 375px base, scaling to 1280px max-width container.

---

### 4.0 Global Navigation Bar

```
[Logo: Campaign Aware · Space Grotesk 600 · #5a96ff wordmark]
                                    [Features] [Pricing] [How It Works]
                                    [Install Free →] ← #5a96ff pill button
```

- Sticky on scroll; backdrop-blur on scroll (`background: rgba(248,249,252,0.92)`)
- Mobile: hamburger collapses to full-screen overlay; CTA persists as bottom-fixed bar
- No dead end: CTA present in nav at all scroll depths

---

### 4.1 Hero Section · Above the Fold

**Purpose:** Communicate the singular differentiator and capture the click in under 6 seconds.

**Layout (Desktop):** 60/40 split — copy left, animated UI mockup right
**Layout (Mobile):** Full-width copy stack, mockup below fold line

```
┌─────────────────────────────────────────────────────────────┐
│  [amber badge chip] ⚡ Ad-spend-aware · Shopify-native      │
│                                                             │
│  Stop Guessing How Much                                     │  ← Space Grotesk 700
│  Stock Your Next Campaign                                   │     #1a1f2e
│  Actually Needs.                                            │
│                                                             │
│  Campaign Aware syncs your Meta and Google Ads spend        │  ← IBM Plex Sans 400
│  calendar with your Shopify sales velocity — and tells      │     18px · #6b7280
│  you exactly how many units to reorder, per SKU,           │
│  before your campaign goes live.                            │
│                                                             │
│  [Install Free · 14-Day Trial →]  [See How It Works ↓]    │
│   #5a96ff fill · Space Grotesk     outlined · amber hover  │
│   600 · 16px                                               │
│                                                             │
│  ✓ Free 14-day trial  ✓ No credit card  ✓ 5-min setup     │  ← IBM Plex 400 · 13px
└─────────────────────────────────────────────────────────────┘
```

**Hero UI Mockup (right panel):** Animated card showing:
- SKU table with JetBrains Mono data: `SKU-0841 · "Summer Linen Tee" · Reorder: 340 units`
- Amber badge: `⚠ Meta campaign starts in 11 days · Current stock: 6 days`
- Primary blue sparkline trending up with campaign date marker
- Subtle pulse animation on the amber badge (CSS keyframes, no JS dependency)

**SXO Anchor:** H1 contains primary keyword phrase naturally. Above-fold CTA uses action verb. Social proof micro-copy immediately below buttons.

---

### 4.2 Social Proof Bar · Trust Strip

Immediately below hero, full-width, `--color-surface-alt` background.

```
  Trusted by Shopify DTC brands running $10K–$500K/month in ad spend
  
  ★★★★★  "Cut our Q4 stockouts by 60%"   |   200+ active stores
          — Operations Lead, [Brand]           since launch
```

- IBM Plex Sans 500 · 14px · centered
- Stars in `#f5a623`
- No logos required at launch (avoids placeholder embarrassment); replace with real logos at 25-store milestone

---

### 4.3 Problem Articulation · "The Monday Morning Nightmare"

**Purpose:** Mirror the pain before presenting the solution. Create the "they get me" moment.

**Layout:** Single column, max-width 720px, centered

```
HEADING (Space Grotesk 600 · 32px):
"You ran a big campaign. Your ads worked.
 And you ran out of stock on day three."

BODY (IBM Plex Sans 400 · 17px · #6b7280):
Most inventory tools look backward at what you sold.
They don't know your Meta campaign starts in two weeks
and is budgeted at 3× your usual weekly spend.

So they recommend the same reorder quantity they always do.
And you're left holding an empty warehouse while your
best-performing ad is still running.
```

**Below:** Three pain cards in a 3-column grid (1-column mobile)

| Card | Icon | Headline | Body |
|---|---|---|---|
| 1 | 📉 | Inventory Planner costs $120–$245/mo | Overkill pricing for brands under $3M ARR — you're paying for features built for 50,000-SKU operations. |
| 2 | 🔗 | Your ad platform and your inventory tool don't talk | No tool at the $49 price point connects Meta/Google campaign calendars to reorder sizing. Until now. |
| 3 | ⏰ | Reorder decisions made on gut feel | By the time your sales velocity signals a stockout, your supplier lead time has already lost the window. |

---

### 4.4 Solution Reveal · "How Campaign Aware Works"

**Purpose:** Mechanistic clarity. Show exactly what the product does in three numbered steps. No vague "AI magic."

**Layout:** Alternating 50/50 rows (desktop); stacked cards (mobile)

---

**Step 1 — Connect**

```
[UI Screenshot: OAuth screens for Meta Ads + Google Ads + Shopify]

HEADING: Connect your ad accounts and Shopify store in 5 minutes.

BODY: Campaign Aware reads your Meta Ads and Google Ads campaign 
calendars — flight dates, budgets, and projected spend periods. 
It also reads your Shopify sales velocity per SKU for the last 
90 days. No CSV exports. No manual data entry.
```

---

**Step 2 — Analyze**

```
[UI Screenshot: Campaign calendar view with SKU velocity overlay]

HEADING: See how upcoming campaigns will stress your inventory.

BODY: For every active and scheduled campaign, Campaign Aware 
models the demand uplift based on your historical spend-to-sales 
correlation. It surfaces which SKUs are at risk of stockout 
before, during, or immediately after each campaign flight.

[Amber badge example]: ⚠ SKU-0441 · "Linen Blazer" · 
Projected demand: 480 units · Current stock: 120 units · 
Campaign starts: 9 days
```

---

**Step 3 — Reorder**

```
[UI Screenshot: Weekly reorder recommendation queue]

HEADING: Get your weekly per-SKU reorder list, sized to campaign demand.

BODY: Every Monday, Campaign Aware generates a prioritized reorder 
queue. Each line item shows the recommended order quantity, the 
supplier lead time you've configured, the campaign it's tied to, 
and the confidence score. Export to CSV or send directly to your 
3PL. No guesswork required.

[JetBrains Mono data block]:
SKU-0841  Summer Linen Tee     Reorder: 340 units  Confidence: 91%
SKU-1203  Crossbody Tote       Reorder: 180 units  Confidence: 87%
SKU-0302  Wide-Leg Trouser     Reorder: 520 units  Confidence: 94%
```

---

### 4.5 Differentiation Table · "Why Not Just Use…"

**Purpose:** Handle the Inventory Planner and Prediko objections explicitly. DTC operators research before installing apps.

**Layout:** Comparison table, full-width on desktop, horizontal-scroll on mobile

| Feature | Campaign Aware | Inventory Planner Essentials | Prediko |
|---|---|---|---|
| **Price** | **$49/mo** | $119.99/mo | $49/mo |
| **Meta Ads campaign calendar sync** | ✅ **Yes** | ❌ No | ❌ Not documented |
| **Google Ads campaign calendar sync** | ✅ **Yes** | ❌ No | ❌ Not documented |
| **Shopify-native** | ✅ Yes | ✅ Yes | ✅ Yes |
| **SKU-level reorder recommendations** | ✅ Yes | ✅ Yes | ✅ Yes |
| **Ad-spend-aware demand uplift modeling** | ✅ **Yes** | ❌ No | ❌ No |
| **Weekly reorder queue** | ✅ Yes | ✅ Yes | ✅ Yes |
| **Setup time** | ⚡ ~5 min | ~2 hrs | ~30 min |

- Header row: `--color-primary` background, white text, Space Grotesk 600
- "Campaign Aware" column: subtle `--color-surface-alt` highlight
- ✅ in primary blue · ❌ in `#9ca3af` ·