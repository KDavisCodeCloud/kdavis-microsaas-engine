# Micro SaaS Engine — Build Order
**Project:** `kdavis-microsaas-engine`
**Company:** THD Agentic Systems LLC
**Last updated:** 2026-07-17
**Status:** Factory pipeline complete and tested. No product has been run through it end-to-end yet — that is the critical path to launch, not further building.

---

## What This Is

A research-validated, retention-first software factory producing 1–2 micro-SaaS products per month across 6 research verticals (Healthcare/Medical Front Desk, Legal/Professional Services, E-commerce/Retail Ops, Real Estate/Property Management, HR/Ops/People Management, Finance/Accounting/Bookkeeping). Primary purpose right now: generate low-ticket subscription revenue ($29–$79/mo) fast enough to cover the operating stack before Cloud Decoded closes its first B2B deal. Every product MSE ships has a hard $4K MRR floor enforced at the DB constraint level and 6 retention loops shipping before any feature work.

**Self-funding milestone:** 6 MSE clients at blended pricing covers the full operating stack.

---

## Completed — Do Not Redo

**Core infrastructure**
- FastAPI backend, JWT auth via `get_supabase_for_request(jwt)`, RLS on every table
- Next.js 15 dashboard — Overview, Research Swarm, Opportunities (renamed from "Pipeline" 2026-07-17), Outreach, Agents, Retention
- n8n self-hosted, workflows active
- Legal docs (EULA, privacy policy, DPA template)
- Test harness — `tests/conftest.py` fake-Supabase fixtures, 94 tests passing

