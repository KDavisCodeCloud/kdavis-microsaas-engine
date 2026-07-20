# CLAUDE.md — Micro SaaS Engine
**Repo:** `kdavis-microsaas-engine`
**Company:** THD Agentic Systems LLC
**Owner:** Kelvin Davis (King Kelz)
**Last updated:** 2026-07-04

Claude Code reads this file automatically at the start of every session. Do not ask Kelvin for context that is already here. Read this file, read `MSE-Build-Order.md`, then start working the next unchecked item in the build order. No preamble.

---

## What This Repo Is

A research-validated, retention-first software factory producing 1–2 micro-SaaS products per month. Primary purpose: generate low-ticket subscription revenue ($29–$79/mo) fast enough to self-fund the operating stack (~$165–255/mo) before Cloud Decoded closes its first B2B deal.

---

## Operating Mode

Kelvin architects and designs. Claude Code executes. Kelvin validates all output. This is delegation with accountability — not pair programming. When something is ambiguous, state the assumption being made and proceed. Flag blockers immediately and specifically.

---

## Tech Stack — Do Not Deviate

- **Database:** Supabase (Postgres + RLS + Realtime + pgvector)
- **Backend:** FastAPI (Python)
- **Frontend:** Next.js 14 (App Router)
- **Agents:** LangGraph
- **Automation:** n8n (self-hosted)
- **Auth:** Supabase Auth — JWT with `tenant_id` claim
- **Payments:** Stripe — dedicated MSE account only, never shared with other products
- **Email:** Resend
- **Languages:** Python, TypeScript, Bash
- **Model routing:** Haiku for high-volume scraping AND for the Dispatch/Verdict research swarm (`agents/orchestrator`, `agents/aggregator` — switched from Sonnet 2026-07-19 as a cost-optimization pass, verified via live regression tests against known cases). Sonnet remains the default everywhere else (brief generation, naming, retention digest, CEO dashboard routes) via `core/llm_router.py`'s `model=` parameter, which defaults to Sonnet — only the swarm agents pass `model=HAIKU` explicitly. Do not change either assignment without new regression testing.

---

## Architecture Rules — Non-Negotiable

1. `tenant_id` on every table — RLS enforced, no exceptions
2. Dedicated Stripe account for MSE — never Decoded Holdings, never Cloud Decoded
3. Every agent emits `POST /events` on every state change — CEO dashboard depends on this
4. `get_supabase_for_request(jwt)` in all API routes touching tenant data — never service role in routes
5. DataSanitizationShield runs before any data embedding
6. Hard $4K MRR floor enforced at DB constraint level on every product this factory ships
7. 6 retention loops ship before any feature work on any product
8. Haiku for scraping and for the Dispatch/Verdict swarm; Sonnet is the default everywhere else — see Model Routing above, do not swap without new regression testing

---

## Repo Structure

```
kdavis-microsaas-engine/
├── CLAUDE.md                    ← this file
├── MSE-Build-Order.md           ← read this next, work the list
├── api/
│   ├── main.py
│   ├── routers/
│   │   ├── events.py            ✅ complete
│   │   ├── stripe.py            ← GAP 4
│   │   └── research.py         ← GAP 13
├── core/
│   ├── supabase_client.py       ← GAP 11
│   └── config.py
├── agents/
│   ├── orchestrator/            ← GAP 13
│   └── aggregator/              ← GAP 13
├── frontend/                    ✅ Next.js initialized
├── n8n/                         ✅ workflows imported
├── legal/                       ← GAP 12
└── .env                         ← partially filled, see build order for what's missing
```

---

## Session Start Checklist

1. Read this file ✓
2. Read `MSE-Build-Order.md` — find the first unchecked item
3. If the first unchecked item has a "manual step required" flag, surface it to Kelvin and stop
4. Otherwise, start executing
5. After each completed item, output a one-line status: what was built, what file was written, any follow-on requirements

---

# MSE Factory Expansion — Rule Additions (2026-07-17)

## RULE: SEARCH VISIBILITY LAYER (NON-NEGOTIABLE)

Every MSE product ships with SEO, AEO, GEO, and SXO implemented AT LAUNCH.
This is not a post-launch optimization. It is part of the build checklist.
No product goes live without this layer complete.

### SEO (Search Engine Optimization)
- Title tags and meta descriptions on every public page, keyword-matched to the product's top 10 search queries
- Structured data (JSON-LD) on product landing page, pricing page, and FAQ page
- Internal linking between landing → features → FAQ → pricing → trial CTA
- Page speed: Lighthouse performance score ≥ 90 before launch
- Sitemap.xml and robots.txt generated and submitted at deploy

### AEO (Answer Engine Optimization)
- FAQ page is required on every product. Minimum 10 questions. Maximum 3-sentence answers per question.
- Every FAQ item uses FAQPage JSON-LD schema markup
- Questions must map directly to the top objections surfaced by the vertical agent during research
- Answers must be self-contained — readable as a standalone AI snippet without surrounding context
- One "definitive answer" page per product: a long-form page that answers the single most-searched question in the vertical with authoritative depth (minimum 1,200 words)

