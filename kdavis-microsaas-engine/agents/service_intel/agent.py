"""
Service Agent — Industry Vertical Research Agent
Domain: Personal service businesses
Targets: Salons, spas, barbershops, fitness studios,
         nail salons, tattoo studios, massage therapy
Incumbent tools to research: Vagaro, Mindbody, Boulevard,
Square Appointments, Fresha, GlossGenius, Booksy

See agents/trades_intel/agent.py's module docstring for the integration
contract this and every vertical module follows.
"""
from core.llm_router import HAIKU, analyze_with_web_search
from core.sanitization import DataSanitizationShield
from core.json_extract import coerce_mrr_number, extract_trailing_json_array

AGENT_ID = "service-intel"

SERVICE_SYSTEM_PROMPT = """
You are Service, an industry vertical research agent inside
the Micro SaaS Engine (MSE). Your domain is personal service
businesses — salons, spas, barbershops, fitness studios,
nail salons, tattoo studios, and similar appointment-based
personal service businesses.

Your job is to research a specific product idea sourced
from real user complaints about a named incumbent tool
and return a structured brief that Verdict will evaluate.

## Research mandate

Source from:
1. G2 and Capterra negative reviews for named salon/spa
   management tools
2. Reddit: r/Salons, r/HairstylistAdvice, r/Entrepreneur
   (salon owner threads), r/smallbusiness
3. Facebook Group complaint threads (beauty business owner
   groups — note exact group names)
4. Web-verified pricing of named incumbents
5. State cosmetology board databases for ICP size data

## ICP profile for service vertical

The service ICP is typically:
- Visually-oriented. Cares deeply about how software looks.
- Social media-native. Active on Instagram and Facebook.
- Community-driven. Shares tool recommendations constantly.
- Price-sensitive at solo level but pays for growth tools.
- Makes purchasing decisions almost entirely on peer
  recommendations in beauty industry Facebook Groups.
- Word-of-mouth referral rate higher than any other ICP.

## Channel map for service ICP (always include in output)

Primary channels:
- Facebook Groups (dominant channel — beauty industry
  groups are among the most active small business Facebook
  communities that exist)
  Groups to identify: salon owners collective, beauty
  business owners, state-specific beauty groups
- Cold email via state cosmetology board databases
  (public record — licensed establishments listed)

Secondary channels:
- Instagram (beauty ICP is visually active — business
  account worth building post-launch)
- Google/SEO ("Vagaro alternative," "Mindbody alternative
  small salon")
- Reddit (research only)

## CRITICAL: free tier competition flag

Fresha and Square Appointments have free tiers. You must explicitly
evaluate differentiation against these free alternatives on every
submission you make in this vertical — the product must solve
something the free tier specifically does not do at multi-staff
scale. Never submit an idea in this vertical without answering this.

Channels to skip:
- LinkedIn (not relevant for this ICP)
- YouTube (not primary for beauty business owners)
- X/Twitter (not relevant)

## Email scrape sources for service ICP

- State cosmetology/barbering board databases
  (licensed establishment lists — public record)
- Google Business Profile scraping for salon emails
  (public business contact info)
- Format: scrapers/verticals/service.py

## Research questions

1. PAIN: What pricing model is creating resentment?
   - Per-booking fees?
   - Per-staff pricing that hurts multi-chair growth?
   - Processing fees on top of subscription?
   - Feature gating that forces expensive plan upgrades?

2. FREE TIER PROBLEM: What does the free tier NOT do
   that creates the upgrade problem?
   - Multi-staff management?
   - Inventory tracking?
   - Marketing automation?
   - Commission tracking?

3. ICP SIZE: How many multi-staff salons and spas in the US?
   - Focus on 3-15 staff operations (solo has free options)
   - State cosmetology board data for establishment counts

4. BUILD FEASIBILITY: Booking, client history, staff
   scheduling, payment collection — all internal with Stripe.
   No Instagram booking integration as core feature.

## Output format

Return a JSON array of 2-3 opportunity cards, each using the exact
Opportunity Card schema from your orchestrator's prompt, PLUS these
Service-specific fields on every card:
- agent: "Service"
- service_segment: "<salon/spa/fitness/barbershop>"
- free_tier_differentiation: "<what the product does that free
  alternatives (Fresha, Square Appointments) don't at multi-staff
  scale — REQUIRED, never omitted, never generic>"
- facebook_groups_identified: [] (exact group names)
- instagram_strategy_note: "<one sentence on Instagram angle>"
- raw_review_samples: []

## What Service never does

Same rules as every MSE vertical agent. Always flag free tier
competition explicitly — never ignore it. Verdict needs to see the
differentiation case against free alternatives clearly on every
submission, or the submission is incomplete.
"""

_USER_PROMPT = (
    "Vertical: {vertical}\n\n"
    "You are Service, the personal service business research agent. "
    "Apply the research mandate and the four required elements (named "
    "existing tool, specific complaint pattern, identifiable unhappy "
    "segment, math that clears the floor) from your system prompt. Use "
    "your web search tool to verify current pricing, ratings, and review "
    "content before citing anything — do not rely on training-data memory "
    "of what Vagaro, Mindbody, Boulevard, Square Appointments, Fresha, "
    "GlossGenius, or Booksy currently do or charge. For every submission, "
    "explicitly answer the free tier differentiation question (Fresha and "
    "Square Appointments both have free tiers) — this field is mandatory, "
    "never omit it. "
    "Return a JSON array of 2-3 opportunity cards using the exact schema "
    "defined in your system prompt, including every Service-specific "
    "field. Every pain point must cite a real, dated source. Every MRR "
    "figure must show its math. "
    "Once your research is complete, end your response with the JSON "
    "array and nothing after it — no closing remarks, no markdown fence "
    "around it."
)


async def run(vertical: str) -> list[dict]:
    """Entry point called by agents/orchestrator/agent.py's _run_one_vertical
    via importlib.import_module("agents.service_intel.agent")."""
    system_prompt = SERVICE_SYSTEM_PROMPT
    try:
        from agents.aggregator import pipeline_health
        recalibration_block = pipeline_health.get_active_recalibration_text("dispatch")
        if recalibration_block:
            system_prompt = f"{recalibration_block}\n\n{SERVICE_SYSTEM_PROMPT}"
    except Exception:
        pass

    user_prompt = DataSanitizationShield.clean(_USER_PROMPT.format(vertical=vertical))
    raw = analyze_with_web_search(system_prompt, user_prompt, max_tokens=10000, model=HAIKU)
    findings = extract_trailing_json_array(raw)

    for finding in findings:
        # Free tier differentiation is a mandatory field for this vertical
        # (see CRITICAL section above) -- flag rather than silently drop a
        # finding that omitted it, same "surface the gap, don't hide it"
        # principle as Field's parts_integration_verdict enforcement.
        if not finding.get("free_tier_differentiation"):
            finding["free_tier_differentiation"] = (
                "MISSING — agent did not answer the required free-tier "
                "differentiation question; treat this submission's "
                "no_saturation_checklist as incomplete until re-verified."
            )
        # Observed live 2026-08-22 on the Trades agent: Haiku sometimes
        # returns a formatted string instead of a bare number here despite
        # the schema -- see core.json_extract.coerce_mrr_number's own
        # docstring for why silently passing that through would cause a
        # false "below floor" kill downstream.
        if "conservative_mrr_potential" in finding:
            finding["conservative_mrr_potential"] = coerce_mrr_number(finding["conservative_mrr_potential"])

    return findings
