# EXECUTION_ORDER.md — Micro SaaS Engine
**Read this at the start of every Claude Code session. Update it at the end of every session.**

---

## Current Sprint Status

**Sprint:** 1 — Infrastructure Complete, Agent Build Pending
**Started:** 2026-07-03
**Last updated:** 2026-07-03
**Session owner:** Kelvin Davis

---

## What Was Built Last Session (Sprint 0 → Sprint 1, 2026-07-03)

- [x] CLAUDE.md, README.md, EXECUTION_ORDER.md — scaffold docs
- [x] `agents/orchestrator/prompt.md` — full orchestrator system prompt
- [x] `agents/aggregator/prompt.md` — full aggregator quality gate prompt (7 hard gates)
- [x] Full directory structure created
- [x] `supabase/migrations/001_core_schema.sql` — all 5 retention tables + RLS
- [x] `supabase/migrations/002_opportunity_pipeline.sql` — pipeline table + MRR floor DB constraint
- [x] `supabase/seed/milestone_definitions.sql` — baseline milestone seed
- [x] `core/supabase_client.py` — Supabase client singleton
- [x] `core/llm_router.py` — Haiku for scraping, Sonnet for analysis
- [x] `core/sanitization.py` — DataSanitizationShield with injection detection
- [x] `core/retention/milestone_detector.py`
- [x] `core/retention/reengagement_trigger.py`
- [x] `core/retention/digest_generator.py`
- [x] `api/main.py` — FastAPI entry point with CORS + middleware
- [x] `api/middleware/auth.py` — Supabase JWT verification
- [x] `api/middleware/tenant_context.py` — tenant_id extraction + RLS context
- [x] `api/routers/events.py` — POST /events (fires milestone detector)
- [x] `api/routers/milestones.py` — GET /milestones/{tenant_id}
- [x] `api/routers/digest.py` — POST /digest/preview/{tenant_id}
- [x] `api/routers/pipeline.py` — GET/POST /pipeline, PATCH status, POST stamp
- [x] `api/routers/mcp.py` — MCP manifest + events resource stub
- [x] `n8n/weekly-digest-workflow.json` — Sunday 20:00 MST cron
- [x] `n8n/reengagement-workflow.json` — Daily 09:00 MST cron
- [x] `frontend/components/UsageTracker.tsx` — wired into pages, silent failure
- [x] `frontend/components/MilestoneToast.tsx`
- [x] `frontend/components/WeeklySnapshot.tsx`
- [x] `frontend/app/dashboard/page.tsx`
- [x] `frontend/app/pipeline/page.tsx` — CRM view with confidence sort
- [x] `frontend/app/research/page.tsx` — agent trigger + session summary display
- [x] `docs/data-dictionary.md` — all tables documented
- [x] `docs/architecture-decisions.md` — ADR-001 through ADR-006
- [x] `requirements.txt`, `.env.example`, `.gitignore`

---

## What Is In Progress

Nothing in progress post-session.

---

## What Is Next — Session 2

### Step 1 — Supabase Setup (Kelvin runs manually)
```bash
# In the microsaas-engine project directory
supabase init
supabase link --project-ref [microsaas-prod project ref]
supabase db push supabase/migrations/001_core_schema.sql
supabase db push supabase/migrations/002_opportunity_pipeline.sql
# Verify tables exist
supabase db diff
```

### Step 2 — Python Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with real keys (Supabase, Anthropic, Resend, Stripe)
```

### Step 3 — Next.js Frontend Init
```bash
cd frontend
npx create-next-app@latest . --typescript --tailwind --app --no-src-dir
# When prompted, accept all defaults
cd ..
```
The generated `layout.tsx` needs `<UsageTracker eventType="page_view" />` added to the root layout body.

### Step 4 — Start API and Test Endpoints
```bash
source venv/bin/activate
uvicorn api.main:app --reload --port 8000

# In another terminal:
curl http://localhost:8000/health
# Expected: {"status": "ok"}

curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer [supabase-jwt]" \
  -d '{"event_type": "test_event", "metadata": {"source": "curl"}}'
```

### Step 5 — Import n8n Workflows
1. Open n8n at http://localhost:5678
2. Import `n8n/weekly-digest-workflow.json`
3. Import `n8n/reengagement-workflow.json`
4. Configure Supabase and Resend credentials in n8n credential manager
5. Test weekly digest with a real tenant

### Step 6 — Research Orchestrator Agent (Thursday build)
- [ ] `agents/orchestrator/agent.py` — LangGraph graph using orchestrator/prompt.md
- [ ] `agents/aggregator/agent.py` — quality gate logic from aggregator/prompt.md
- [ ] Test single vertical (healthcare) before full swarm
- [ ] Add `POST /research/run` router to FastAPI
- [ ] Verify READY_TO_BUILD stamps write to opportunity_pipeline

### Verification Checklist (Session 2 Complete When All Pass)
- [ ] All 6 Supabase tables exist with RLS enabled
- [ ] POST /events accepts and stores a usage event
- [ ] GET /milestones/{tenant_id} returns milestone state
- [ ] Weekly digest n8n workflow runs and sends to test email
- [ ] Re-engagement workflow fires on simulated 7-day gap
- [ ] UsageTracker wired into root layout.tsx
- [ ] GET /health returns 200
- [ ] git status is clean, all changes committed

---

## Blocked Items

None currently. First session has no blockers.

**Prerequisite reminder:** This scaffold is ready to build. The research agent activation (Thursday build night) is sequenced after Stripe billing closes on Cloud Decoded. The scaffold itself — database, API, retention infrastructure, frontend — can be built now in a Saturday sprint.

---

## Decisions Made This Sprint

| Decision | Reason | Date |
|----------|--------|------|
| Isolated Supabase project | Exit architecture — product must be independently acquirable | July 2026 |
| MRR floor as DB constraint | Enforce at infrastructure level, not just application logic | July 2026 |
| Retention scaffold before feature work | Non-negotiable — churn problem solved at architecture level | July 2026 |
| Haiku for scraping, Sonnet for analysis | Cost optimization — high-volume tasks use lowest capable model | July 2026 |

---

## Agent Build Cadence (Thursday Nights)

| Week | Agent | Status |
|------|-------|--------|
| 1 | Research Orchestrator | Not started |
| 2 | Healthcare/Medical Desk Intel | Not started |
| 3 | Legal/Professional Services Intel | Not started |
| 4 | E-commerce/Retail Intel | Not started |
| 5 | Real Estate Intel | Not started |
| 6 | HR/Ops Intel | Not started |
| 7 | Finance/Accounting Intel | Not started |
| 8 | Aggregator + Quality Gate | Not started |

---

## End of Session Protocol

Before closing any Claude Code session:
1. Update "What Was Built Last Session" above
2. Update "What Is In Progress"
3. Update "What Is Next" — remove completed items, add newly discovered tasks
4. Update agent build cadence status
5. Add any new decisions to the decisions table
6. Commit EXECUTION_ORDER.md: `git commit -m "chore: update execution order post-session"`
