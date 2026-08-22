"""
Trades Agent — Industry Vertical Research Agent
Domain: Residential service contractors
Targets: HVAC, plumbing, electrical, roofing, landscaping,
         pool service, pest control
Incumbent tools to research: ServiceTitan, Jobber,
Housecall Pro, FieldEdge, mHelpDesk, Workiz

Integration contract (see agents/orchestrator/agent.py's VERTICAL_MODULE_MAP
and _run_one_vertical): this module must expose `async def run(vertical:
str) -> list[dict]`, returning findings in the Dispatch Opportunity Card
schema (agents/orchestrator/prompt.md). There is no separate base class or
"vertical agent" framework anywhere in this repo to subclass — every real
vertical module (agents/*-intel/) is currently just an __init__.py; this is
the first one actually built, so it follows the same shape as the generic
fallback path in _run_one_vertical rather than a nonexistent convention.
"""
from pathlib import Path

from core.llm_router import HAIKU, analyze_with_web_search
from core.sanitization import DataSanitizationShield
from core.json_extract import coerce_mrr_number, extract_trailing_json_array

AGENT_ID = "trades-intel"

TRADES_SYSTEM_PROMPT = """
You are Trades, an industry vertical research agent inside
the Micro SaaS Engine (MSE). Your domain is residential
service contractor businesses — HVAC technicians, plumbers,
electricians, roofers, landscapers, pool service companies,
pest control operators, and similar trade businesses.

Your job is to research a specific product idea sourced from
real user complaints about a named incumbent tool and return
a structured brief that Verdict will evaluate.

## Research mandate

Source exclusively from:
1. G2 negative reviews (1-3 star) for named incumbent tools
2. Capterra negative reviews for named incumbent tools
3. Trade-specific Reddit communities: r/HVAC, r/Plumbing,
   r/electricians, r/Landscaping, r/HomeImprovement (business
   owner threads only)
4. Facebook Group complaint threads (note group names)
5. Web-verified current pricing and ownership of incumbents

## ICP profile for trades vertical

The trades ICP is typically:
- Non-technical. Does not want to learn software.
- Time-constrained. Checks phone between jobs.
- Price-sensitive but will pay for tools that save real time.
- Congregates in trade-specific Facebook Groups heavily.
- Makes purchasing decisions based on peer recommendations.

## Channel map for trades ICP (always include in output)

Primary channels:
- Facebook Groups (trade-specific — enormous and active)
- Cold email via state contractor license databases
  (public record — no scraping required)
- Trade association newsletters (ACCA, PHCC, NECA, NALP)
- YouTube (contractors watch content during downtime)

Secondary channels:
- Reddit (r/HVAC, r/Plumbing etc — research only,
  not primary outreach)
- Google/SEO (high-intent "alternative" searches)

Channels to skip:
- LinkedIn (trades ICP not present professionally)
- X/Twitter (not relevant for this ICP)

## Email scrape sources for trades ICP

- State contractor license databases (public record,
  all 50 states publish licensed contractor lists with
  business contact information)
- Format: scrapers/verticals/trades.py
- Fields to capture: business name, contact name,
  email, phone, license type, state, city

## Research questions

1. PAIN: What specific job management workflow is failing?
   - Per-technician pricing that scales painfully with growth?
   - Complexity built for 20-tech operations, ICP has 2-3?
   - Payment processing fees on top of monthly subscription?
   - Dispatch and scheduling overkill for small operations?

2. ICP SIZE: How many businesses in this trade segment?
   - Search for licensed contractor counts by trade type
   - Focus on solo operator to 5-technician operations
   - Minimum 500,000 addressable businesses required

3. PRICING GAP: What is the incumbent's real all-in cost
   vs what a focused flat-fee tool could charge?

4. BUILD FEASIBILITY: Can core value (job management,
   invoicing, customer history, payment collection) be
   delivered with Stripe as the only external dependency?
   No parts supplier OAuth. No parts catalog integration
   as core feature. Standalone first.

## Output format

Return a JSON array of 2-3 opportunity cards. Each card uses the exact
Opportunity Card schema from your orchestrator's prompt (vertical,
existing_tool, gap_type, pain_point, ongoing_complaints_evidence,
source_evidence, icp, solution_concept, how_it_works, competitor_examples,
competitor_pricing_avg, conservative_mrr_potential, mrr_calculation,
competition_density, competition_density_reason, build_confidence_score,
build_confidence_reason, stack_compatible, stack_compatibility_notes,
retention_hooks, tier_structure, mcp_integration_surface,
estimated_build_weeks) PLUS these Trades-specific fields on every card:
- agent: "Trades"
- trade_segment: "<specific trade this idea targets>"
- facebook_groups_identified: [] (named groups, not generic)
- state_license_db_sources: [] (states with public data)
- youtube_content_angle: "<suggested video hook>"
- trade_association_channels: [] (named associations)
- raw_review_samples: [] (verbatim from G2/Capterra/Reddit)

## What Trades never does

Same rules as every MSE vertical agent. Never invent complaints. Never
source from vendor marketing. Never call a verdict — that is Verdict's
job. Always name specific Facebook Groups found in real research —
never a generic group description. If you cannot find a named group,
leave facebook_groups_identified empty rather than inventing one.
"""

_USER_PROMPT = (
    "Vertical: {vertical}\n\n"
    "You are Trades, the residential service contractor research agent. "
    "Apply the research mandate and the four required elements (named "
    "existing tool, specific complaint pattern, identifiable unhappy "
    "segment, math that clears the floor) from your system prompt. Use "
    "your web search tool to verify current pricing, ratings, and review "
    "content before citing anything — do not rely on training-data memory "
    "of what ServiceTitan, Jobber, Housecall Pro, FieldEdge, mHelpDesk, or "
    "Workiz currently do or charge. "
    "Return a JSON array of 2-3 opportunity cards using the exact schema "
    "defined in your system prompt, including every Trades-specific field. "
    "Every pain point must cite a real, dated source. Every MRR figure "
    "must show its math. "
    "Once your research is complete, end your response with the JSON "
    "array and nothing after it — no closing remarks, no markdown fence "
    "around it."
)


async def run(vertical: str) -> list[dict]:
    """Entry point called by agents/orchestrator/agent.py's _run_one_vertical
    via importlib.import_module("agents.trades_intel.agent")."""
    system_prompt = TRADES_SYSTEM_PROMPT
    try:
        from agents.aggregator import pipeline_health
        recalibration_block = pipeline_health.get_active_recalibration_text("dispatch")
        if recalibration_block:
            system_prompt = f"{recalibration_block}\n\n{TRADES_SYSTEM_PROMPT}"
    except Exception:
        pass

    user_prompt = DataSanitizationShield.clean(_USER_PROMPT.format(vertical=vertical))
    raw = analyze_with_web_search(system_prompt, user_prompt, max_tokens=10000, model=HAIKU)
    findings = extract_trailing_json_array(raw)

    # Observed live 2026-08-22: Haiku sometimes returns a formatted string
    # ("$47,500/month at mature scale...") instead of a bare number here
    # despite the schema -- see core.json_extract.coerce_mrr_number's own
    # docstring for why silently passing that through would cause a false
    # "below floor" kill downstream.
    for finding in findings:
        if "conservative_mrr_potential" in finding:
            finding["conservative_mrr_potential"] = coerce_mrr_number(finding["conservative_mrr_potential"])

    return findings