### GEO (Generative Engine Optimization)
- Product description page uses language that matches how people query AI assistants ("best tool for X", "how to solve Y", "alternative to Z")
- One comparison page per product: "[Product Name] vs [Top Competitor]" — structured for LLM citation
- All public pages use clear, crawlable HTML — no JS-only rendering on SEO-critical content
- Author/company attribution on all content pages (helps LLM trust scoring)

### SXO (Search Experience Optimization)
- Every organic search entry point has a clear above-fold CTA within first scroll
- No dead ends: every page has a next action (trial CTA, FAQ link, or comparison link)
- Trial CTA requires no sales call, no demo request, no form longer than email + password
- Social proof (testimonial or usage stat) visible before the pricing section on landing page
- Mobile-first: all SXO elements verified on 375px viewport before launch

---

## RULE: SEARCH SIGNAL REQUIREMENT FOR VERDICT PASS

Vertical agents must include the following in every research report submitted to Verdict:

```
search_signals:
  top_10_queries: []          # Most searched terms for this problem
  monthly_search_volume: 0    # Estimated combined monthly searches
  content_gap_score: 0-10     # How underserved is existing content (10 = massive gap)

objection_signals:
  top_5_objections: []        # Most common reasons people don't buy in this vertical
  objection_sources: []       # Where objections were found (Reddit, G2, Capterra, etc.)
  competitor_faq_gaps: []     # Questions competitors haven't answered well

geo_signals:
  llm_query_patterns: []      # How people ask AI assistants about this problem
  citation_opportunity: bool  # Does a content gap exist for LLM citation?
```

Verdict CANNOT issue a pass if `top_10_queries` is empty or `monthly_search_volume` is 0.
If search demand cannot be confirmed, Verdict issues a FLAG (not reject) and routes to HITL.

**Status as of 2026-07-17: not yet wired into the aggregator/Verdict gate — the live research swarm's output schema does not yet include `search_signals`/`objection_signals`/`geo_signals`. This is a follow-on build item, not yet done.**

---

## RULE: POST-VERDICT BUILD BRIEF GENERATION

When Verdict issues a PASS on any opportunity (`status = 'READY_TO_BUILD'`), the following must be auto-generated before the opportunity enters the product build queue:

1. `BUILD_BRIEF_CLAUDE_CODE.md` — full Claude Code build prompt
2. `BUILD_BRIEF_CLAUDE_DESIGN.md` — full Claude Design prompt
3. Supabase record inserted into `mse_build_briefs` table with both briefs as JSONB fields
4. CEO dashboard notified via Supabase Realtime (brief appears as clickable card in R&D panel)
5. MSE dashboard Opportunities page updated with new opportunity card + brief preview

Both briefs are generated by the brief_generator agent (`agents/brief_generator.py`).
The brief is visible to: R&D, Technology, Marketing, Operations departments (`visible_to` array on `mse_build_briefs`).
Full brief is accessible via modal/drawer on both MSE and CEO dashboards.

---

## RULE: POST-$4K MONITORING AND INCIDENT RESPONSE AGENT ACTIVATION

### Trigger Conditions
A product's monitoring agent activates when EITHER condition is met:
1. **Revenue trigger**: `mrr_current` ≥ $4,000 AND `mrr_sustained_days` ≥ 30 (checked nightly by n8n cron via `check_monitoring_activation()`)
2. **Manual trigger**: Verdict issues a maturity confirmation AND Kelvin activates via CEO dashboard toggle

### What Activates
Three per-product agent files, created only at activation (never in the shared MSE repo, never before a product has real live MRR data):
- `agents/[product_slug]_monitor.py` — nightly health check, HITL flags, weekly digest
- `agents/[product_slug]_incident.py` — triggered by Monitor flags, structured incident reports
- `agents/[product_slug]_support.py` — customer-facing chat, docs-subdomain knowledge base, escalates below 0.70 confidence

