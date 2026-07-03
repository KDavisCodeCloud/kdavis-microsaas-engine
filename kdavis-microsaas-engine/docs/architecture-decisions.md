# Architecture Decisions — Micro SaaS Engine
Exit diligence document. Updated in the same commit as the decision it documents.

---

## ADR-001: Isolated Supabase Project
**Date:** July 2026
**Status:** Active

**Decision:** Micro SaaS Engine uses a dedicated Supabase project (`microsaas-prod`), never shared with Cloud Decoded, DecodedSix, or any other portfolio product.

**Why:** Product must be independently acquirable. A buyer should be able to lift this product — database, schema, RLS policies, tenants, usage history — without touching any other Decoded Empire asset. Shared DB creates an inseparable dependency that kills exit flexibility and requires database surgery before any acquisition.

**Consequences:** Each product has its own Supabase project billing. Command Center integration is API-only, never direct DB access.

---

## ADR-002: MRR Floor Enforced at Database Level
**Date:** July 2026
**Status:** Active

**Decision:** `opportunity_pipeline` has a CHECK constraint: `conservative_mrr_potential >= 4000 OR status = 'rejected'`. The application-layer filter in the aggregator agent is a second layer, not the first.

**Why:** Application logic can drift. Developers override it. The DB constraint cannot be bypassed without an explicit migration. A future agent, a future dev, or a future AI assistant cannot accidentally insert a sub-$4K opportunity that reaches the build backlog.

**Consequences:** Any attempt to insert an opportunity with MRR < 4000 into any status other than `rejected` throws a database error. This is intentional.

---

## ADR-003: Retention Scaffold Before Feature Work
**Date:** July 2026
**Status:** Active

**Decision:** No product feature is built until `usage_events`, `milestones`, `retention_sequences`, `weekly_digest_log` tables exist with RLS active, weekly digest n8n workflow is tested, and `UsageTracker` is wired into root layout.

**Why:** Retention bolted on after launch has a documented failure pattern: by the time you add it, churn data is already gone and customers have no behavioral history. The weekly digest requires event history to send. The milestone system requires event counts from day one. Retrofitting these is 3× the work and produces inferior data.

**Consequences:** First sprint is all infrastructure. Feature work starts in sprint 2 or later.

---

## ADR-004: Haiku for Scraping, Sonnet for Analysis
**Date:** July 2026
**Status:** Active

**Decision:** Vertical research agents use `claude-haiku-4-5` for raw data ingestion and web scraping tasks. Structured analysis, JSON extraction, scoring, and digest generation use `claude-sonnet-4-6`. Opus is not used for routine operations.

**Why:** Research agent swarm runs 6 vertical agents in parallel. At Haiku-volume pricing, scraping cost is 8–10× cheaper than Sonnet. Quality delta for raw extraction tasks is negligible. Sonnet is reserved for tasks where structured, high-quality output matters (scoring, analysis, customer-facing content).

**Consequences:** `core/llm_router.py` enforces routing. Every agent call must route through it. Direct Anthropic client calls outside the router are a violation of this ADR.

---

## ADR-005: MCP Endpoint Ships With Every Product
**Date:** July 2026
**Status:** Active

**Decision:** Every micro SaaS product exposes an MCP server manifest at `/mcp/manifest` from day one.

**Why:** MCP integration is the retention mechanism identified by the research agent as "natural integration lock." Once a tenant's data flows bidirectionally through an MCP endpoint into their existing toolchain (Claude Desktop, internal agents), switching cost becomes real. Deferring MCP to a later sprint means early customers never get integration locked — they churn instead.

**Consequences:** MCP router is initialized at app startup. It is not feature-flagged. The initial implementation is a stub with manifest + events read. Full action surface is expanded per product vertical in sprint 2+.

---

## ADR-006: Dedicated Stripe Account Per Product
**Date:** July 2026
**Status:** Active

**Decision:** Each micro SaaS product has its own Stripe account and webhook configuration.

**Why:** Commingled Stripe revenue makes per-product MRR tracking unreliable, complicates acquisition due diligence (buyer wants clean product-specific revenue history), and creates webhook routing complexity at scale. A buyer acquiring one product must not inherit billing entanglements with other products.

**Consequences:** Separate Stripe accounts require separate API keys in env config. Webhook endpoints are product-specific. Admin overhead is higher but acquisition cleanliness justifies it.
