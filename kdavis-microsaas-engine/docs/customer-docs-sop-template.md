# MSE Customer-Facing Docs — SOP Template
# Applies to: docs.[productdomain].com, generated at product launch
# Stack: Nextra (preferred) or Mintlify, separate Vercel project, CNAME to docs subdomain
# Audience: the paying customer — not internal engineering documentation

---

## Purpose

Every MSE product ships with a customer-facing docs site at launch. This is the knowledge base the support agent (`agents/[product_slug]_support.py`) indexes and cites from, and the first place a customer is pointed to before any human support interaction. It must be complete enough that the support agent can answer the majority of questions from it alone.

---

## Required Page Structure

```
docs.[productdomain].com/
├── index.mdx                    Getting started — what this product does, in one paragraph
├── quickstart.mdx                First 5 minutes: signup → first value moment
├── guides/
│   ├── onboarding.mdx             Every onboarding step, screenshot per step
│   ├── core-feature-1.mdx         One page per core feature, task-oriented headings
│   ├── core-feature-2.mdx
│   ├── integrations.mdx           Any third-party connections the product supports
│   └── troubleshooting.mdx        Common issues, in Q→A format
├── billing.mdx                   Plans, upgrade/downgrade, cancellation, invoices
├── faq.mdx                       Minimum 10 Q&A pairs, FAQPage JSON-LD (see CLAUDE.md AEO rule)
└── changelog.mdx                 Dated, customer-visible product updates
```

---

## Content Rules

- Every page is written for the customer, in plain language — no internal tool names (Supabase, FastAPI, n8n, LangGraph, agent names) ever appear
- Every guide page is task-oriented: title as "How to X", not "X Feature"
- Answers are self-contained — a paragraph should make sense pulled out of context (this is what the support agent's vector search returns as a citation)
- Screenshots accompany every multi-step guide
- FAQ answers are 3 sentences or fewer, matching the AEO rule in CLAUDE.md
- No page assumes the reader has read another page first, unless explicitly linked ("see Quickstart first")
- Every page ends with a link to in-app support (chat widget) for anything not covered

---

## Billing Page — Required Content

- Full list of plans and what's included in each
- How to upgrade/downgrade (self-serve link to account settings)
- How to cancel (self-serve, no "contact us to cancel" dark patterns)
- What happens to data on cancellation (retention period, export option if applicable)
- How refunds are handled (link to policy, don't promise specifics that a human hasn't approved)

---

## FAQ Page — Sourcing

FAQ questions are not invented — they map directly to the top objections and questions surfaced during the vertical agent's research phase (see `search_signals.top_10_queries` / `objection_signals.top_5_objections` in CLAUDE.md's Verdict rule, once that schema is wired in) plus any real support tickets logged post-launch. Until `objection_signals` is wired into the research pipeline, seed the FAQ from the opportunity's original research report and competitor G2/Capterra review gaps manually at brief-generation time.

---

## Ownership and Update Cadence

- Initial docs are generated as part of the product's build (Claude Code, from the `BUILD_BRIEF_CLAUDE_CODE.md` feature list)
- Support agent's weekly learning loop (see `docs/monitoring-agent-suite.md`) surfaces top unresolved question patterns — these become new FAQ entries or new guide pages
- Changelog is updated on every customer-visible deploy, not every commit
