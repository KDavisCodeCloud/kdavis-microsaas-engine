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
- **SATURATED → DO_NOT_BUILD:** ✅ confirmed repeatedly (19/22 real opportunities now — 6 v2.0, 6 v3.0, 7 v4.0)
- **CLEAR → BUILD:** ❌ not yet confirmed after 3 dedicated attempts (2 fresh Framework 4 "recent regulatory change" dispatches, both <12mo-old regulations as requested) — all 3 came back SATURATED against real, named, standalone competitors (SixFifty for pay transparency; Zuub/Weave/Dental Intelligence for dental eligibility). Live web search keeps finding real competitors even in narrow, recently-regulated niches.
- **PARTIAL → BUILD or CONDITIONAL:** ⚠️ confirmed twice now that Verdict genuinely detects PARTIAL (platform-locked competitor + real standalone segment), but **both times the underlying MRR math didn't hold up**: (1) PM/time-tracking approval layer, floor $412 vs $4,450; (2) a Gusto/ADP RUN 1099 compliance gap targeting a very large ICP (Gusto has 300k+ customers) — even this large-TAM attempt only reached a $406 floor vs $4,150, because the realistic reachable segment (businesses actually paying contractors OUTSIDE Gusto/ADP, discoverable via organic-only GTM) turned out to be a small slice of that large raw population. **Still 0/22 evaluations have produced a genuine BUILD/CONDITIONAL** that clears the code-level floor check.
- **RESUBMIT → needs_correction:** ❌ not yet confirmed live under v3.0/v4.0 after 2 dedicated attempts (deliberately single-industry ideas: dental eligibility, veterinary DEA reconciliation) — both times Verdict's Step 2 override correctly fired instead, because both ideas turned out to be genuinely SATURATED on independent research (Zuub/Weave/Dental Intelligence; VetSnap/CUBEX). The RESUBMIT status-mapping code path itself IS confirmed correct via canned-response unit tests (`test_resubmit_maps_to_needs_correction_not_rejected`) — what's unconfirmed is a live model call independently choosing to emit it against real research, which the standing rule calls for.

**Two real bugs found and fixed while running these tests (`fc72269`), both now validated working in production:**
1. `node_write_pipeline` used one atomic batch INSERT — a single bad row previously would have silently destroyed 2 other valid rows in the same batch. Now inserts row-by-row.
2. The code-level price-adjusted-floor check only ever covered `verdict=BUILD` — a live CONDITIONAL result with `final_mrr_floor` at 28% of the target and `time_to_floor.classification: STRONG` (not the legitimate 13-18mo CONDITIONAL path) would have crashed the DB write. Extended the check to CONDITIONAL. **Confirmed working on a second real case in the very next test round**: the Gusto/ADP 1099 idea returned `verdict: CONDITIONAL` from the model with `classification: PASS` (not `CONDITIONAL`) — the code correctly demoted it to `status: rejected` before it ever reached the DB.

**Assessment after 7 v4.0 evaluations + 2 dedicated RESUBMIT attempts + 3 dedicated CLEAR attempts:** CLEAR and RESUBMIT are proving genuinely difficult to trigger via constructed real-world test cases, not just organic research — every idea with enough real quantifiable pain + evidence to pass Step 1 also tends to have enough commercial merit that a real competitor already exists, and single-industry ideas keep dying on SATURATED before the cross-industry gate ever becomes the deciding factor. This is itself a useful finding, not a dead end — worth a check-in on strategy before spending more on further live attempts.

---

## Verdict Agent v5.0 (shipped 2026-07-19, complete replacement of v2.0-v4.0)

After 22 real v2.0-v4.0 evaluations (19 SATURATED, 2 PARTIAL both failing MRR math, 0 CLEAR, 0 genuine RESUBMIT), Kelvin's read: "this isn't working, replace every prior version completely." v5.0 inverts the whole model — instead of generating an idea from scratch and checking if a competitor exists (which killed almost everything), Dispatch now anchors every idea on a NAMED existing tool people are already using and complaining about (G2/Capterra/Reddit/forum reviews, 3+ reviews citing the same specific workflow gap). Verdict retires SATURATED and RESUBMIT entirely — only `BUILD | CONDITIONAL | DO_NOT_BUILD` remain, and the only question is whether the existing tool is failing enough of its users to build a $4K MRR business around the specific gap.

**Also closed a real v4.0 bug in the same rewrite:** CONDITIONAL used to allow a "floor doesn't clear but might with a named partner" escape hatch (13-18mo classification) — exactly what let a real CONDITIONAL result reach the DB at 28% of its target floor. v5.0 removes the escape hatch by definition: CONDITIONAL and BUILD both require the floor to genuinely clear, differing only in timing (month 1-7 vs 8-12). Code-level enforcement in `agents/aggregator/agent.py` now applies this uniformly to both statuses.

**First live v5.0 batch (2026-07-19), 2 verticals (accounting, legal), 2 submissions:**
- Wave Accounting invoice-reminder add-on → DO_NOT_BUILD on **math**, not gap: Verdict independently re-verified Wave's active user base (~2M, not the ~3.5M Dispatch assumed) and found net MRR of $2,352 vs a $4,000 floor (41% short), with Wave's webhook API also gated to the Pro plan, further compressing the reachable segment.
- Clio Manage trust-account monitor → DO_NOT_BUILD because **the claimed gap doesn't exist**: Verdict found Clio Manage already ships per-matter trust balance threshold alerts and automated retainer replenishment natively on Essentials+ — Dispatch's Element 2 (specific complaint pattern) was based on a stale/incorrect premise, caught by Verdict's independent verification before any build resources would have been spent on a redundant feature.

**Read on this first batch:** both real rejections, for two completely different and substantive reasons (TAM overestimate; false gap premise) — not a repeat of "a competitor exists, halt." This is a good early sign the new rules are doing real independent verification rather than rubber-stamping, though it also means Dispatch's own live-search-free idea generation (plain Sonnet, no web_search tool, same as all prior versions) is producing assumptions Verdict's real search then corrects — worth more batches to see if any submission's assumptions hold up under Verdict's independent check.

