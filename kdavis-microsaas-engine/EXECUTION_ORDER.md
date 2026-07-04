# EXECUTION_ORDER.md — Micro SaaS Engine
**Read this at the start of every Claude Code session. Update it at the end of every session.**

---

## Current Sprint Status

**Sprint:** 3 — Code Gaps + Thursday Agent Build
**Started:** 2026-07-03
**Last updated:** 2026-07-03
**Session owner:** Kelvin Davis

---

## What Was Built (Cumulative — All Claude Sessions)

### Session 1 (2026-07-03)
- [x] CLAUDE.md, README.md, EXECUTION_ORDER.md, docs/data-dictionary.md, docs/architecture-decisions.md
- [x] agents/orchestrator/prompt.md, agents/aggregator/prompt.md
- [x] supabase/migrations/001_core_schema.sql — 5 retention tables + RLS policies
- [x] supabase/migrations/002_opportunity_pipeline.sql — pipeline table + MRR floor DB constraint
- [x] supabase/seed/milestone_definitions.sql
- [x] core/supabase_client.py, core/llm_router.py, core/sanitization.py
- [x] core/retention/milestone_detector.py, reengagement_trigger.py, digest_generator.py
- [x] api/main.py, api/middleware/auth.py, api/middleware/tenant_context.py
- [x] api/routers/events.py, milestones.py, digest.py, pipeline.py, mcp.py
- [x] n8n/weekly-digest-workflow.json, n8n/reengagement-workflow.json
- [x] frontend/components/UsageTracker.tsx, MilestoneToast.tsx, WeeklySnapshot.tsx
- [x] frontend/app/dashboard/page.tsx, pipeline/page.tsx, research/page.tsx
- [x] requirements.txt, .env.example, .gitignore
- [x] GitHub repo created + pushed

### Session 2 (2026-07-03)
- [x] Fixed auth.py:19 bug (invalid HTTPException kwarg `error_code`)
- [x] api/routers/reengagement.py — POST /reengagement/evaluate/{tenant_id} (n8n cron target)
- [x] api/routers/research.py — POST /research/run + GET /research/session/{id} (frontend target)
- [x] api/main.py — wired reengagement + research routers

### Session 3 (2026-07-03) — Infrastructure Sprint
- [x] All Python packages installed (supabase, langgraph, langchain-anthropic, resend, stripe)
- [x] .env created — Supabase + Stripe keys filled (ANTHROPIC + RESEND still needed)
- [x] Stripe dedicated account created: Micro Saas Decoded (acct_1TpLcKLIpoJRr7Tc)
- [x] Supabase project microsaas-prod created + CLI linked + both migrations pushed
- [x] All 6 tables live with RLS confirmed (tenants, usage_events, milestones, retention_sequences, weekly_digest_log, opportunity_pipeline)
- [x] API smoke tested: /health 200, /docs 200, POST /events writes to prod DB (e2e confirmed)
- [x] Fixed tenant_context.py: /docs and /openapi.json excluded from JWT auth
- [x] Node.js v22 + v24 installed via nvm
- [x] Next.js 15 initialized: 4 routes live, UsageTracker wired into root layout
- [x] n8n 2.28.6 installed + @langchain/core exports patched + both workflows imported
- [x] Empire dashboard updated (003_update_2026_07_03_session3.sql)

---

## What Is In Progress

Nothing in progress. Next action: run 003_update_2026_07_03_session3.sql in empire-dashboard Supabase, then open next Claude session for code gaps.

---

## MANUAL SETUP RUNBOOK
**These steps require Kelvin to run in the terminal. Claude cannot execute them.**
**Complete all steps in order before next Claude Code session.**

---

### STEP 1 — Python Environment
**Time: ~5 minutes**

