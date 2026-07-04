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
- **Model routing:** Haiku for high-volume scraping, Sonnet for analysis — do not swap

---

## Architecture Rules — Non-Negotiable

1. `tenant_id` on every table — RLS enforced, no exceptions
2. Dedicated Stripe account for MSE — never Decoded Holdings, never Cloud Decoded
3. Every agent emits `POST /events` on every state change — CEO dashboard depends on this
4. `get_supabase_for_request(jwt)` in all API routes touching tenant data — never service role in routes
5. DataSanitizationShield runs before any data embedding
6. Hard $4K MRR floor enforced at DB constraint level on every product this factory ships
7. 6 retention loops ship before any feature work on any product
8. Haiku for scraping, Sonnet for analysis — do not swap models

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