**FEATURE_GAP verification rule added 2026-07-19** (`a010a8a`) after the Clio Manage false-premise rejection above: both agents now require checking a tool's current help docs/release notes before trusting a claimed feature gap, and checking whether reviews dated after a recent (<24mo) feature release still cite the same complaint.

**Batch 2 (real estate, HR), 2 submissions — same failure mode confirmed twice more:**
- Rentec Direct maintenance-notification layer → DO_NOT_BUILD: Rentec Direct shipped automated tenant maintenance-status notifications in October 2024; all of Dispatch's cited review evidence predated the release, no post-release reviews re-cited the gap.
- Gusto onboarding document orchestrator → DO_NOT_BUILD: Gusto's Plus/Premium plans already ship custom document upload with e-signature and conditional routing; Gusto's G2 rating has risen 4.2→4.6 across 8,500+ reviews since the dated complaints Dispatch cited.

**Structural finding, 4/4 rejections across both batches now driven by the same root cause:** Dispatch generates ideas via plain Sonnet with no web search (`core.llm_router.analyze`) — it's working off training-data knowledge of what these tools used to do, and 2 of 2 anchor tools in batch 2 had shipped the exact "missing" feature within the last 8-21 months, invisible to Dispatch without live verification. Verdict (which does have live web search) is correctly catching every case, but this burns a full Verdict cycle (and real search cost) on ideas that were dead on arrival.

**Dispatch given real web search 2026-07-19** (`f2729ad`) — `agents/orchestrator/agent.py`'s `_run_one_vertical` switched from `analyze` to `analyze_with_web_search`, with a new `_extract_trailing_json_array` helper (mirrors the aggregator's own trailing-JSON extraction) since a search-backed response narrates before its final array. **Real bug found immediately**: the call explicitly overrode `max_tokens=8192`, bypassing the 16000 default raised earlier for the same truncation issue on Verdict's side — first live run returned 0 findings from both dispatch calls. Fixed (`57642fa`), confirmed working on retry.

### 🎉 First confirmed BUILD, 2026-07-19 — batch 3 (e-commerce, finance), 6 submissions

**Shopify-native ad-spend/inventory forecasting tool** (cheaper alternative to Inventory Planner by Sage) → **BUILD, `status: READY_TO_BUILD`**, code-level floor check passed:
- `existing_tool`: Inventory Planner by Sage, $119.99/mo (Essentials) up to ~$245/mo for full-featured access (Verdict corrected Dispatch's submitted $299/mo figure)
- `gap_type`: PRICE_GAP — too expensive for smaller Shopify sellers who still need ad-spend-aware inventory forecasting
- `net_mrr_floor`: **$5,174** vs `price_adjusted_floor`: $4,000
- `timeline_classification`: STRONG, floor clears by **Month 5**
- This is the first real opportunity across the ENTIRE calibration effort (v2.0-v5.0, ~30 real evaluations) to clear every gate independently, including the code-level floor re-verification. It's sitting in `opportunity_pipeline` with `status: READY_TO_BUILD` now — will surface in the dashboard's Build Queue tab. **Needs Kelvin's human review/approval** (separate `human_review_status` field, per the existing HITL design) before triggering an actual build.

Other 5 submissions this batch: 1 rejected on PLATFORM_GAP-not-confirmed (Linnworks — HyperStock/WarehousePlus already fill it, free); 3 rejected on math alone (ShipStation, QuickBooks Online, FreshBooks — all had a real, confirmed gap but the reachable segment was too small once independently verified); 1 rejected on the FEATURE_GAP verification catching a stale premise again (Xero — shipped native W-9 collection August 2024). **Note: giving Dispatch web search reduced but did not eliminate the stale-premise failure mode** — 1/6 this batch vs. 4/4 the prior two batches, real improvement but not perfect; Verdict's independent re-verification remains the actual safety net.

## Cost optimization pass (2026-07-19)

Kelvin: "costs are way too high" — pasted a 6-phase cost-reduction spec.
**Phase 1's diagnosis didn't match this codebase** — every Dispatch/Verdict
call was already a single opportunity with a fresh `messages` list, no
conversation history or batch accumulation ever carried between calls
(confirmed by reading the actual code before touching anything). The real
drivers: no prompt caching on the large, mostly-static system prompts;
Sonnet on both agents; no output token cap. Skipped Phase 2 (Gemini Flash
for Dispatch) after user confirmation — needs a GEMINI_API_KEY only Kelvin
can provision, and adds an unapproved vendor; Haiku + caching covers the
same goal without either problem.

**Shipped (`f20faf0`, `053e417`):**
- `core/llm_router.py`: `cache_control: ephemeral` on the system prompt for
  both `analyze()` and `analyze_with_web_search()`; added `model=` param
  (default Sonnet, unaffected for brief_generator/naming/digest_generator/
  ceo routes); token-usage + estimated-cost logging; `max_tokens` reduced
  16000→8000 (Verdict) / →10000 (Dispatch) — not down to a guessed
  2000-3000, since 8192 already truncated a real Verdict call mid-
  narration once this session.
- Both agents now call Haiku instead of Sonnet.
- Confidence score system added to Verdict (4 components, 0-25 each,
  override rule: <45 forces DO_NOT_BUILD, 45-59 downgrades BUILD to
  CONDITIONAL, never upgrades), enforced again at the code level.
- **Real bug caught in regression testing**: the SCORE_INTERPRETATION
  reference table's verdict-sounding labels ("60-74: CONDITIONAL") were
  being read by the model as a second way to SET the verdict, contradicting
  Step 3's own finding on a real case (floor_cleared=true,
  timeline=STRONG, but verdict written as CONDITIONAL because confidence
  landed at 69). Fixed (`053e417`) — SCORE_INTERPRETATION is now explicit
  reference-only commentary; only the two stated override rules may touch
  `verdict`.
- MSE's own dashboard (`frontend/app/pipeline/page.tsx`) — confidence
  meter with 4-component breakdown, color-coded to the spec's bands, 75%
  threshold marker, Approve button soft-disabled below 75% with a warning.
  TypeScript compiles clean. **Not built yet**: the "Ask More" follow-up
  flow (re-query Verdict with a specific question, update the score in
  place) — a real, separate feature, deferred as a fast-follow rather than
  folded into an already-large pass.