```bash
cd /mnt/c/Users/Kelvin/projects/kdavis-microsaas-engine

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Verify it worked:
```bash
python -c "import fastapi, supabase, anthropic, langgraph; print('all imports OK')"
```
Expected output: `all imports OK`

If any package fails, check your Python version first: `python3 --version` (need 3.11+).

---

### STEP 2 — Environment Variables
**Time: ~10 minutes (gathering keys)**

```bash
cp .env.example .env
```

Open `.env` and fill in every value. Here's where to get each one:

| Variable | Where to get it |
|----------|----------------|
| `SUPABASE_URL` | Supabase dashboard → microsaas-prod project → Settings → API → Project URL |
| `SUPABASE_SERVICE_KEY` | Supabase dashboard → Settings → API → service_role key (secret) |
| `SUPABASE_JWT_SECRET` | Supabase dashboard → Settings → API → JWT Secret |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `RESEND_API_KEY` | resend.com → API Keys |
| `STRIPE_SECRET_KEY` | dashboard.stripe.com → Developers → API keys → Secret key (use the microsaas-specific Stripe account) |
| `STRIPE_WEBHOOK_SECRET` | dashboard.stripe.com → Developers → Webhooks → signing secret (create endpoint first) |
| `ALLOWED_ORIGINS` | `http://localhost:3000` for now, add prod domain later |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` for local dev |

**Never commit `.env` to git. `.gitignore` already excludes it.**

---

### STEP 3 — Supabase Project Init and Migrations
**Time: ~15 minutes**

**Prerequisites:** Supabase CLI installed. If not: `npm install -g supabase`

```bash
# From the project root
supabase init
```
This creates `supabase/config.toml`. Accept all defaults.

```bash
# Link to your microsaas-prod project
# Find your project ref in Supabase dashboard URL: app.supabase.com/project/[YOUR-REF]
supabase link --project-ref YOUR_PROJECT_REF_HERE
```
When prompted, enter your database password (from Supabase dashboard → Settings → Database).

```bash
# Push both migrations
supabase db push supabase/migrations/001_core_schema.sql
supabase db push supabase/migrations/002_opportunity_pipeline.sql
```

Verify all tables exist:
```bash
supabase db diff
```

You should see NO diff (everything is in sync). If you see a diff, the migration didn't apply — check for errors in the push output.

Double-check in Supabase dashboard → Table Editor that these 6 tables exist:
- `tenants`
- `usage_events`
- `milestones`
- `retention_sequences`
- `weekly_digest_log`
- `opportunity_pipeline`

Verify RLS is ON for each table: Table Editor → click each table → Auth policies → should show "RLS enabled."

**Run seed data for milestones:**
```bash
# After creating at least one test tenant, run this in Supabase SQL editor:
# Copy contents of supabase/seed/milestone_definitions.sql and execute
```

---

### STEP 4 — Start and Test the API
**Time: ~10 minutes**

```bash
source venv/bin/activate
uvicorn api.main:app --reload --port 8000
```

In a second terminal, verify the health check:
```bash
curl http://localhost:8000/health
# Expected: {"status":"ok"}
```

View all routes at: http://localhost:8000/docs (FastAPI auto-generated docs)

You should see these route groups:
- `events` — POST /events
- `milestones` — GET /milestones/{tenant_id}
- `digest` — POST /digest/preview/{tenant_id}
- `pipeline` — GET/POST /pipeline, PATCH /pipeline/{id}/status, POST /pipeline/{id}/stamp
- `mcp` — GET /mcp/manifest, GET /mcp/resources/events
- `reengagement` — POST /reengagement/evaluate/{tenant_id}
- `research` — POST /research/run, GET /research/session/{session_id}
- `GET /health`

Test POST /events (requires a valid Supabase JWT — get one by logging in via Supabase Auth in your test app, or generate one in the Supabase dashboard under Authentication → Users):
```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_SUPABASE_JWT" \
  -d '{"event_type": "test_event", "metadata": {"source": "curl"}}'
# Expected: {"id": "uuid...", "milestones_achieved": [...]}
```

---

### STEP 5 — Next.js Frontend Init
**Time: ~10 minutes**

```bash
cd /mnt/c/Users/Kelvin/projects/kdavis-microsaas-engine/frontend
npx create-next-app@latest . --typescript --tailwind --app --no-src-dir
```