Full system prompts and Supabase table templates (`product_health_metrics`, `incident_log`, `support_tickets` — run in the product's OWN isolated Supabase project at activation, not the shared MSE one) are in `docs/monitoring-agent-suite.md`.

**Sequencing note: there is nothing to wire until a product actually hits this gate — building these agents ahead of a live product with real MRR data has nothing to run against.**

---

## RULE: CUSTOMER-FACING DOCS (docs.[productdomain].com)

Every MSE product ships with a dedicated docs subdomain at launch — not internal documentation, the customer's own reference for everything they can see, do, and troubleshoot. Full content template (every page, every section, content rules) is in `docs/customer-docs-sop-template.md`. Stack: Nextra (preferred) or Mintlify, deployed as a separate Vercel project, CNAME to `docs.[productdomain].com`.

---

## RULE: BRIEF_GENERATOR AGENT

File: `agents/brief_generator.py`
Triggered by: Verdict PASS event (`opportunity_pipeline.status` → `READY_TO_BUILD`), via `POST /factory/generate-brief/{opportunity_id}` → n8n → this agent (mirrors the existing `/factory/build/{id}` HITL-gated trigger pattern).

Responsibilities:
1. Reads the full opportunity + research report
2. Queries `industry_color_map` for the industry-specific palette (matched on `opportunity_pipeline.vertical` — see the real-vertical seed migration, `20260717000012`, since the original spec's seed data used placeholder vertical names that don't match this system's actual 6 research verticals)
3. Generates `BUILD_BRIEF_CLAUDE_CODE.md` and `BUILD_BRIEF_CLAUDE_DESIGN.md`
4. Writes both files to a new GitHub branch: `brief/[product-slug]`
5. Inserts into `mse_build_briefs` (Realtime publication already enabled — the insert itself is what notifies subscribed dashboards, no separate broadcast step needed)

---

## RULE: VERDICT AGENT v5.0 (2026-07-19, complete replacement of v2.0-v4.0)

The aggregator (`agents/aggregator/agent.py`) is the Verdict gate — full rules in `agents/aggregator/prompt.md`. It is not a deterministic Python gate-checker; it genuinely researches every opportunity live via Haiku + Anthropic's server-side `web_search` tool (`core.llm_router.analyze_with_web_search`, requires `anthropic>=0.40`; switched from Sonnet 2026-07-19, see Model Routing above).

**Why v3.0/v4.0 were retired:** 22 real opportunities across v2.0-v4.0 (19 SATURATED, 2 PARTIAL that both failed on MRR math, 0 CLEAR, 0 genuine RESUBMIT) showed the competitor-absence gate (CLEAR/PARTIAL/SATURATED) killed an idea the instant ANY competitor existed, regardless of whether that competitor was serving its users well. v5.0 inverts the model: Dispatch (`agents/orchestrator/agent.py`) anchors every idea on a NAMED existing tool people are already using and complaining about (G2/Capterra/Reddit/forum reviews, 3+ reviews citing the same specific gap), and Verdict asks only whether that tool is failing enough of its users to build a $4K MRR business around the specific gap. There are only three legal verdicts now — `BUILD | CONDITIONAL | DO_NOT_BUILD` — `SATURATED` and `RESUBMIT` are retired entirely; a malformed submission is `DO_NOT_BUILD` with the missing element named as the reason.

**Three-step evaluation:** (1) is the pain still real after the existing tool launched, (2) why is the existing tool failing this ICP — `gap_type` is exactly one of `PRICE_GAP | PLATFORM_GAP | FEATURE_GAP | COMPLEXITY_GAP | SEGMENT_GAP`, (3) does the math clear the price-adjusted floor. `CONDITIONAL` differs from `BUILD` only in timing (floor clears month 8-12 vs. 1-7) — both require the floor to genuinely clear, never a "might clear later" escape hatch. The price-adjusted floor table itself is unchanged since v3.0: $19-29/mo → $3,500, $39-59/mo → $4,000, $69-99/mo → $4,500, $100+/mo → $5,000 — computed independently in code (`agents/aggregator/agent.py`'s `_price_adjusted_floor`) from the model's own `proposed_price`, never trusted from the model's self-report alone.

**Confidence score (added 2026-07-19, same pass as the Haiku switch):** every Verdict output also includes a 0-100 confidence score across 4 components (pain evidence, gap verification, math reliability, GTM realism). A score below 45 forces `DO_NOT_BUILD` regardless of the three-step verdict; 45-59 downgrades a `BUILD` to `CONDITIONAL`; the score can only ever downgrade, never upgrade, and is enforced again at the code level, never trusted from the model's self-report alone — same principle as the floor check.

**v5.0's MRR figure is a flat `verdict_v2_output.net_mrr_floor`** (no more three-scenario nesting from v3.0/v4.0) — `node_write_pipeline` reads it from there first, falling back through the older nested `scenarios.floor.final_mrr_floor` shape and then the legacy top-level key, in that order, in case an older-shaped response ever comes through.

`opportunity_pipeline.human_review_status`/`human_review_comment`/`human_reviewed_by`/`human_reviewed_at` are Kelvin's own approve/reject/comment decision from the dashboard — kept deliberately separate from the agent's own `status`/`verdict_v2_output`. Comparing the two is the tuning signal for future prompt revisions. Do not conflate them into one field.

The MRR floor must never be enforced by inflating a below-floor number up to look like it passed — enforce it only by rejecting. (`node_write_pipeline` did exactly this via `max(mrr, 4000)` until it was found and fixed 2026-07-17 — watch for this pattern recurring anywhere else in the pipeline.) The floor itself is now per-row (`opportunity_pipeline.price_adjusted_floor`, migration 016), not a single hardcoded constant — both the aggregator's own code-level check and the dashboard's approve-route check must read the row's own floor, not assume $4,000.