**Real BUILD-finding confidence batch (2026-07-21), 5 fresh anchor tools, 15 submissions — full Haiku pipeline (Dispatch + Verdict), not a hand-crafted or re-tested case:**
- **HubSpot Sales Hub (3 submissions):** all rejected on math — reachable segments too small (148-2,000 accounts), net MRR topping out at $82-283/mo against $4,000-4,500 floors.
- **Calendly (3 submissions):** 1 BUILD (below), 2 rejected on math.
- **Square/Toast/Clover POS (3 submissions):** all rejected — one gap-verification catch (Square shipped bar-tab preauth March 2025, the claimed gap no longer exists), one evidence-staleness catch (cited pain point traced to a single 2017 review, not current), one real but too-small service-business opportunity (Clover cancellation help, $3,168 net MRR vs $4,500 floor).
- **Mailchimp (3 submissions):** all rejected — one gap-verification catch (segmentation "5-condition limit" contradicted by current docs, Standard plan has unlimited), two math shortfalls. One (CRM augmentation layer) is a second live confirmation of the CONDITIONAL floor-check fix: model claimed `CONDITIONAL`, net_mrr_floor was $765 against a $4,000 floor with `timeline_classification: PASS` (not `CONDITIONAL`) — code correctly demoted to rejected before it could reach the DB.
- **Zendesk (3 submissions):** all rejected on math — reachable segments overclaimed (86 submitted vs. 1,200 independently verified; 18-50 accounts), net MRR topping out at $187-936/mo.

**The one BUILD — genuinely independent, not inherited from the earlier Sonnet-found opportunity:**
Calendly recurring/series booking add-on ("one-click recurring appointment series booking for service professionals") — `existing_tool`: Calendly ($12-20/seat/mo, 4.7 stars, 2,641 reviews), `gap_type`: FEATURE_GAP (no native recurring booking), **net_mrr_floor $12,480 vs. $4,000 floor (3x margin)**, confidence **87/100**, timeline **STRONG, floor clears Month 1**. Passed the code-level floor re-verification (`status: READY_TO_BUILD`), not just the model's own claim.

**Assessment:** 1/15 (6.7%) reached BUILD/CONDITIONAL — still below the pipeline health target of 20%+ across a rolling 10, but a real, high-margin, independently-found BUILD is a materially stronger confidence signal than the ambiguous Shopify re-test result. Combined with the Shopify case (2 BUILD-family results total across the full Haiku era so far, one clean, one ambiguous), there's real but not yet abundant evidence Haiku can find builds on its own, not just correctly reject. Worth running further batches to build a fuller track record rather than treating this one result as fully conclusive either way.

