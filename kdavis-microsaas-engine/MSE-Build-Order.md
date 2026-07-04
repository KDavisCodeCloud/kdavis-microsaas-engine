# Micro SaaS Engine — Build Order
**Project:** `kdavis-microsaas-engine`
**Company:** THD Agentic Systems LLC
**Last updated:** 2026-07-04
**Status:** Active build — GAPs 1–10 complete, GAPs 4/11/12 next

---

## What This Is

A research-validated, retention-first software factory producing 1–2 micro-SaaS products per month. Primary purpose right now: generate low-ticket subscription revenue ($29–$79/mo) fast enough to cover the operating stack (~$165–$255/mo) before Cloud Decoded closes its first B2B deal. Every product MSE ships has a hard $4K MRR floor enforced at the DB constraint level and 6 retention loops shipping before any feature work.

**Self-funding milestone:** 6 MSE clients at blended pricing covers the full operating stack.

---

## Completed — Do Not Redo

- GAP 1 — venv + pip install ✅
- GAP 2 — .env partially filled (Supabase URL, service key, JWT secret, Stripe test key) ✅
- GAP 3 — Dedicated MSE Stripe account created, CLI re-authed, sk_test in .env ✅
- GAP 5 — Node.js installed via nvm ✅
- GAP 6 — Supabase CLI installed ✅
- GAP 7 — Supabase project linked + migrations pushed (6 tables, RLS enabled) ✅
- GAP 8 — API smoke test passed (/health 200, POST /events inserts row) ✅
- GAP 9 — Next.js initialized + UsageTracker wired into root layout ✅
- GAP 10 — n8n installed + weekly-digest + reengagement workflows imported ✅

---

## Manual Steps Required Before Next Claude Code Session

Kelvin completes these first — Claude Code cannot proceed without them:

1. Fill `ANTHROPIC_API_KEY` in `.env` → console.anthropic.com
2. Fill `RESEND_API_KEY` in `.env` → resend.com
3. n8n first-run setup at http://localhost:5678 → create owner account
4. n8n → Credentials → add Supabase credential (`microsaas-supabase`) with URL + service_role key
5. n8n → update `RESEND_API_KEY` in `n8n/start-n8n.sh` → activate both workflows

---

## Build Order — Remaining

### Session: Next Claude Code Session

**GAP 4 — Stripe webhook handler**
File: `api/routers/stripe.py`
Events to handle:
- `subscription.created` → create tenant row
- `subscription.updated` → update tier
- `subscription.deleted` → mark churned
- `invoice.payment_failed` → log + trigger n8n re-engagement workflow
- Verify Stripe signature on every request
- Write results to `tenants` table

**GAP 11 — RLS fix**
File: `core/supabase_client.py`
- Refactor to expose `get_supabase_admin()` (service role, bypasses RLS — internal use only)
- Add `get_supabase_for_request(jwt)` (user-scoped, RLS enforces per tenant)
- Every API route that touches tenant data must use `get_supabase_for_request`

**GAP 12 — Legal documents**
Files: `legal/EULA.md`, `legal/privacy-policy.md`, `legal/dpa-template.md`
- EULA: usage rights, prohibited uses, IP ownership, termination
- Privacy policy: data collected, retention, deletion, third-party services (Stripe, Supabase, Anthropic)
- DPA template: for enterprise/B2B deals, GDPR-compatible

---

### Thursday 2026-07-10 — Agent Cadence Night (GAP 13 begins)

**Week 1: Orchestrator + Aggregator**
Files: `agents/orchestrator/agent.py`, `agents/aggregator/agent.py`
- Wire `/research/run` endpoint
- Orchestrator coordinates vertical intel agents
- Aggregator runs quality gate before any output is accepted
- Both emit `POST /events` on every state change → CEO dashboard feed

**Weeks 2–7: Vertical Intel Agents (one per Thursday)**
- Week 2: Market sizing agent
- Week 3: Competitor signal agent
- Week 4: ICP research agent (also runs Compass Decoded ICP research)
- Week 5: Retention pattern agent
- Week 6: Pricing signal agent
- Week 7: Distribution channel agent

**Week 8: Full swarm test**
- Run all 7 vertical agents under orchestrator
- Aggregator quality gate validates output
- End-to-end: research run → product opportunity report → CEO dashboard

---

## Architecture Reference

```
kdavis-microsaas-engine/
├── api/
│   ├── main.py               # FastAPI app
│   ├── routers/
│   │   ├── events.py         # POST /events ✅
│   │   ├── stripe.py         # GAP 4 — next session
│   │   └── research.py       # GAP 13 — Thu 7/10
├── core/
│   ├── supabase_client.py    # GAP 11 — next session
│   └── config.py
├── agents/
│   ├── orchestrator/         # GAP 13 — Thu 7/10
│   └── aggregator/           # GAP 13 — Thu 7/10
├── frontend/                 # Next.js ✅
├── n8n/                      # workflows ✅
├── legal/                    # GAP 12 — next session
└── .env                      # partially filled — manual steps above
```

---

## Key Constraints (Do Not Violate)

- Dedicated Stripe account for MSE — never share with Cloud Decoded or Decoded Holdings
- Hard $4K MRR floor enforced at DB constraint level on every product
- 6 retention loops ship before any feature work on any product
- RLS must be enforced per `tenant_id` on every table — never bypass with service role in API routes
- Every agent emits `POST /events` on state change — CEO dashboard depends on this
- Model routing: Haiku for high-volume scraping, Sonnet for analysis — do not swap

---

## Operating Stack Context

MSE is the primary revenue engine covering the shared operating stack while Cloud Decoded closes its first B2B deal. Target: 6 clients at blended pricing = stack self-funded. Every shipped product needs fast trial-to-paid conversion and retention past month 2.