When prompted:
- ESLint: **Yes**
- Tailwind: **Yes** (already selected)
- `src/` directory: **No**
- App Router: **Yes**
- Customize import alias: **No** (use `@/`)

This generates `layout.tsx`, `page.tsx`, `globals.css`, `next.config.ts`, `package.json`, `tsconfig.json`, `node_modules/`.

**Wire UsageTracker into root layout** — open `frontend/app/layout.tsx` and add:

```tsx
// At the top of the file, add this import:
import { UsageTracker } from "@/components/UsageTracker";

// Inside the <body> tag, add this as the first child:
<UsageTracker eventType="page_view" />
```

The full body section should look like:
```tsx
<body className={inter.className}>
  <UsageTracker eventType="page_view" />
  {children}
</body>
```

Then start the dev server:
```bash
cd frontend
npm run dev
# Opens at http://localhost:3000
```

Verify pages load:
- http://localhost:3000/dashboard
- http://localhost:3000/pipeline
- http://localhost:3000/research

---

### STEP 6 — Import n8n Workflows
**Time: ~20 minutes**

**Prerequisite:** n8n running at http://localhost:5678. If not installed:
```bash
npm install -g n8n
n8n start
```

**Import weekly digest workflow:**
1. Open http://localhost:5678
2. Click "Workflows" in sidebar → "Add workflow" → "Import from file"
3. Select `n8n/weekly-digest-workflow.json`
4. Click "Save"

**Import reengagement workflow:**
1. Same steps, select `n8n/reengagement-workflow.json`
2. Click "Save"

**Configure credentials (do this once, both workflows use the same ones):**

For Supabase nodes:
1. n8n sidebar → Credentials → Add credential → Supabase
2. Name: `microsaas-supabase`
3. Host: your Supabase URL (from .env `SUPABASE_URL`)
4. Service Role Secret: your service role key (from .env `SUPABASE_SERVICE_KEY`)

For HTTP request nodes (Resend email):
1. Credentials → Add credential → Generic HTTP Header Auth
2. Name: `resend-auth`
3. Header Name: `Authorization`
4. Header Value: `Bearer YOUR_RESEND_API_KEY`

**Set environment variables in n8n** (Settings → Variables):
| Variable | Value |
|----------|-------|
| `API_BASE_URL` | `http://localhost:8000` |
| `RESEND_API_KEY` | your Resend key |
| `PRODUCT_DOMAIN` | your product domain or `localhost` for now |
| `APP_URL` | `http://localhost:3000` |

**Activate both workflows** — toggle "Active" switch on each workflow.

**Test weekly digest manually:**
1. Open the weekly-digest workflow
2. Click "Execute Workflow" (top right)
3. Watch node outputs — should pull tenants, call /digest/preview, send or log skip
4. Check Supabase `weekly_digest_log` table — should see a row

**Test reengagement manually:**
1. Open the reengagement workflow
2. Click "Execute Workflow"
3. Check Supabase `retention_sequences` table — should see rows for any tenants with no recent usage

---

### STEP 7 — Stripe Webhook (Before Taking Any Payment)
**Time: ~15 minutes setup, then Claude builds the handler**

This step requires:
1. A dedicated Stripe account for this product (separate from Cloud Decoded)
2. A deployed API endpoint (Render or Railway — see deploy step below) OR use Stripe CLI for local testing

**Local testing with Stripe CLI:**
```bash
# Install Stripe CLI
curl -s https://packages.stripe.com/api/stripe-cli-gpg-key.asc | gpg --dearmor | sudo tee /usr/share/keyrings/stripe.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/stripe.gpg] https://packages.stripe.com/stripe-cli-debian-track stable main" | sudo tee /etc/apt/sources.list.d/stripe.list
sudo apt update && sudo apt install stripe

stripe login
stripe listen --forward-to http://localhost:8000/stripe/webhook
```

The CLI will print a webhook signing secret — add it to `.env` as `STRIPE_WEBHOOK_SECRET`.