**Second real BUILD-finding confidence batch (2026-07-20), 5 fresh anchor tools, 15 submissions — full Haiku pipeline (Dispatch + Verdict), tools never tested in any prior batch this session:**
- **Intercom (3 submissions):** 1 BUILD, 2 rejected — one gap-verification catch (native SMS/WhatsApp already exists on Intercom, claimed gap doesn't), one math shortfall (KB importer, $1,254 net MRR vs. $4,000 floor).
- **DocuSign (3 submissions):** all rejected on math — reachable segments too small (real estate transaction dashboard loses to Dotloop's 98% retention/50%+ share; fixed-price solopreneur tier and envelope-reminder layer both top out under $2,200/mo against $3,500-4,000 floors).
- **Asana (3 submissions):** all rejected on math — multi-assignee layer, overdue-escalation layer, and analytics layer all reach only $46-$314/mo against $3,500-4,000 floors; reachable segments 112-2,000x too small in the worst case.
- **Airtable (3 submissions):** all rejected on math — permission/cost dashboard, Interface theming layer, and PDF exporter all top out at $196-$992/mo by month 12 against $4,000 floors.
- **ActiveCampaign (3 submissions):** all rejected — one gap-verification catch (native Spam Check shipped Feb 10, 2026; the deliverability-scoring pain point no longer exists, confidence scored 23), two math shortfalls ($74-$570/mo against $4,000-4,500 floors).

**The one BUILD:** Flat-rate SMS/WhatsApp orchestration layer on Intercom ("predictable monthly billing" vs. Intercom's per-message usage fees) — `existing_tool`: Intercom ($29-$132/seat/mo + $0.99 Fin resolution + per-message SMS/WhatsApp, 4.5 stars, 3,880 reviews), `gap_type`: PRICE_GAP, **net_mrr_floor $6,400 vs. $5,000 floor**, confidence **77/100**, timeline **STRONG, floor clears Month 7**. Reached `READY_TO_BUILD` after the code-level floor re-check.

**Assessment:** 1/15 (6.7%) again — identical rate to the prior batch. Combined Haiku-era rolling total is now **2/30 (6.7%)** reaching BUILD/CONDITIONAL, still well below the 20%+ target, with 3 BUILD-family results total across all batches (2 clean: Calendly, this Intercom case; 1 ambiguous: Shopify). Two consecutive batches at the same rate is a firmer signal than one — Haiku reliably finds real, well-anchored gap-verification catches and does correct math rejection, but the BUILD-surfacing rate itself is not yet trending toward the pipeline-health floor. Worth continued tracking, not yet a "fix this" trigger on its own.

**Haiku regression results (live, real cases, not canned) — genuinely
mixed, reported honestly rather than rounded up to a clean pass:**
- Wave Invoice Escalation → DO_NOT_BUILD, matches expected. Clean.
- Clio Trust Balance Alerts → DO_NOT_BUILD, matches expected. Clean.
- Shopify Inventory Forecasting → expected BUILD. First Haiku pass hit the
  SCORE_INTERPRETATION bug above (wrongly CONDITIONAL). Re-run after the
  fix: Haiku's own live search this time found the specific claimed
  feature ("ad-spend-aware reorder triggers") already exists in Inventory
  Planner's Standard tier, and surfaced two specific budget competitors
  (Forthcast $19.99/mo, Assisty $19/mo) not found in the original Sonnet
  pass — a well-reasoned, specifically-cited DO_NOT_BUILD, not a
  hallucination. Genuinely ambiguous whether this is Haiku doing MORE
  thorough research than the original Sonnet pass, or ordinary run-to-run
  variance in live web search (which this whole project has shown
  repeatedly, independent of model). Does not cleanly confirm Haiku
  matches Sonnet's research quality on this specific case — worth
  watching on the next few real batches rather than treating as settled.

**v5.0 calibration status (supersedes the old v2.0-v4.0 four-state CLEAR/PARTIAL/SATURATED/RESUBMIT checklist, which no longer applies under the retired competitor-absence gate):**
- **BUILD → READY_TO_BUILD:** ✅ confirmed (above)
- **CONDITIONAL → validated (floor clears at month 8-12, not 1-7):** ❌ not yet confirmed
- **DO_NOT_BUILD → rejected:** ✅ confirmed repeatedly (10 of 11 real v5.0 submissions so far), now via substantive reasons (TAM overestimate, false gap premise, math shortfall, existing gap already filled) rather than a reflexive competitor-exists halt

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

## Launch readiness audit (2026-07-20) — 2 critical items resolved same day

Full audit published as an artifact; two of the four critical findings were fixed immediately:

- **Deploy pipeline auth fixed** (`b679afc`): `agents/factory/deploy.py` no longer assumes an interactive `railway`/`vercel` login session. Both CLIs now get their token from `RAILWAY_TOKEN`/`VERCEL_TOKEN` env vars, validated with a fail-fast, specifically-named error before any CLI command runs — `deploy_product` checks both up front so a missing Vercel token can never be discovered only after a Railway project already exists. Documented in `.env.example`. **Still needs Kelvin:** actually generate both tokens (railway.app / vercel.com account settings) and set them wherever `mse-api` actually runs — the code fix alone doesn't create the credentials.
- **CLAUDE.md brought current to v5.0** (`b679afc`): was still describing Verdict v3.0 as the live system and said "Sonnet for analysis — do not swap," directly contradicting the verified Haiku switch. Both fixed surgically, nothing else touched.

**Search Visibility Layer shipped 2026-07-20** (`b889326`): turned out to be a bigger gap than missing SEO tags — `app/page.tsx` was a bare redirect to `/login`, meaning scaffolded products had no public marketing site at all. New `agents/factory/search_visibility_generator.py` generates the real marketing site (landing/pricing/FAQ/comparison/definitive-answer pages) with SEO (JSON-LD, sitemap.ts/robots.ts, per-page metadata), AEO (10+ grounded FAQ pairs, 1200+ word definitive answer), GEO (comparison anchored on the opportunity's real `existing_tool`, author attribution), and SXO (above-fold CTA, no dead ends) built in from the start — one grounded Sonnet call per product using real Verdict research data, non-negotiable floors enforced in code (`SearchVisibilityError`), never lorem-ipsum. Two real injection bugs found by generating and reading an actual scaffold (not just passing unit tests): the JSON-LD script wasn't escaping `<` (a `</script>` in LLM content could break out of the script tag), and every other field was interpolated as raw, unescaped JSX text (could break the generated file's syntax or inject an element). Both fixed, both covered by regression tests using real attack strings. Deliberately NOT built: Lighthouse verification (that's post-deploy, needs a live URL — belongs in deploy.py as a follow-on) and fabricated social proof (a real chicken-and-egg problem for a zero-customer product — ships as a commented-out slot, not a fake testimonial). 28 new tests, 181 passing overall.

**Dedicated MSE Stripe account created 2026-07-20** (Kelvin, manual step per CLAUDE.md's non-negotiable) — workspace "Micro Saas Decoded", `acct_1TpLcKLIpoJRr7Tc`. This was the last credential-only blocker on the critical path; everything else needed to actually trigger a build now exists in principle.

**Dashboard fix shipped 2026-07-21** (`2f58e2c`): Kelvin hit a real gap reviewing Ninety Nine Comply — the Build Briefs section showed only product name/vertical/status, forcing a click into a 10k+ char raw markdown document just to learn what the product actually does. Fixed via `mse_build_briefs.opportunity_id`'s real FK to `opportunity_pipeline` (Supabase embedded select, not markdown parsing — the brief markdown's "pitch" section heading isn't enforced anywhere, so regex-extracting it would've been fragile). Now shows a pain-point preview in the collapsed row and a full "What This Is" summary card (solution concept, pain point, MRR math) at the top of the expanded view.

**Build Queue → brief linking shipped 2026-07-21** (`7b9d1e3`): every READY_TO_BUILD opportunity card now shows a "Build Brief" block right above "Build This Product" — a direct "View Build Brief" link (scrolls to and expands the matching entry, matched via `opportunity_id`) if one exists, or a "Generate Build Brief" button if it doesn't. There was previously no dashboard trigger for brief generation at all, only the API route.

**Still open from the audit** (see the artifact for the full ranked list): 2 opportunities awaiting Kelvin's review (Ninety Nine Comply, 3+ days; Shopify inventory forecasting, 1+ day) — nothing else can proceed until one gets approved and its secret key is entered directly in the dashboard's masked Stripe key field (never pasted into a chat session); confirm `RAILWAY_TOKEN`/`VERCEL_TOKEN` are actually set wherever `mse-api` runs in production (the code fix landed 2026-07-19, but the tokens themselves still need generating from each platform's account settings); the search-signal requirement decision point (does v5.0's tool-anchored sourcing already satisfy it?); Lighthouse post-deploy verification (follow-on from the Search Visibility Layer work); a few lower-priority items (cross-dashboard agent-run correlation, "Ask More" follow-up flow, GITHUB_TOKEN production verification, VERTICAL_MODULE_MAP likely-dead code).

**Note on the "2 opportunities awaiting review" above — both resolved 2026-07-20, see below.** Ninety Nine Comply did not survive re-verdict; it is no longer awaiting review because the opportunity itself was killed.

---

## Build briefs generated + hard MRR gate shipped (2026-07-20)

**Both pending build briefs generated** (`966ff378`, `64ed7a59`): Shopify ("Campaign Aware Replenishment") and Calendly ("Series Scheduler Pro"). Both hit the same broken `gh auth git-credential` issue as every other git push this session — the automated `generate_build_brief` git push failed, logged a "lose" audit event, and never reached the DB insert step. Recovered manually: pushed both branches with the token-extraction workaround, then completed the DB insert by hand using the exact payload shape `generate_build_brief` would have written. Both briefs are live, `pending_review`.

**RULE: HARD MRR GATE shipped** (full rule text in `CLAUDE.md`) — Kelvin's diagnosis: obvious misses ($46/mo, $50/mo, $74/mo, $108/mo results) were reaching the dashboard, costing review time, and burning tokens on ideas that never had a chance. Two gates added to `agents/aggregator/agent.py`:
- **Pre-Verdict prefilter**: Dispatch's own self-reported `conservative_mrr_potential` is checked before a Verdict web-search call is ever made — below $3,500 (the lowest tier floor in `_PRICE_TIER_FLOORS`, reused as the universal minimum), killed before spending the call.
- **Post-Verdict gate**: only 4 outcomes now, purely numeric (`net_mrr_floor` + `confidence_score`), the model's own BUILD/CONDITIONAL/DO_NOT_BUILD label no longer authoritative — `killed_below_floor` (< $3,500, archived to `opportunity_pipeline_rejections`, never touches the dashboard), `READY_TO_BUILD` (clears its own price-tier floor AND confidence ≥75), `validated`/CONDITIONAL ($3,500–$4,000 absolute band OR confidence 65-74), `watch` (clears $3,500 but misses both of the above — the DB column existed since migration 002 but nothing ever wrote to it until now). 33 aggregator tests + 1 new write-pipeline test rewritten/added to lock in the new contract; 185 tests passing overall.

**Applied retroactively**: 15 pre-existing rows below $3,500 archived + deleted. Two READY_TO_BUILD opportunities predating the confidence-score system (no recorded score) were re-verdicted for real rather than grandfathered in:
- **Campaign Aware Replenishment (Shopify)**: re-verdict came back confidence 61/100, net_mrr_floor unchanged at $5,174 → **demoted to `watch`**. Clears the money, doesn't clear the confidence bar for a clean pass. Its build brief stands, unaffected.
- **Ninety Nine Comply**: re-verdict came back `net_mrr_floor: None` (effectively $0) / confidence 20/100 → **killed_below_floor, archived, and deleted from `opportunity_pipeline`**. On genuine independent re-evaluation the money never clears at all — this is the opportunity Kelvin flagged confusion about earlier ("what is it, I didn't approve the build"); it turned out not to hold up. Its `mse_build_briefs` row survives with `opportunity_id` now `NULL` (existing `ON DELETE SET NULL` FK, migration 011) — brief content isn't destroyed, just orphaned. **Needs a decision from Kelvin**: reject/archive the orphaned brief manually, since the underlying opportunity no longer exists.

Post-cleanup dashboard state: exactly 3 rows in `opportunity_pipeline` — Intercom SMS/WhatsApp (READY, $6,400), Calendly recurring booking (READY, $12,480), Shopify ad-spend forecasting (watch, $5,174). 80 rows total in the rejection archive.

**RULE: prompt-side companion added** (`agents/orchestrator/prompt.md`) — before scoring any opportunity, Dispatch must now answer 3 questions internally: exact buyer (job title/company size/industry), the specific manual workflow being replaced (actual steps, not category), and why the incumbent hasn't shipped this natively (one of: regulatory complexity, different customer segment, technical architecture constraint, intentional product decision — logged into `competition_density_reason`). If the third question can't be answered with a specific structural reason, the idea is discarded before Verdict. Targets the same root cause as the MRR gate from the sourcing side: category-level ideas without a durable, named reason for the gap's existence are what's been dying on Verdict's math checks.

**Test batch, 2026-07-20, 5 fresh anchor tools (Toggl, Buffer, Pipedrive, Notion, ConvertKit) — first real batch under both new rules:** **0/15 reached BUILD or CONDITIONAL.** All 15 killed_below_floor before touching the dashboard (net_mrr_floor range $0–$1,444, confidence range 15-65) — the gate worked exactly as designed, zero clutter reached `opportunity_pipeline` from this batch (still exactly 3 rows after). But the actual BUILD-finding rate this batch is worse than the prior two (1/15 each). Honest read: this is a single small batch against 5 extremely large, well-established, heavily-reviewed incumbents (Notion alone has 10,967 G2 reviews) — cannot cleanly attribute 0/15 to the new 3-question rule vs. these specific tools simply being harder, more saturated markets than the previous batch's anchors. The script doesn't currently surface how many ideas Dispatch discarded internally via the new Q3 rule before submitting these 15 (all 5 tools produced exactly 3 submissions each, the max), so there's no direct evidence yet that the new rule is filtering anything Dispatch wouldn't have filtered anyway. Rolling Haiku-era tally is now 2/45 (4.4%) — worse trend, not better, needs more batches against a wider mix of anchor tools before concluding anything about the new prompt rule's effect.

**Vertical-only batch, 2026-07-20, real estate + field service (Kelvin's own curated target list, 5 of 12 selected to hit 15 submissions — Follow Up Boss, Dotloop, Propertyware, Jobber, ServiceTitan): 1/10 reached BUILD or CONDITIONAL (10%).** Only 10 submissions collected, not 15 — Follow Up Boss produced zero submissions this run (Dispatch found nothing that cleared its own pre-submission math/Q3 bar for that tool). Per-platform breakdown:
- **Real estate:** Dotloop (0/3 survived — 2 killed on math, 1 killed by the **pre-filter** before a Verdict call even ran, the first real pre-filter hit observed this session: Dispatch's own submitted $3,200 was already below $3,500), Propertyware (**1/3 survived — the batch's only BUILD**), Follow Up Boss (0 submissions).
- **Field service:** Jobber (0/1 survived), ServiceTitan (0/3 survived — one submission is worth flagging: the model labeled it `verdict: CONDITIONAL, timeline: PASS` but its own `net_mrr_floor` was $0, an internally inconsistent result the numeric gate correctly killed regardless of the model's label).

**The one BUILD:** accounting-focused property management SaaS for 10-50 unit landlords at $29-49/mo flat fee — `existing_tool`: Propertyware ($1-2/unit with $250-450/mo minimums, 3.8 stars, 324 reviews), `gap_type`: PRICE_GAP (Propertyware's per-unit-with-high-minimum pricing doesn't work for small landlord portfolios), **net_mrr_floor $5,040 vs. $3,500 floor**, confidence **83/100**, timeline **STRONG, floor clears month 7**. Reached `READY_TO_BUILD` after the code-level checks.

**Early read on the vertical-vs-horizontal question:** small sample (10 submissions, 1 batch), but real estate (1 hit / 2 active platforms) outperformed field service (0 hits / 2 active platforms) this round, and the 10% rate beats both the 6.7% horizontal-tool rate and the 0% ConvertKit/Notion/etc. batch. Not enough data to call this a trend yet — needs more batches before concluding vertical/niche tools systematically outperform horizontal SaaS for this factory. Dashboard now has 4 rows: 3 READY (Intercom, Calendly, Propertyware) + 1 watch (Shopify). 89 rows in the rejection archive.

---

## Pipeline v1-vs-current diff + 3 frozen-config baseline batches (2026-07-20)

**Diff artifact published** (read-only, no code changes): traced the actual first commits (`0697047`, `c9a9025`, 2026-07-03/04 — before Verdict had a version number) against today's live configuration. Headline finding: v1's aggregator was a pure deterministic Python gate-checker with zero LLM calls — it trusted whatever a "vertical intel agent" submitted at face value (its "MRR math" gate was literally `len(mrr_calculation) > 15`, a string-length check). Those 6 vertical agents were never built, so v1 was never actually run for real. Assessment: don't roll back the verification model (every incident since v2.0 was a direct consequence of trusting self-reported data); do consider consolidating the floor/confidence logic later (it's grown by accretion — 3 floor checks + 2 confidence bands — not by design, v1 was genuinely cleaner here); most importantly, hold the current configuration steady before changing it again, since every batch so far has changed the prompt *and* the anchor tools simultaneously, so no result isolates one variable.

**3 baseline batches run immediately after, no logic changes, deliberately mixed anchor tools** (completing the original real-estate/field-service list + following the one proven signal (property management) + fresh horizontal tools for comparison):
- **Batch A — ShowingTime, FieldEdge, Workiz, mHelpDesk** (Buildout produced 0 submissions): **1/12 BUILD.** ShowingTime's post-showing automation middleware — `existing_tool` ShowingTime Appointment Center ($45/mo, 4.3 stars, 77 reviews), **net_mrr_floor $6,586 vs. $4,000 floor**, confidence **90/100** (highest of the session), READY_TO_BUILD.
- **Batch B — AppFolio, Rent Manager, Housecall Pro, Acuity Scheduling** (Buildium produced 0 submissions): **0/11.** All killed on math; two more real "model says CONDITIONAL but its own net_mrr_floor doesn't clear the absolute floor" catches (FieldEdge $464 vs. $5,000 tier, Housecall Pro $772 vs. $4,500 tier) — the numeric override continues to catch internally inconsistent model outputs.
- **Batch C — Trello, Monday.com, Harvest, Typeform** (Freshdesk produced 0 submissions): **0/12.** All killed on math (net_mrr_floor range $0-$588).

**Rolling Haiku-era tally across all 7 real batches to date: 4/90 (4.4%)** — the 4 BUILD-family results are Calendly, Intercom, Propertyware, and now ShowingTime. Still below the 20% pipeline-health target, but 90 submissions is a real enough sample to say the rate is genuinely landing in the 4-7% range, not swinging batch to batch by chance — the earlier 10% (real estate/field service) and 0% (Toggl/etc.) results were both within normal variance of this same underlying rate, not evidence either batch's specific tools or the prompt changes moved the needle on their own. Property management/real estate keeps producing hits (Propertyware, ShowingTime) while pure horizontal SaaS (Trello, Monday, Typeform, Harvest) has now gone 0-for-many across two separate batches — the closest thing to a real trend in the data so far. Dashboard: 5 rows (4 READY: Intercom, Calendly, Propertyware, ShowingTime; 1 watch: Shopify). 123 rows in the rejection archive.

---

## Healthcare vertical validation — 3 batches, same frozen config (2026-07-20)

**Batch D — SimplePractice, Kareo/Tebra, AdvancedMD, TherapyNotes (12 submissions): 0/12 BUILD, 1 watch.** The watch: an AI-powered treatment-plan progress tracker for TherapyNotes — `net_mrr_floor $4,248` (clears its own $4,000 tier floor cleanly) but confidence only **57/100** — too low for READY (needs ≥75) or CONDITIONAL (65-74 band), so it surfaces as watch: genuinely clears the money, needs Kelvin's judgment on the confidence gap specifically. Everything else killed on math ($71-$1,040 range).

**Batch E — Weave, Solutionreach, NexHealth, Klara, Updox (15 submissions): 0/15.** All killed on math, best case $1,920 vs. a $4,000 floor. **Data-quality note:** 4 of the 15 submissions (1 Weave-adjacent, 1 Klara, 2 Updox) came back with `existing_tool`, `verdict`, and `net_mrr_floor` all `None` — Verdict's response didn't parse into the expected schema at all for these. The code failed safe (final_floor defaults to 0, correctly killed_below_floor, no crash, nothing inflated) but this is the first time this session multiple submissions in one batch came back essentially empty — worth a closer look at whether Haiku is occasionally failing to close out the JSON contract on some prompts, independent of anything gate-related.

**Batch F — CareCloud, Curve Dental, Open Dental, RXNT, PracticeSuite: only 3 submissions total, all CareCloud.** Curve Dental, Open Dental, RXNT, and PracticeSuite returned zero submissions each — Dispatch found nothing worth submitting for any of the other four. 0/3 BUILD, all killed on math ($551-$1,119 range).

**Healthcare vertical total: 0/30 reached BUILD or CONDITIONAL (0%), 1 watch.** Honest read: this is the opposite result from the real-estate/property-management push — that niche produced 2 real hits in roughly 34 tries (~6%), while healthcare (practice management, patient communication, and dental tools specifically) went 0-for-30 with only one marginal watch. Doesn't rule out healthcare as a vertical entirely (30 submissions across mostly EHR/PM/patient-comms categories is not exhaustive — scheduling-specific or billing-specific healthcare niches weren't tried), but on this test, healthcare has **not** validated the way property management did. Updated rolling Haiku-era tally: **4/120 (3.3%)** across all 10 real batches to date. Dashboard: 6 rows (4 READY, 2 watch: Shopify + TherapyNotes). 152 rows in the rejection archive.

---

## Pipeline health monitoring — now automated (2026-08-15)

Every batch tally above was tracked narratively, by hand, in this file — nothing enforced the 20%+ target or acted on a low rate beyond a human (Kelvin, or a Claude session) reading this log and deciding to intervene. That's now automated: `agents/aggregator/pipeline_health.py` checks the real rolling-10-submission build rate after every research run and, below a 10% floor with one failure category dominant (>40% of the window), auto-applies a defined recalibration — logged to `mse_pipeline_recalibrations`, not this file. Full rule: `CLAUDE.md`'s "RULE: PIPELINE HEALTH AUTO-RECALIBRATION."

**Immediate implication, stated plainly:** the real rolling tally above (4/120, 3.3%) is already well under the new 10% trigger. The very next real research run after this migration is applied to production will very likely fire a recalibration on its first health check — expected, not a bug, given the data already logged in this file. Whether it fires on `RESEARCH_POOL_EXHAUSTED`, `MRR_FLOOR`, or something else depends on the real rolling-10 window at that moment (the last 10 by timestamp, not the all-time 4/120 figure), which this file doesn't track precisely enough to predict in advance.

This file remains the place for narrative batch write-ups (what was tested, what was found, qualitative read) — the health monitor's own decisions are queryable from `mse_pipeline_recalibrations` directly, not duplicated here.

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

---

## Email Campaign HITL Queue API + RFC 8058 compliance (2026-09-21)

Session goal (part of a 3-repo cross-cutting build — see
`kdavis-agentic-platform`'s CEO Decoded dashboard, which consumes this):
give the CEO dashboard a real API to review/approve MSE's lifecycle email
sequences, and close a real CAN-SPAM/RFC 8058 gap on the one channel that
actually sends live email today.

**Shipped, safe, additive, tested (624 passed / 1 skipped / 0 failed,
clean env — see note below):**
- `core/email_compliance.py`: `build_list_unsubscribe_headers()` — RFC
  8058 `List-Unsubscribe` + `List-Unsubscribe-Post` headers.
- `agents/marketing/mkt_o5_sequence_sender.py`: wired those headers into
  every real Resend send (the only live commercial-email sender in this
  repo — MKT-O3/Brevo has never sent anything real, `BREVO_API_KEY` is
  unset).
- `api/routers/marketing.py`: `POST /marketing/unsubscribe` — the RFC
  8058 one-click POST target (existing `GET` landing page unchanged).
- `api/routers/marketing_internal.py` (new, registered in `api/main.py`):
  admin-JWT-gated `GET/POST /marketing/internal/email-templates[...]`,
  `/campaign-status`, `/email-metrics`. Full contract:
  `docs/internal/email-approval-api.md`.
- Migration `20260921000051_email_sequence_grounding.sql`: additive
  `origin`/`source_script`/`grounding_sources` columns on
  `mse_email_sequences`, plus a `retired` status option. **Not yet
  confirmed applied to the live DB** — this repo has no `DATABASE_URL`
  in the local `.env` (only set on the `mse-api` Railway service itself),
  so it could not be dry-run locally; `core/migrate.py` applies it
  automatically on next deploy. Confirm post-deploy via
  `mse_schema_migrations` or by checking the new columns exist.

**Real architecture finding — deliberately NOT worked around this
session, needs a decision before the next email-system session:**
Three unrelated "product_id" spaces coexist in this repo with no
resolving FK between them: (1) `opportunity_pipeline.id` — what the live
campaign trigger (`mkt_orch_campaign_orchestrator.py`, fired from
`POST /products/{id}/run-campaign`) actually keys on; (2)
`mse_products.id` — the DIST positioning-gate registry (`mse_positioning`,
10 products, only `small-portfolio-hub` has an approved brief as of
today); (3) whatever `mse_email_sequences.product_id` actually is on its
2 existing rows (0/2 match `mse_products.id`, both pre-date that
registry). The build spec this session was written against wanted
campaign generation hard-gated on an approved `mse_positioning` row
(Locked Decision 3) — that gate is genuinely NOT wired, because doing so
correctly requires resolving which of these three ID spaces
`mkt_orch_campaign_orchestrator.py` should actually check against, and
guessing wrong risks silently blocking (or failing to block) a live,
n8n-triggered production path for real products. Needs Kelvin's call:
either add an explicit `mse_products_id` FK to `opportunity_pipeline`
and `campaign_builds`, or accept slug-based matching as good enough, then
wire the gate for real.

**Also NOT built this session, and why:** a parallel
`mse_email_templates`/subscribers/enrollments/sends/clicks schema, a
15-minute scheduler, and click tracking. The build spec assumed these
don't exist; in fact `mse_email_sequences` + `campaign_builds` + MKT-O3
already cover most of the same ground under different names, and
building a second schema alongside it would recreate the exact
kind of duplicate-system problem this cross-repo build was explicitly
trying to avoid on the Cloud Decoded side (see that repo's own
reconciliation decisions, same session). Recommend extending
`mse_email_sequences` (per-step rows instead of one JSONB blob, plus
new `mse_email_clicks`/`mse_email_sends` tables) as a properly-scoped
follow-up, once BREVO_API_KEY is actually set and MKT-O3 sequences have
something real to track.

**Testing note for future sessions:** do not `source .env` before running
`pytest` in this repo — the real `SUPABASE_JWT_SECRET`/service keys in
`.env` silently override `tests/conftest.py`'s placeholder values (which
use `os.environ.setdefault`), breaking every JWT-signing test with
401s instead of the expected 403s. Run pytest in a clean env
(`env -i HOME="$HOME" PATH="$PATH" python3 -m pytest`) or simply don't
source `.env` first — confirmed this cost real time this session before
being traced to the real cause (not a regression from this session's
code changes).

**Kelvin-only actions from this session:**
- `BREVO_API_KEY` still unset — MKT-O3 sequences remain `pending_hitl`
  forever until this is provisioned; see `BREVO_SETUP.md`.
- Confirm migration `20260921000051` applied on the next `mse-api`
  deploy (see above — could not be verified locally).
- Approve more `mse_positioning` briefs — the new gate (below) means
  MKT-O3 email generation is now blocked for every product except
  `small-portfolio-hub`.

---

## W2.5 — Product-ID reconciliation + positioning gate wiring (2026-09-21, same day)

Follow-up to the section above, per Kelvin's locked decision:
**`mse_products.id` is canonical product identity for all campaign/email
tables and the positioning gate; `opportunity_pipeline` rows map TO
products, they never own campaign identity.**

### Final canonical mapping (confirmed by Kelvin before any write)

| `mse_products.id` | slug | `opportunity_id` | basis |
|---|---|---|---|
| `ccc66fa2-ce86-4eb7-9b7c-6cbae9404c2e` | tradesdesk | `b1ebd730-a83b-48f8-96f0-e2701449677b` | exact — brief `52cfbaf2` product_name/slug match, content matches opportunity verbatim |
| `5ba0639e-4c9c-4205-be3d-7904f4dbc9b5` | small-portfolio-hub | `9d26abed-7a8a-427f-8493-be487f7eb641` | exact — brief `175e4d78` already had `opportunity_id` set |
| `7e1c9343-be82-4da6-bf38-909f8665d55d` (**new row**) | showing-signal | `797adb70-f518-41e9-8a9c-d5141691076f` | exact — real launched product, own repo, brief `cb6f9203`, never registered until this session |
| `b555a9db`, `b614ef27`, `a7e9e74b` (tradesdesk-hvac/plumbing/electrical) | — | `NULL` | no_match — sub-vertical positioning variants, no dedicated opportunity row |
| `da79dcfa` (decodedsix), `9b6c8f36` (thdagentic-consulting), `96e169fe` (ada-title-ii), `2f2ff5c8` (bible-devotional), `777a1852` (cloud-decoded) | — | `NULL` | no_match — hand-conceived / internal / consumer / non-MSE products, never went through Dispatch/Verdict |

Both orphan `mse_email_sequences` rows repointed: `29d8bab1...` (was
stamped with `mse_build_briefs.id`) → `tradesdesk`; `4817dba9...` (was
stamped with `opportunity_pipeline.id`) → the new `showing-signal` row.
FK `mse_email_sequences.product_id → mse_products.id` now enforced
(migration `20260921000052`).

**Guessed and flagged, not sourced:** `showing-signal`'s
`activation_definition` ("First automated CRM/SMS follow-up fired from a
ShowingTime event") is inferred from build brief `cb6f9203`'s pitch —
build briefs have no explicit activation-definition field. Confirm/correct
if wrong; it has no functional effect today (nothing reads it yet).

### Positioning gate (migration `20260921000053`)

Real Postgres `BEFORE INSERT` trigger on `mse_email_sequences`, backed by
a `has_approved_positioning(product_id)` helper — rejects any insert for
a product with no `mse_positioning` row at `status='approved'`. Fires
regardless of caller, including the backend's own service_role
connection (triggers are never bypassed by RLS bypass — the actual
DB-level backstop the DIST pattern calls for). No `role='admin'` check
*inside this trigger* — the caller here is always backend agent code via
service_role with no per-request JWT to check; the human-facing,
role-gated action this depends on is the existing `approve_positioning()`
function, unchanged and already role-gated. Tested with real JWTs via
`tests/sql/test_positioning_gate_live.sql` (same `set_config('request.jwt.claims', ...)`
convention as `tests/sql/test_dist_phase8_live.sql` — not wired into CI,
run manually against a branch/local Postgres).

**Real collateral-damage bug found and fixed while wiring this**:
`run_campaign_orchestrator`'s `_DOWNSTREAM_AGENTS` fan-out loop has no
per-agent isolation — one agent's uncaught exception aborts every agent
still to come. mkt-o3 (email) sat in the *middle* of that list. Since
the new gate now rejects every product except `small-portfolio-hub`,
leaving the order unchanged would have silently stopped mkt-s1 (SEO) and
mkt-v1 (social) from ever firing again for any other product's campaign
runs — a real regression, not a hypothetical one. Fixed by moving mkt-o3
to the end of the list (see the comment on `_DOWNSTREAM_AGENTS` itself);
lead-finder/DM/SEO/social now always run to completion regardless of the
email gate's outcome. Covered by new
`tests/test_mkt_orch_campaign_orchestrator.py` (this file had **zero**
prior real test coverage of the fan-out loop — `test_brief_generation.py`
only ever monkeypatches `run_campaign_orchestrator` out entirely).

### Still open, NOT resolved this session — for Kelvin, not something to silently fix

- Two more `mse_build_briefs` rows exist with no `mse_products` row and
  unclear current status: `64ed7a59` ("Series Scheduler Pro" — likely
  the Calendly recurring-booking BUILD) and `10f49a2d` ("Pulse Message
  Bridge" — plausibly the Intercom SMS/WhatsApp BUILD by content,
  unconfirmed). Their originating `opportunity_pipeline` rows have since
  been deleted (only 4 remain live total). Not registered, not mapped —
  deliberately left alone since their real-world status (launched? dead?
  still pending?) couldn't be established from the DB alone.
- Which Railway project actually hosts the live "Showing Signal" backend
  is unconfirmed — two candidates exist under Railway's auto-generated
  names (`zooming-appreciation`, `hospitable-endurance`) in the same
  workspace, neither renamed to anything identifiable. Not chased down
  this session; doesn't block anything above since the new
  `mse_products` row doesn't need a Railway project reference.

### Tests / deploy

Full suite: 629 passed, 1 skipped, 0 failed (clean env — see the testing
note above about not sourcing `.env` before pytest). 5 new tests added
this pass (4 in `test_mkt_orch_campaign_orchestrator.py`, 1 in
`test_mkt_o3_brevo.py`), zero regressions against the 624-passing
baseline from the same-day W2 session earlier.

Migrations `052`/`053` have **not been confirmed applied to the live
DB** as of this writing — same blocker as `051`: this repo's `mse-api`
Railway service is not GitHub-webhook-auto-deployed (confirmed this
session — no deployment record was ever created for commit `5c43fcd`
despite being pushed and waited on), and manual `railway up` deploys
attempted earlier today both failed at Railway's build stage
(`railpack prepare exited with an error`, no further detail surfaced in
available logs) before ever reaching the app. See the deploy attempt
right after this commit for whether a third attempt succeeded or
produced a fuller error to investigate.
