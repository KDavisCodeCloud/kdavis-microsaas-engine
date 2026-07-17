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
- **Not yet done: running real opportunities through it.** Every test above uses canned LLM responses — the live research quality itself can only be validated/tuned by actually running opportunities through it for real, which costs real money ($10/1,000 searches + tokens) and is the next concrete step, not something to trigger automatically.

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

**This affected every authenticated POST/PATCH/DELETE endpoint called from a browser** — not just reject/approve, but almost certainly the "Build This Product" trigger and brief-generation trigger too, since all of today's "real" testing of those went through direct Python invocation or FastAPI's `TestClient` (which doesn't simulate real browser CORS preflight), never an actual browser click. Fixed and redeployed to Railway (`mse-api`), verified live against the real production frontend origin (`https://mse.thdecodedempire.com`) with a raw `curl` OPTIONS request — 200 with correct `Access-Control-Allow-Origin` now. **Not yet confirmed by an actual click in the live dashboard — please try the buttons again and confirm.**

### 2. Run 7 more opportunities through Verdict v2.0 to tune it
Kelvin's plan, stated 2026-07-17: run 7 more products through the new research-backed Verdict agent, using the dashboard's new Approve/Reject/comment buttons to build the tuning signal — Kelvin expects to reject all but 1. This is a real-money action (web search is $10/1,000 searches + Sonnet tokens per opportunity, run live, not simulated) — waiting for an explicit go before firing these off rather than running them automatically.

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
