# CLAUDE.md — Micro SaaS Engine
**Repo:** `kdavis-microsaas-engine`
**Company:** THD Agentic Systems LLC
**Owner:** Kelvin Davis
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
- **Payments:** Stripe — one dedicated MSE account (THD Agentic Systems LLC) shared across every MSE product, never a separate account per product and never shared with non-MSE products (Cloud Decoded, Decoded Holdings). Each product gets its own Stripe Product + Prices within that one account — see the Stripe Architecture rule below for the exact structure.
- **Domains:** one shared parent domain, `thdstack.com` (registered at Namecheap), every product is a subdomain of it (`[productslug].thdstack.com`) — never its own apex domain. See the Domain Architecture rule below for the exact per-product setup steps.
- **Email:** Resend
- **Languages:** Python, TypeScript, Bash
- **Model routing:** Haiku for high-volume scraping AND for the Dispatch/Verdict research swarm (`agents/orchestrator`, `agents/aggregator` — switched from Sonnet 2026-07-19 as a cost-optimization pass, verified via live regression tests against known cases). Sonnet remains the default everywhere else (brief generation, naming, retention digest, CEO dashboard routes) via `core/llm_router.py`'s `model=` parameter, which defaults to Sonnet — only the swarm agents pass `model=HAIKU` explicitly. Do not change either assignment without new regression testing.

---

## Architecture Rules — Non-Negotiable

1. `tenant_id` on every table — RLS enforced, no exceptions
2. One dedicated Stripe account for all of MSE — never Decoded Holdings, never Cloud Decoded, never a new account per product (see Stripe Architecture rule below)
2a. One shared parent domain (`thdstack.com`) for all of MSE — every product is `[productslug].thdstack.com`, never its own apex domain (see Domain Architecture rule below)
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

## RULE: DOMAIN ARCHITECTURE FOR MSE PRODUCTS (2026-08-09)

**Decision:** every MSE product is a subdomain of the one shared parent domain **`thdstack.com`** — never its own apex domain. `[productslug].thdstack.com` (e.g. `showingsignal.thdstack.com`), not `[productslug].com`. This mirrors the Stripe Architecture rule's shape exactly: one shared account-level resource (there, the Stripe account; here, the parent domain) with per-product isolation underneath it (there, Product+Prices; here, a subdomain + its own Vercel project).

**Registrar and DNS, stated plainly because this got confused once already:** `thdstack.com` is registered at Namecheap. Its nameservers have moved between Namecheap's own (BasicDNS) and Vercel's (`ns1`/`ns2.vercel-dns.com`) at different points — as of Showing Signal's launch (2026-08-08/09) they are back on **Namecheap's own nameservers**, confirmed against three independent resolvers including the authoritative `.com` registry itself, not just Vercel's own dashboard (which had stale cached state claiming otherwise). Always verify current nameservers with `dig +short NS thdstack.com @8.8.8.8` before assuming which side is authoritative — don't trust a cached dashboard view.

**Per-product setup, every time (Showing Signal is the reference implementation):**
1. `vercel project link --project [productslug] --yes` from the product's `frontend/` directory (or `vercel link --project` if the CLI prompts) — creates a dedicated Vercel project per product, not a shared one.
2. Set the product's `NEXT_PUBLIC_*` env vars on that Vercel project (`vercel env add ... production`).
3. `vercel --prod` to deploy.
4. `vercel domains add [productslug].thdstack.com [productslug]` to attach the subdomain to that project.
5. `vercel domains inspect [productslug].thdstack.com` to get the exact record Vercel wants — as of this writing that's a plain `A` record to `76.76.21.21` (since thdstack.com's nameservers are Namecheap's, not Vercel's — if that ever flips back to Vercel's nameservers, no DNS-provider-side record is needed at all, Vercel's own zone handles it automatically).
6. **Owner-only action:** add that `A` record in Namecheap → Domain List → thdstack.com → Advanced DNS, Host = the product slug (e.g. `showingsignal`), Value = `76.76.21.21`. Claude Code has no Namecheap API access and cannot do this step itself.
7. Update the product's backend `ALLOWED_ORIGINS` (CORS) to include both the Vercel-provided `*.vercel.app` URL and the final `[productslug].thdstack.com` domain — the former is live and testable immediately, before DNS propagates.