**Then tell Claude to build `api/routers/stripe.py`** — Claude will build the subscription lifecycle handler (create tenant on `customer.subscription.created`, update tier on `customer.subscription.updated`, mark churned on `customer.subscription.deleted`).

---

## What Claude Builds Next Session

Priority order — complete in one session:

- [ ] RLS fix: refactor `core/supabase_client.py` to support per-request authenticated Supabase client (service_role for admin ops, user JWT for tenant-scoped queries) — this closes the tenant isolation gap
- [ ] `api/routers/stripe.py` — POST /stripe/webhook + tenant lifecycle handlers
- [ ] `frontend/app/layout.tsx` additions — if Next.js is initialized, wire final layout structure
- [ ] Legal docs: `legal/EULA.md`, `legal/privacy-policy.md`, `legal/dpa-template.md`

**Then Thursday agent build night:**
- [ ] `agents/orchestrator/agent.py` — LangGraph graph
- [ ] `agents/aggregator/agent.py` — 7-gate quality filter runner
- [ ] Wire `_run_orchestrator_stub` in research.py to real orchestrator
- [ ] Test with single vertical (healthcare) before full swarm

---

## Open Gaps (Tracking)

| Gap | Severity | Status |
|-----|----------|--------|
| ANTHROPIC_API_KEY missing from .env | Blocks LLM calls | Kelvin: console.anthropic.com |
| RESEND_API_KEY missing from .env | Blocks email sending | Kelvin: resend.com |
| n8n first-run setup not done | Blocks workflow activation | Kelvin: localhost:5678 |
| n8n Supabase credential not configured | Blocks workflow DB access | Kelvin: n8n UI |
| n8n workflows not activated | Retention loops not firing | Kelvin: n8n UI after credential setup |
| RLS context not set per-request | High — service_role bypasses RLS | Claude builds next session |
| Stripe webhook handler missing | Blocks payment processing | Claude builds next session |
| Legal docs missing | Required before launch | Claude builds next session |
| All agent.py files missing | Blocks research swarm | Thursday build cadence |

---

## Decisions Made

| Decision | Reason | Date |
|----------|--------|------|
| Isolated Supabase project | Exit architecture — product must be independently acquirable | 2026-07-03 |
| MRR floor as DB constraint | Enforce at infrastructure level, not just application logic | 2026-07-03 |
| Retention scaffold before feature work | Non-negotiable — churn problem solved at architecture level | 2026-07-03 |
| Haiku for scraping, Sonnet for analysis | Cost optimization — high-volume tasks use lowest capable model | 2026-07-03 |
| research.py ships as stub | Orchestrator agent.py doesn't exist yet — stub returns session_id, wires to real agent on Thursday | 2026-07-03 |
| service_role for admin/n8n ops | Admin operations (aggregator stamp, n8n workflows) bypass RLS intentionally | 2026-07-03 |

---

## Agent Build Cadence (Thursday Nights)

| Week | Agent | Deliverable | Status |
|------|-------|-------------|--------|
| 1 | Orchestrator + Aggregator | agent.py for both, /research/run fully wired | Not started |
| 2 | Healthcare/Medical Desk Intel | prompt.md + agent.py | Not started |
| 3 | Legal/Professional Services Intel | prompt.md + agent.py | Not started |
| 4 | E-commerce/Retail Intel | prompt.md + agent.py | Not started |
| 5 | Real Estate Intel | prompt.md + agent.py | Not started |
| 6 | HR/Ops Intel | prompt.md + agent.py | Not started |
| 7 | Finance/Accounting Intel | prompt.md + agent.py | Not started |
| 8 | Full swarm end-to-end test | All 6 verticals → aggregator → pipeline → READY_TO_BUILD | Not started |

---

## End of Session Protocol

Before closing any Claude Code session:
1. Update "What Was Built" with session date
2. Update "What Is In Progress"
3. Update "Open Gaps" table
4. Update agent build cadence status
5. Add new decisions to decisions table
6. Commit: `git commit -m "chore: update execution order post-session"`
