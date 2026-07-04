# Micro SaaS Engine — Build Order
**Project:** `kdavis-microsaas-engine`
**Company:** THD Agentic Systems LLC
**Last updated:** 2026-07-04
**Status:** Active build — infrastructure complete, agent cadence started

---

## What This Is

A research-validated, retention-first software factory producing 1–2 micro-SaaS products per month. Primary purpose right now: generate low-ticket subscription revenue ($29–$79/mo) fast enough to cover the operating stack (~$165–$255/mo) before Cloud Decoded closes its first B2B deal. Every product MSE ships has a hard $4K MRR floor enforced at the DB constraint level and 6 retention loops shipping before any feature work.

**Self-funding milestone:** 6 MSE clients at blended pricing covers the full operating stack.

---

## Completed — Do Not Redo

- GAP 1 — venv + pip install ✅
- GAP 2 — .env fully filled (all keys including ANTHROPIC, RESEND, SUPABASE_ANON_KEY) ✅
- GAP 3 — Dedicated MSE Stripe account created (Micro Saas Decoded, live mode) ✅
- GAP 4 — Stripe webhook handler built (`api/routers/stripe.py`) ✅
- GAP 5 — Node.js v22 installed via nvm ✅
- GAP 6 — Supabase CLI installed + linked to microsaas-prod ✅
- GAP 7 — Migrations pushed — 6 tables live with RLS (migration 003 applied: auth.uid()) ✅
- GAP 8 — API smoke test passed (/health 200, POST /events inserts row) ✅
- GAP 9 — Next.js 15 initialized + UsageTracker wired into root layout ✅
- GAP 10 — n8n 2.28.6 installed, both workflows active, Supabase credential saved ✅
- GAP 11 — RLS fix: `get_supabase_for_request(jwt)` built, anon key enforces per-tenant isolation ✅
- GAP 12 — Legal docs: EULA, privacy-policy, DPA template created (AZ/Maricopa pre-filled) ✅

---

## Active — Agent Cadence (GAP 13)

### Week 1 — 2026-07-04 (NOW)

**Orchestrator agent**
File: `agents/orchestrator/agent.py`
- LangGraph state machine
- Receives research run request, dispatches to vertical intel agents in parallel
- Collects outputs, passes to aggregator
- Emits `POST /events` on every state change

**Aggregator agent**
File: `agents/aggregator/agent.py`
- Runs 7 quality gates against orchestrator output
- Issues `READY_TO_BUILD` stamp only when all 7 pass
- Writes validated opportunity to `opportunity_pipeline` table
- Rejects below $4K MRR floor

**Wire `/research/run`**
File: `api/routers/research.py`
- `POST /research/run` → calls orchestrator → returns session ID
- `GET /research/session/{id}` → returns current state + result

---

### Weeks 2–7 — Vertical Intel Agents (one per Thursday)

| Week | Date | Agent |
|---|---|---|
| 2 | 2026-07-10 | Market sizing agent |
| 3 | 2026-07-17 | Competitor signal agent |
| 4 | 2026-07-24 | ICP research agent |
| 5 | 2026-07-31 | Retention pattern agent |
| 6 | 2026-08-07 | Pricing signal agent |
| 7 | 2026-08-14 | Distribution channel agent |
| 8 | 2026-08-21 | Full swarm end-to-end test |

---

## Key Constraints (Do Not Violate)

- Dedicated Stripe account for MSE — never share with Cloud Decoded or Decoded Holdings
- Hard $4K MRR floor enforced at DB constraint level on every product
- 6 retention loops ship before any feature work on any product
- RLS enforced via `get_supabase_for_request(jwt)` in all routes — never service role in API routes
- Every agent emits `POST /events` on state change — CEO dashboard depends on this
- Model routing: Haiku for high-volume scraping, Sonnet for analysis — do not swap
- DataSanitizationShield before every LLM call — no exceptions