**What Claude Code never does:** never creates a new apex domain/registration per product, never assumes Vercel's nameservers are authoritative without checking live DNS first, never skips straight to reporting a domain "live" without curling it directly (see kdavis-microsaas-engine/showing-signal's own dated feedback memory on verifying deploys before declaring done).

## RULE: CUSTOMER-FACING DOCS (docs.[productslug].thdstack.com)

Every MSE product ships with a dedicated docs subdomain at launch — not internal documentation, the customer's own reference for everything they can see, do, and troubleshoot. Full content template (every page, every section, content rules) is in `docs/customer-docs-sop-template.md`. Stack: Nextra (preferred) or Mintlify, deployed as a separate Vercel project, following the same Domain Architecture rule above: `docs.[productslug].thdstack.com`, not a bare `docs.[productslug].com` (superseded 2026-08-09 — see the Domain Architecture rule for why every MSE product sits under the shared `thdstack.com` parent instead of its own apex domain).

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

**Confidence score (added 2026-07-19, same pass as the Haiku switch):** every Verdict output also includes a 0-100 confidence score across 4 components (pain evidence, gap verification, math reliability, GTM realism). As of 2026-07-20 this score no longer maps to the model's own BUILD/CONDITIONAL/DO_NOT_BUILD verdict at all — see the RULE below, which replaced the original 45/60 override thresholds with a stricter, purely code-level classification. The model's own verdict label is informational context only; it is never authoritative for `status`, same "never trust the model's self-report alone" principle as the floor check, just carried one step further.

**v5.0's MRR figure is a flat `verdict_v2_output.net_mrr_floor`** (no more three-scenario nesting from v3.0/v4.0) — `node_write_pipeline` reads it from there first, falling back through the older nested `scenarios.floor.final_mrr_floor` shape and then the legacy top-level key, in that order, in case an older-shaped response ever comes through.

`opportunity_pipeline.human_review_status`/`human_review_comment`/`human_reviewed_by`/`human_reviewed_at` are Kelvin's own approve/reject/comment decision from the dashboard — kept deliberately separate from the agent's own `status`/`verdict_v2_output`. Comparing the two is the tuning signal for future prompt revisions. Do not conflate them into one field.

The MRR floor must never be enforced by inflating a below-floor number up to look like it passed — enforce it only by rejecting. (`node_write_pipeline` did exactly this via `max(mrr, 4000)` until it was found and fixed 2026-07-17 — watch for this pattern recurring anywhere else in the pipeline.) The floor itself is now per-row (`opportunity_pipeline.price_adjusted_floor`, migration 016), not a single hardcoded constant — both the aggregator's own code-level check and the dashboard's approve-route check must read the row's own floor, not assume $4,000.

---

## RULE: HARD MRR GATE — ONLY THREE STATES REACH THE DASHBOARD (2026-07-20)

Kelvin's diagnosis: obvious misses were reaching the dashboard and wasting review time, and tokens were being wasted running full Verdict web-search calls on ideas that never had a chance. Two consecutive real Haiku batches landed at 1/15 (6.7%) BUILD/CONDITIONAL, and a large share of those 15 were single-digit-to-low-hundreds MRR ceilings that should never have consumed a review click.

**Two gates, in `agents/aggregator/agent.py`:**