**Verdict Agent v2.0 (shipped 2026-07-17, consolidated from an 8-model audit)**
- Replaces the old aggregator, which was a pure deterministic Python gate-checker that never actually called an LLM at all (`prompt.md` was loaded but never used anywhere — confirmed dead code) and just trust-checked whatever `competition_density`/`stack_compatible`/`build_confidence_score` labels the upstream vertical agent self-assigned, with zero independent verification
- Now genuinely researches every opportunity live via Sonnet + Anthropic's server-side `web_search` tool (`core.llm_router.analyze_with_web_search`) — hard-gates on independently-verified competitor discovery before any MRR math runs, normalizes pricing across per-user/per-firm/annual/free-tier comps, builds TAM from named sources with real funnel logic, and computes its own MRR floor rather than trusting the vertical agent's self-report
- Required bumping `anthropic` 0.36.0 → 0.116.0 (web_search tool needs ≥0.40)
- New `opportunity_pipeline` columns: `verdict_v2_output` (full structured reasoning, nothing lost), `human_review_status`/`human_review_comment`/`human_reviewed_by`/`human_reviewed_at` (Kelvin's per-opportunity approve/reject/comment — see below)
- Fixed two real bugs found while wiring this in: (1) `node_write_pipeline` was silently skipping every rejected opportunity — the dashboard has had a "rejected" filter tab since it was built, but it's always been empty because nothing rejected ever got a row; (2) `"conservative_mrr_potential": max(mrr, 4000)` was artificially inflating a below-floor MRR number to look like it cleared $4,000 — exactly the "floor inflation" failure mode the v2.0 audit exists to eliminate. The floor is now enforced only by rejecting, never by lying about the number.
- Dashboard: every opportunity card now has Approve/Reject buttons + a comment field (`POST /pipeline/{id}/review`, admin-gated, audit-logged) — deliberately stored SEPARATE from the agent's own verdict, so comparing the two is the actual tuning signal for prompt v2.1+
- 25 new tests (aggregator gating + regression cases from the audit's own table, `node_write_pipeline` fixes, review route)

**Verdict Agent v3.0 (shipped 2026-07-18, supersedes v2.0)**
- v2.0 ran 6 real opportunities through the live tuning process (4 in one batch, 2 in another) and got 6 straight rejections — 0 BUILD, 0 CONDITIONAL, 0 RESUBMIT. Kelvin's read: "the gates are too tight." Given new v3.0 rules to fix it.
- **Three-state competitor gate** replaces binary CLEAR/EXISTS: `CLEAR` (no competitor), `PARTIAL` (a competitor exists but only serves users on some other broader platform — a real standalone segment can still BUILD with an approved differentiation thesis, narrowing TAM to that segment), `SATURATED` (a real standalone competitor — still a hard halt, same as v2.0's old behavior). This is the main loosening — several of the 6 v2.0 rejections (e.g. onboarding tools blocked by full-HRIS competitors) look like they'd plausibly reclassify as PARTIAL under v3.0, not SATURATED.
- **Price-adjusted MRR floor** replaces the flat $4,000: $19-29/mo → $3,500, $39-59/mo → $4,000, $69-99/mo → $4,500, $100+/mo → $5,000. Computed independently in code from the model's own `proposed_price` (`agents/aggregator/agent.py`'s `_price_adjusted_floor`), never trusted from the model's self-report alone — same principle as the old $4K code-level check. New `opportunity_pipeline.price_adjusted_floor` column (migration 016) so the DB's `mrr_floor_check` constraint enforces the right number per-row instead of one hardcoded value; the dashboard's approve-route check reads the same per-row column.
- Adds a required three-scenario MRR breakdown (FLOOR/BASE/STRETCH, each with its own capture rate range) and a time-to-floor classification (1-6mo STRONG, 7-12mo PASS, 13-18mo CONDITIONAL w/ named distribution partner, 19+mo FAIL) — `RESUBMIT` verdict unchanged from v2.0, still maps to `needs_correction`, not `rejected`.
- **Real bug found wiring this in:** v3.0 nests the MRR figure under `scenarios.floor.final_mrr_floor` instead of v2.0's top-level `final_mrr_floor` — `node_write_pipeline` still read the old top-level key, which is always `None` on a v3.0 result, silently falling through to the unverified upstream vertical-agent number on every single v3.0 opportunity. Caught before the first real v3.0 run, not after.
- 12 new/updated tests (price-tier boundaries, PARTIAL-can-BUILD, the nested-floor bug, approve-route price-adjusted-floor checks), 161 passing overall.
- **Not yet done: running real opportunities through v3.0.** Same caveat as v2.0 — canned-response tests only prove the Python-side contract, not live research quality. Kelvin's plan: run 2 more once this is deployed, to see whether the loosened gate actually produces a BUILD/CONDITIONAL/RESUBMIT this time.

**Research → Verdict pipeline**
- Orchestrator + aggregator agents (LangGraph), 7 quality gates, $4K MRR floor enforced at DB + gate level
- All 6 vertical intel agents built (`agents/{healthcare,legal,ecommerce,realestate,hr-ops,finance}-intel/`) — confirmed producing real opportunities in a live swarm run
- `opportunity_pipeline` table — full status flow

**Build/deploy pipeline (`agents/factory/`)**
- `scaffold_generator.py` → `provision_supabase.py` → `provision_stripe.py` → `deploy.py` → `build_pipeline.py`
- HITL-gated via admin-only `POST /factory/build/{opportunity_id}` — requires a named human (`triggered_by`), never fires automatically
- Deploys to Railway (backend) + Vercel (frontend), `thdstack.com` wildcard subdomains

**Outreach**
- Apollo.io lead sourcing (live API key) + Systeme.io (live API key)
- LinkedIn outreach is manual-DM-only by design — no auto-send, HITL queue routes every cold DM to a human
- Bold amber HITL disclaimer + "MANUAL SEND" badge on every LinkedIn lead row in `/outreach` — deployed live 2026-07-17

**Factory Expansion (shipped 2026-07-17)**
- `industry_color_map`, `mse_build_briefs`, `mse_monitoring_events` tables + activation functions — migrations `20260717000011`/`000012`, live and verified
- `agents/factory/brief_generator.py` — auto-generates both build briefs on Verdict PASS, HITL-gated via `POST /factory/generate-brief/{opportunity_id}`, 13 tests passing
- MSE dashboard Opportunities page — new "Build Briefs" section, click-to-expand brief viewer
- `docs/monitoring-agent-suite.md` + `docs/customer-docs-sop-template.md` — reference templates for later use
- CLAUDE.md — search-visibility, brief-generation, and monitoring-activation rules added

---

## Active — What's Actually Left to Go Live

### 1. First opportunity is through research → Verdict → brief. Build/deploy is next (CRITICAL PATH)
Ran for real 2026-07-17 against the top-scoring `READY_TO_BUILD` opportunity — a contractor payment compliance tool (1099-NEC), vertical HR/Ops, Verdict score 82, conservative MRR $5,400/mo. `brief_generator` produced both briefs, branch `brief/ninety-nine-comply`, `mse_build_briefs` row `31705c98-747a-4165-ba9f-f256da603d6e`, status `pending_review`. **Three real bugs only surfaced by actually running it** (all fixed same session, see git log): `opportunity_pipeline.solution_concept` is a full sentence with no dedicated name field, which crashed the git branch name (`_slugify` uncapped + no LLM-derived short name → fixed via `core/naming.py` + a length cap in both `brief_generator.py` and `scaffold_generator.py`); the LLM naming call didn't reliably follow "respond with only the name" (fixed: take first line, hard cap); `git push` from a script has no ambient credentials the way an interactive session does — nothing else in this repo ever pushed to git from running code, so this was a genuinely new gap, not a previously-solved one (fixed: `GITHUB_TOKEN` via inline auth header, never a global git config rewrite).

**Manual (Kelvin), next concrete step:** review the generated brief on branch `brief/ninety-nine-comply` (or in the dashboard's new Build Briefs section) and, if approved, trigger the actual build via `POST /factory/build/{opportunity_id}` — which requires providing a Stripe secret key for this product's dedicated account (a hard, deliberate gate: Claude Code does not create Stripe accounts or generate live secret keys — that is Kelvin's action alone, per this pipeline's existing HITL design). Creating the dedicated MSE Stripe account happens at this step, not before.

### 1a. Systemic bug found and fixed 2026-07-17: every dashboard button that mutates data was broken in a live browser
Kelvin reported the reject button doing nothing but "failed to fetch". Root cause: `tenant_context_middleware` had no exemption for CORS preflight (`OPTIONS`) requests, and — because `CORSMiddleware` is registered *before* it in `main.py`, which makes it the *inner* layer at runtime (Starlette prepends each added middleware, so whichever is added last wraps all the earlier ones) — every preflight hit the auth check first and got rejected with 401 before CORS ever got a chance to answer it. The browser reports that to JavaScript as a generic network failure, not a clean error. Confirmed via live Railway logs (`OPTIONS /pipeline/{id}/review` → 401) and fixed by exempting `OPTIONS` from auth in `tenant_context_middleware`.

**This affected every authenticated POST/PATCH/DELETE endpoint called from a browser** — not just reject/approve, but almost certainly the "Build This Product" trigger and brief-generation trigger too, since all of today's "real" testing of those went through direct Python invocation or FastAPI's `TestClient` (which doesn't simulate real browser CORS preflight), never an actual browser click.

**That first fix was necessary but not sufficient** — Kelvin clicked reject again after it deployed and still got "Failed to fetch". Railway logs showed why: the OPTIONS preflight now correctly returned 200, but the *actual* POST that follows it came back `401` — and that 401 carried no CORS headers either, for the same underlying reason. `CORSMiddleware` was registered in `main.py` *before* `tenant_context_middleware`, which (Starlette prepends each added middleware, so the one added last ends up outermost) made `tenant_context_middleware` the outer layer and CORSMiddleware the inner one. Any real request the auth middleware rejected — not just an OPTIONS preflight, any genuine 401 or 403 — returned its error response directly without ever calling `call_next`, so it bypassed CORSMiddleware too. A browser can't read a cross-origin response missing CORS headers regardless of status code, so this looked identical to the first bug from the browser's side.

**Real fix:** reordered `main.py` so `CORSMiddleware` is registered last (outermost), wrapping every response — success or error. Verified locally and against live production (`mse-api` on Railway) that both a real 401 and a real 403 now carry the correct `Access-Control-Allow-Origin` header, not just the OPTIONS preflight.

**Third pass — the actual root cause of the auth failure itself:** this fix made the browser able to finally *see* the real error, which was: `"The specified alg value is not allowed"`. `verify_jwt` (`api/middleware/auth.py`) hardcoded `algorithms=["HS256"]` against `SUPABASE_JWT_SECRET` — but this Supabase project has migrated to the newer asymmetric JWT signing keys model, confirmed live by querying this project's own `/auth/v1/.well-known/jwks.json`, which returns an ES256 EC key, not a shared secret. Every prior "real" verification this session used synthetic HS256 test tokens, so this was never exercised until an actual browser session (with a genuine Supabase-issued token) hit it. Fixed: `verify_jwt` now dispatches on the token's own declared `alg` (read from its unverified header) — HS256 still uses the legacy shared-secret path unchanged, anything else fetches the matching key from the real JWKS via `PyJWT`'s `PyJWKClient` (added `cryptography` to `requirements.txt`, needed for ES256 and previously only present transitively). Confirmed `PyJWKClient` successfully fetches this project's real live JWKS before deploying. 6 new tests, including real ES256 verification against a generated EC key pair.

Deployed to Railway (`mse-api`, deployment `d0885c2f`, confirmed SUCCESS). **This is the one that should actually make the buttons work — please try again.**

### 2. Run 7 more opportunities through Verdict v2.0 to tune it
Kelvin's plan, stated 2026-07-17: run 7 more products through the new research-backed Verdict agent, using the dashboard's new Approve/Reject/comment buttons to build the tuning signal — Kelvin expects to reject all but 1. This is a real-money action (web search is $10/1,000 searches + Sonnet tokens per opportunity, run live, not simulated) — waiting for an explicit go before firing these off rather than running them automatically.

### 2a. Verdict v3.0 tuning — calibration status: NOT complete, don't declare it live-ready on SATURATED volume alone
**Rule (Kelvin, 2026-07-18): the swarm does not go live until each of the four possible verdict states — CLEAR, PARTIAL, SATURATED, RESUBMIT — has at least one real, confirmed pass under v3.0.** Repeatedly re-confirming SATURATED is not progress toward the other three; a gate that only ever fires one branch is unvalidated on every other branch no matter how many times it's re-run.

Status as of 2026-07-18:
- **SATURATED → DO_NOT_BUILD:** ✅ confirmed repeatedly (15/16 real opportunities now — 6 v2.0, 6 v3.0, 3 v4.0)
- **CLEAR → BUILD:** ❌ not yet confirmed — never seen in a real run
- **PARTIAL → BUILD or CONDITIONAL (approved differentiation thesis):** ⚠️ partially confirmed 2026-07-18 — first real `competitor_state: PARTIAL` seen under v4.0 (a PM-tool/time-tracking approval-layer idea, correctly identified as platform-locked-only with a real standalone segment). But verdict was still `DO_NOT_BUILD`, not BUILD/CONDITIONAL — it failed independently on the MRR floor (Floor/Base scenarios both missed the $4,450 adjusted floor within 24 months). This is a real, useful signal: it proves the competitor gate and the MRR-math gate operate independently rather than PARTIAL always being a rubber stamp toward BUILD — but it does NOT satisfy the standing requirement, since PARTIAL never actually reached a BUILD/CONDITIONAL outcome.
- **RESUBMIT → needs_correction:** ❌ not yet confirmed *under v3.0 or v4.0* (a v2.0-era RESUBMIT was seen once, on different rules/schema — doesn't count)

**Still 0/16 real evaluations have produced BUILD, CONDITIONAL, or RESUBMIT.** The standing rule from 2026-07-18 stands: don't call the swarm validated until each of the four states has a confirmed pass, and a PARTIAL that dead-ends at DO_NOT_BUILD via a failed MRR gate doesn't count as confirming the PARTIAL→BUILD path.

Every real batch so far has organically proposed ideas in already-commoditized top-level categories (appointment scheduling, employee onboarding), which is why SATURATED keeps firing — this is a research/dispatch signal, not a Verdict-gate bug. Added a "NICHE TARGETING DIRECTIVE" to `agents/orchestrator/prompt.md` 2026-07-18 to push Dispatch toward genuinely underserved sub-segments.

**Niche-targeting batch result (2026-07-18):** the directive worked as a research-quality fix — this batch's 6 raw findings were genuinely narrower than any prior batch (USCIS bulk case-status monitoring for immigration paralegals, multi-marketplace payout reconciliation for e-commerce bookkeepers, K-1 basis tracking via OCR, restaurant POS prime-cost automation — real sub-segments, not top-level categories). But the 2 picked for Verdict still both came back SATURATED, this time against equally-specific real competitors (CaseTracker.io for the USCIS tool; A2X/Link My Books for the reconciliation tool — both live, priced, reviewed products serving that exact sub-segment). **Niche-within-niche targeting alone will not reliably surface CLEAR/PARTIAL/RESUBMIT** — genuinely uncontested micro-SaaS niches are rare enough that organic research keeps finding real incumbents even one level deeper.

**Planning implication — next batch should deliberately construct test inputs, not run more organic research:** e.g. a genuinely uncontested micro-niche (very recent regulatory change, a brand-new platform with no ecosystem yet) for CLEAR; a segment served only by a broader adjacent-platform competitor (the kind v3.0's PARTIAL state was specifically designed for — re-examine the 6 v2.0 rejections for one that fits, e.g. the onboarding tool blocked by full-HRIS competitors) for PARTIAL; an opportunity with a deliberately weak/incomplete MRR calculation or missing evidence for RESUBMIT (this one doesn't even need new research — a hand-crafted malformed input should trigger it). Update this checklist every time a batch runs, regardless of outcome.

**v4.0 cross-industry framework batches (2026-07-18):** ran 2 batches under the new v4.0 rules (`agents/orchestrator/prompt.md` + `agents/aggregator/prompt.md` fully rewritten to the cross-industry sourcing model — 4 frameworks: Universal Business Obligation, Tool Pair Gap, Underserved Buyer Role, Recent Regulatory Change). Each batch generated 1 idea per framework, picked the top 2 by build confidence, ran them through Verdict v4.0. **Real bug found and fixed first:** the more detailed v4.0 reasoning (cross-industry check, regulatory burden flag) ran the first Verdict call out of its 8,192-token response budget mid-Step-2, before ever reaching the closing JSON contract — bumped `core.llm_router.analyze_with_web_search`'s default `max_tokens` to 16,000 (`0c12d65`), confirmed fixed on retry.

Results: contractor W-9/COI document vault → SATURATED (getW9, TrustLayer, HoundDog); PM-tool/time-tracking approval layer → **PARTIAL** (first non-SATURATED competitor_state seen in the whole project) but still DO_NOT_BUILD on a failed MRR floor gate, not the competitor gate; scheduling-tool deposit-enforcement connector → SATURATED (Calendly/Acuity/Square all have this natively); vendor/license renewal tracker → SATURATED (Termedora, Contract Hound, ExpiryEdge). See the checklist above for what this does and doesn't confirm.

**Founder-domain + accountant-pre-authorization batch (2026-07-18):** ran Legal/Professional Services plus a directed dispatch specifically at IDEA SOURCING DIRECTIVE Source 2 (accountant pre-authorization workflows), rather than generic Finance research. Raw findings included a genuine `COMPETITOR_GAP`-style hit (Legal: a Clio/MyCase-connected pre-bill narrative expander) and two direct accountant-pre-authorization ideas. Picked: a court scheduling-order PDF parser (Legal) and a bookkeeper client-authorization portal for check runs (Finance). **Both still SATURATED** — the scheduling-order parser against CourtSync Calendar and LawToolBox AI ($49/user/mo); the pre-authorization portal against ApprovalMax ($54–121/org/mo, 618 reviews, 82% five-star, dedicated bookkeeper partner program) and Plooto ($32–59/mo). Notably ApprovalMax's rating profile is well above the 4.2-star PARTIAL/SATURATED cutoff in the new PARTIAL COMPETITOR STRATEGY rule — this is a case where the gate's SATURATED call looks correct on the merits (a strong, well-loved incumbent), not a research-quality miss. This is now **12/12 real evaluations landing on SATURATED**, including a deliberately-sourced founder-domain idea — reinforcing that hand-constructed test inputs (not just better-sourced organic ideas) are the remaining path to a confirmed CLEAR/PARTIAL/RESUBMIT.

### 3. LinkedIn + Canva — both need one manual step in an external console today
**LinkedIn (root cause found, not yet fixed):** the internal (owner) OAuth flow's redirect URI (`https://theclouddecoded.com/api/v1/internal/marketing/connect/callback/linkedin`) was likely never registered on the LinkedIn Developer App — only the customer-facing redirect URI was ever documented anywhere (`.env.example` fixed 2026-07-17 to document both). **Manual (Kelvin), 2 minutes:** developers.linkedin.com → your app → Auth tab → Authorized redirect URLs → add that exact URL (https, no trailing slash) → click "Connect LinkedIn" again and report the result.

**Canva (code built, needs setup before it can run):** OAuth (PKCE) connect/callback flow + Autofill API client shipped 2026-07-17 in `kdavis-agentic-platform` (`core/publishers/canva.py`, `api/routes/internal_marketing.py`, migration `012_internal_canva_connection.sql` — applied live). Cannot be tested until Kelvin does three things, none of which Claude Code can do: (1) create a Canva Developer account + "External Application" at canva.com/developers, get `CANVA_CLIENT_ID`/`CANVA_CLIENT_SECRET`, add both to `.env`; (2) register the redirect URI `{API_BASE_URL}/api/v1/internal/marketing/connect/callback/canva` on that app; (3) build at least one Brand Template by hand in Canva's own editor with named autofill placeholder fields (the Autofill API only fills existing templates, it does not generate designs from scratch) — and confirm the Canva plan tier actually includes Connect/Autofill API access, since that's sometimes gated to paid tiers.

### 4. Cross-dashboard "agent last ran" correlation
Flagged gap: MSE, CEO, and DecodedSix dashboards don't clearly correlate which agent ran when — DecodedSix's Agents tab was showing "never run" for agents that had in fact run. Needs a real fix, not just DecodedSix-specific — same gap likely exists across all three dashboards since they share the underlying event-emission pattern.

### 5. CEO dashboard cross-repo wiring
The Build Briefs section shipped on the MSE dashboard this session. The equivalent (brief cards + monitoring health cards once a product goes live) still needs to be wired into the CEO dashboard's R&D department view — that's a separate repo (`kdavis-agentic-platform`), not started.

### 6. Monitoring/Incident/Support agent trio — deferred by design, not a gap
Do not build `agents/[slug]_monitor.py` / `_incident.py` / `_support.py` until a real product crosses the $4K MRR / 30-day sustained gate. Full prompts and table templates already exist in `docs/monitoring-agent-suite.md` for when that day comes — building them now would have nothing to run against.

### 7. Follow-on: wire search signals into the research swarm
CLAUDE.md's SEARCH SIGNAL REQUIREMENT FOR VERDICT PASS rule specifies `search_signals`/`objection_signals`/`geo_signals` output fields the vertical agents don't produce yet. Not blocking launch of the first product, but should land before the second or third product ships so Verdict's search-demand gate is real rather than aspirational.

---

## What Claude Code Can Do Alone vs. What Needs Kelvin

Pattern observed across today's real run: every gap that blocked forward progress was one of exactly two kinds.

**Claude Code can do unassisted (code, migrations, tests, git, calling already-authorized APIs):**
- Write/fix agent code, migrations, tests, dashboard UI — and run the actual test suite, not just claim it passes
- Query/update the live Supabase DB directly (already-linked project, service-role key in `.env`)
- Call any API for which a live key already sits in `.env` (Anthropic, Apollo.io, Systeme.io) — including running a real opportunity through research/Verdict/brief generation, as happened today
- git commit and push (once it has its own working credentials — see `GITHUB_TOKEN` fix above; this was a real gap until today, now solved for good)
- Diagnose external-integration failures from the code side (e.g. finding the LinkedIn redirect_uri mismatch) even when the fix itself is external

**Needs Kelvin, no way around it:**
- Anything requiring a login to a third-party console Claude Code has no credentials for: LinkedIn Developer App settings, Canva Developer Portal, creating a new Stripe account
- Generating a live secret key/credential for a *new* piece of infrastructure (a product's dedicated Stripe key, a new OAuth app's client secret) — by design, not an oversight, per this repo's "no autonomous outbound" rule
- Judgment calls with real money or brand consequences: approving a generated build brief before it becomes a real deployed product, picking which opportunity to greenlight, deciding when a monitoring agent should actually activate
- Anything inside a UI Claude Code can't click through (Canva's Brand Template editor, LinkedIn's redirect URL allowlist field)

The practical rule of thumb: if it's a decision, a credential, or a click inside someone else's web console, it's Kelvin's. Everything else — Claude Code should just do it, not ask permission to check.

---

## Key Constraints (Do Not Violate)

- Dedicated Stripe account for MSE — never share with Cloud Decoded or Decoded Holdings, and never create it before a product needs it
- Hard $4K MRR floor enforced at DB constraint level on every product
- 6 retention loops ship before any feature work on any product
- RLS enforced via `get_supabase_for_request(jwt)` in all routes — never service role in API routes
- Every agent emits `POST /events` + an `audit_log` win/lose entry — dashboards depend on this
- Model routing: Haiku for high-volume scraping, Sonnet for analysis — do not swap
- DataSanitizationShield before every LLM call — no exceptions
- No autonomous outbound — every build, brief-generation, or outreach action requires a named human (`triggered_by`) and admin role; this is enforced in code, not just policy