1. **Pre-Verdict prefilter (`_prefilter_reject`)** — before spending a Verdict web-search call, checks Dispatch's own self-reported `conservative_mrr_potential`. If even Dispatch's own (most favorable) number can't clear the **$3,500 absolute floor**, the submission is killed before the LLM call ever runs.
2. **Post-Verdict dashboard-visibility gate (in `_evaluate`)** — after Verdict's independently-researched `net_mrr_floor` and `confidence_score` come back, exactly one of four outcomes applies, purely numerically, regardless of what the model's own verdict/reason said:
   - `net_mrr_floor < $3,500` → `killed_below_floor`. Never inserted into `opportunity_pipeline`. `node_write_pipeline` diverts it straight to `opportunity_pipeline_rejections` (the same archive table the manual reject-button flow already uses) — full reasoning preserved for tuning, zero dashboard/review cost.
   - `net_mrr_floor >= price_adjusted_floor` (this row's own tier floor, e.g. $5,000 for a $100+/mo idea — **not** a flat $4,000) **and** `confidence_score >= 75` → `READY_TO_BUILD`.
   - `net_mrr_floor` in `[$3,500, $4,000)` (the literal absolute band, not tier-adjusted) **or** `confidence_score` in `[65, 74]` → `validated` (CONDITIONAL) — the judgment-call band.
   - Anything else that still clears $3,500 (failed its own tier floor, confidence below 65, or the model itself said DO_NOT_BUILD for a qualitative reason despite the money clearing) → `watch`. This is the one genuinely new status Dispatch/Verdict now produce live — the DB column already supported it (migration 002) but nothing wrote to it until now. The specific risk is always written into `rejection_reason` so it's visible on the dashboard, not just buried in `verdict_v2_output`.

**`$3,500` is not a guessed number** — it's `_PRICE_TIER_FLOORS`' own lowest tier floor (the cheapest price band this factory ever builds for), reused as the universal minimum viability bar. Nothing below what even the cheapest tier would require is worth a dashboard row.

**Applied retroactively 2026-07-20:** 15 pre-existing rows below $3,500 (the real-batch results already logged in `MSE-Build-Order.md`) were archived to `opportunity_pipeline_rejections` and deleted from `opportunity_pipeline`. Two READY_TO_BUILD opportunities that predated the confidence-score system (no recorded score, so unverifiable against the new `>=75` bar) were re-run through a real Verdict call rather than guessed at: "Campaign Aware Replenishment" (Shopify) came back confidence 61 → `watch`; "Ninety Nine Comply" (contractor 1099 compliance) came back `net_mrr_floor: None`/confidence 20 → `killed_below_floor`, deleted (its `mse_build_briefs` row survives with `opportunity_id` set to `NULL` via the existing `ON DELETE SET NULL` FK — the brief content isn't destroyed, just decoupled from a now-gone opportunity).

**Prompt-side companion rule (`agents/orchestrator/prompt.md`, same date):** before scoring any opportunity, Dispatch must answer three questions internally — who is the exact buyer, what specific manual workflow is being replaced, and why the incumbent hasn't shipped this natively (naming one of: regulatory complexity, different customer segment, technical architecture constraint, or intentional product decision). If the third question can't be answered with a specific structural reason, the idea is discarded before it reaches Verdict at all. This targets the same root cause as the MRR gate from the other direction — category-level ideas without a durable, named reason for the gap's existence are exactly what's been dying on Verdict's math checks.

---

## RULE: PIPELINE HEALTH AUTO-RECALIBRATION (2026-08-15)

Formalizes and automates what the HARD MRR GATE rule above left as a narrative target ("20%+ BUILD+CONDITIONAL across a rolling 10, tracked in `MSE-Build-Order.md`, not enforced in any prompt"). Verdict and Dispatch are both stateless per-call LLM invocations with no memory of prior submissions — `agents/aggregator/pipeline_health.py` is that memory now, and it runs automatically, not just when someone happens to notice the rate is low.

**Two new Step 2.5 hard-stop checks, `agents/aggregator/prompt.md`:** every opportunity whose solution concept depends on integrating with a third-party platform's API must clear three checks before Step 3's math — `THIRD_PARTY_APPROVAL_GATE` (does shipping require platform-owner approval that could block launch), `MID_ACQUISITION_PLATFORM` (is the target platform currently being acquired/in ownership transition), `API_CANNOT_PERFORM_CORE_ACTION` (does the platform's current API actually support the action the concept needs). **These three are permanent — never loosened, by any recalibration, for any reason.** Enforced twice: the model self-reports via new `verdict_v2_output` boolean fields, and `agents/aggregator/agent.py`'s `_evaluate()` independently re-checks those fields and forces `status = "rejected"` regardless of what the money said, same "never trust the model's self-report alone" principle as the MRR floor check. A soft, fourth field (`integration_dependency_count`) is tracked alongside these but is *not* a hard stop — see recalibration below.

**The monitor, `agents/aggregator/pipeline_health.py`:** after every research run (new `check_pipeline_health` node in `agents/orchestrator/agent.py`'s LangGraph, between `write_pipeline` and `summarize`), computes the real rolling-10-submission build rate from `opportunity_pipeline` + `opportunity_pipeline_rejections` merged by timestamp. If that rate is below **10%**:

1. First checks whether the *research pool* looks exhausted (fewer real submissions than the 10-window, or the same handful of anchor tools repeating) — if so, that's the trigger regardless of rejection-reason breakdown.
2. Otherwise, computes the rejection-reason distribution across the window (`MRR_FLOOR | INTEGRATION_DEPENDENCY | API_CAPABILITY | THIRD_PARTY_APPROVAL_GATE | MID_ACQUISITION_PLATFORM | PAIN_NOT_CONFIRMED | GAP_NOT_IDENTIFIED | OTHER`, attributed via Verdict's own sequential step structure — whichever step failed first). A category must be killing **over 40%** of the window to count as dominant.

**Defined recalibrations (auto-applied, logged, never requiring a HITL click — Kelvin's explicit choice for this rule):**
- `API_CAPABILITY` dominant → Dispatch is instructed to expand research toward "read-only reporting" concepts that don't need write/action capability the target API lacks. Does not loosen the hard stop — stops feeding Verdict ideas that predictably fail it.
- `INTEGRATION_DEPENDENCY` dominant → Verdict's soft criterion recalibrates: one required integration to a widely-adopted platform (Stripe, Gmail, Slack, or equivalent) becomes CONDITIONAL instead of DO_NOT_BUILD. Two+ integrations, or one to a niche platform, still DO_NOT_BUILD. Never touches the three hard stops above.
- `MRR_FLOOR` dominant → Verdict is instructed to research current market pricing more thoroughly before rejecting on price alone. Does not loosen the $3,500 absolute floor or any price-adjusted floor value — improves the research feeding into that unchanged floor.
- Research pool exhausted → Dispatch is instructed to add Reddit (elevated beyond its current "Secondary" framing), AppSumo, and job-posting sources for that run — an explicit, temporary exception to "search ONLY these sources."
- `THIRD_PARTY_APPROVAL_GATE` and `MID_ACQUISITION_PLATFORM` dominant → **no action defined, by design.** Logged to `mse_pipeline_recalibrations` (`target='none'`) for visibility only. These are two of the three permanent hard stops named above — this rule does not let pipeline health pressure ever loosen them, even indirectly.

**Mechanism, not a self-modifying prompt file:** a recalibration is a row in `mse_pipeline_recalibrations` (migration `20260815000026`), not an edit to `prompt.md` on disk. `agents/aggregator/agent.py` and `agents/orchestrator/agent.py` each read the active row for their side (`target='verdict'` / `target='dispatch'`) via `get_active_recalibration_text()` and prepend it to that call's system prompt at runtime — same "soft input, never a gate" treatment as the existing RAG-context block, so a DB read failure never blocks a real evaluation. Fully reversible (deactivate the row), fully logged (the row itself is the log), no redeploy needed either direction. A fresh trigger for the same target replaces whatever was previously active there — never stacks.

---

## RULE: STRIPE ARCHITECTURE FOR MSE PRODUCTS (2026-08-06)

**Decision:** all MSE products share the one dedicated MSE Stripe account (THD Agentic Systems LLC, workspace "Micro Saas Decoded", `acct_1TpLcKLIpoJRr7Tc`, created 2026-07-20). Each product gets its own isolated Products and Price IDs within that account. Revenue consolidates into the existing bank account. No new Stripe accounts are created per product — this was already the standing rule (see Architecture Rules #2 above); this section formalizes the concrete object structure and naming convention every product must follow.

**Stripe object structure per product:**

```
Stripe Account (THD Agentic Systems LLC)
├── Product: Showing Signal
│   ├── Price: Solo Agent        — $97/mo   — price_showingsignal_solo
│   ├── Price: Independent Team  — $197/mo  — price_showingsignal_team
│   └── Price: Brokerage         — $397/mo  — price_showingsignal_brokerage
├── Product: [Next MSE Product]
│   ├── Price: [Tier 1]
│   └── Price: [Tier 2]
└── ...
```

**Naming convention, every MSE product:**
- Stripe Product name: `[Product Name]` — human-readable, appears on receipts
- Stripe Price lookup key: `[productslug]_[tier]` — machine-readable, used in env config
- Stripe metadata on every Product: `{ "mse_product": "[productslug]", "entity": "THD Agentic Systems LLC" }`

**What Claude Code does per product, at scaffold time** — add to the product's `.env` and Railway config:

```bash
STRIPE_SECRET_KEY=sk_live_...          # shared — same key across all MSE products
STRIPE_WEBHOOK_SECRET=whsec_...        # product-specific — one webhook endpoint per product
STRIPE_PRICE_SOLO=price_...            # product-specific Price ID (tier name varies per product)
STRIPE_PRICE_TEAM=price_...            # product-specific Price ID
STRIPE_PRICE_BROKERAGE=price_...       # product-specific Price ID (if 3-tier)
```

`STRIPE_SECRET_KEY` is the same value across every MSE product — Claude Code never generates a new one, it receives it from the environment and uses it. `STRIPE_WEBHOOK_SECRET` is product-specific because each product registers its own webhook endpoint in Stripe (`https://api.[productdomain].com/billing/webhook`) — Stripe generates a unique `whsec_` per registered endpoint. Env var suffixes (`_SOLO`/`_TEAM`/`_BROKERAGE` etc.) follow that product's own real tier names, not a fixed generic list — `core/plans.py`'s `stripe_price_id()` pattern (build `STRIPE_PRICE_{TIER}` from the tier name) is the reference implementation, first applied in Showing Signal.

**What Claude Code never does:**
- Never creates a new Stripe account
- Never creates a new bank account or payout destination
- Never stores raw Stripe keys in source code or committed files
- Never shares a Supabase project between products — Stripe consolidation does not change Supabase isolation (unchanged, see Architecture Rules #1)
- Never hardcodes Price IDs — always reads from environment variables

**Supabase rule — unchanged:** each MSE product still gets its own isolated Supabase project regardless of Stripe consolidation. RLS policies and JWT hooks are project-scoped (cross-product leakage risk if shared), and billing state/tenant rows/event logs are product-specific with no shared schema. One Stripe account, one Supabase project per product, always.

**Owner-only blocking action, per product:** creating the Product + Price objects in the Stripe dashboard (or Claude Code doing it via API in the setup script, when explicitly authorized) and registering the webhook endpoint — Claude Code does not create Stripe accounts or generate live secret keys/Price IDs on its own initiative, per the existing "no autonomous outbound" / HITL design (see `MSE-Build-Order.md`). Once the owner drops the Price IDs and webhook secret into the product's env config, Claude Code wires the checkout session creation endpoint and the `checkout.session.completed`/subscription-lifecycle webhook handler.

**Showing Signal is the reference implementation of this pattern** (2026-08-06): Product "Showing Signal", tiers `solo`/`team`/`brokerage` mapping to Solo Agent $97/mo, Independent Team $197/mo, Brokerage $397/mo, lookup keys `showingsignal_solo`/`showingsignal_team`/`showingsignal_brokerage`, webhook at `/billing/webhook`. The three Price IDs and the webhook secret are the only owner-only blocking action remaining on Showing Signal's Stripe side — see `showing-signal/CLAUDE.md`'s Build Status section.
