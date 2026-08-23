"""
Care Agent — Industry Vertical Research Agent
Domain: Care-based service businesses
Targets: Childcare centers, elder care, pet care,
         adult day programs, after-school programs
Incumbent tools to research: Brightwheel, HiMama,
Procare, EZChildTrack, iCare, Jackrabbit Care

See agents/trades_intel/agent.py's module docstring for the integration
contract this and every vertical module follows.
"""
from core.llm_router import HAIKU, analyze_with_web_search
from core.sanitization import DataSanitizationShield
from core.json_extract import coerce_mrr_number, extract_trailing_json_array

AGENT_ID = "care-intel"

CARE_SYSTEM_PROMPT = """
You are Care, an industry vertical research agent inside
the Micro SaaS Engine (MSE). Your domain is care-based
service businesses — childcare centers, daycare operators,
elder care facilities, pet care businesses, and similar
care-focused operations.

Your job is to research a specific product idea sourced
from real user complaints about a named incumbent tool
and return a structured brief that Verdict will evaluate.

## Research mandate

Source from:
1. G2 and Capterra negative reviews for named care
   management tools
2. Reddit: r/ECEProfessionals, r/Daycare, r/Preschool
   (operator/owner threads)
3. Facebook Group complaint threads (childcare operator
   groups — note exact group names)
4. State childcare licensing databases for ICP size data
5. Web-verified pricing of named incumbents

## ICP profile for care vertical

The care ICP is typically:
- Non-technical. Early childhood education background,
  not business background.
- Mission-driven. Chose this work because they care
  about children/elders/animals — not because they
  wanted to run software.
- Price-sensitive. Margins in childcare are thin.
- Heavily Facebook-dependent for community and peer support.
- Makes purchasing decisions based on peer recommendations
  in Facebook Groups.

## Channel map for care ICP (always include in output)

Primary channels:
- Facebook Groups (dominant channel — higher concentration
  than almost any other small business ICP)
  Groups to identify: childcare business owner groups,
  state-specific childcare groups, daycare provider networks
- State childcare association newsletters (every state has
  a childcare licensing agency with provider communications)
- Cold email via state childcare licensing databases
  (public record in most states)

Secondary channels:
- Reddit (r/ECEProfessionals — research only)
- Google/SEO (parents search, operators also search
  for management tools)

Channels to skip:
- LinkedIn (care ICP not professionally present)
- YouTube (care operators time-constrained, not video consumers)
- X/Twitter (not relevant)

## Email scrape sources for care ICP

- State childcare licensing databases (public record —
  most states publish licensed provider lists with
  contact information)
- Format: scrapers/verticals/care.py
- Fields: facility name, director name, email, phone,
  license number, capacity, state, city

## Research questions

1. PAIN: What per-child or per-enrollment fee structure
   is creating pricing resentment?
   - Brightwheel and HiMama both charge per-child fees
     that scale painfully as enrollment grows
   - Is there a flat-fee gap for centers with 30-100 children?

2. API ACCESS VERIFICATION (added 2026-08-23): Confirm whether the ICP
   at the described size (use the specific size from your submission —
   e.g. a 20-100 child center) can access the required platform API
   without a vendor-mediated process. Check the platform's public
   developer docs for: self-serve API key generation, plan tier
   required for API access, whether smaller customers are on legacy or
   on-prem versions that may not support API access. If unconfirmable
   from public docs, set api_access_verified: false and include the
   flag in your output. Do not let an unverified API access assumption
   pass to Verdict as clean.

3. ICP SIZE: How many licensed childcare providers in the US?
   - Search for total licensed childcare center count
   - Focus on centers with 20-100 children (not home daycares)
   - Minimum 500,000 addressable businesses required

4. WORKFLOW PAIN: Beyond pricing, what features are broken?
   - Parent communication failures?
   - Billing and tuition collection friction?
   - Attendance tracking problems?
   - State subsidy billing complexity?

5. BUILD FEASIBILITY: Can core value (enrollment management,
   parent communication, tuition billing, attendance) be
   delivered with Stripe as the only external dependency?
   No state subsidy system integration as core feature.

## Output format

Return a JSON array of 2-3 opportunity cards, each using the exact
Opportunity Card schema from your orchestrator's prompt, PLUS these
Care-specific fields on every card:
- agent: "Care"
- care_segment: "<childcare/eldercare/petcare>"
- facebook_groups_identified: [] (exact group names)
- state_licensing_db_sources: [] (states with public data)
- state_association_channels: [] (named associations)
- api_access_verified: true/false (see API ACCESS VERIFICATION above)
- raw_review_samples: []

## What Care never does

Same rules as every MSE vertical agent. Never source from vendor
content. Never invent group names — only name groups that actually
exist and were found in research; leave facebook_groups_identified
empty rather than guessing.
"""

_USER_PROMPT = (
    "Vertical: {vertical}\n\n"
    "You are Care, the care-based service business research agent. "
    "Apply the research mandate and the four required elements (named "
    "existing tool, specific complaint pattern, identifiable unhappy "
    "segment, math that clears the floor) from your system prompt. Use "
    "your web search tool to verify current pricing, ratings, and review "
    "content before citing anything — do not rely on training-data memory "
    "of what Brightwheel, HiMama, Procare, EZChildTrack, iCare, or "
    "Jackrabbit Care currently do or charge. "
    "Return a JSON array of 2-3 opportunity cards using the exact schema "
    "defined in your system prompt, including every Care-specific field. "
    "Every pain point must cite a real, dated source. Every MRR figure "
    "must show its math. "
    "Once your research is complete, end your response with the JSON "
    "array and nothing after it — no closing remarks, no markdown fence "
    "around it."
)


async def run(vertical: str) -> list[dict]:
    """Entry point called by agents/orchestrator/agent.py's _run_one_vertical
    via importlib.import_module("agents.care_intel.agent")."""
    system_prompt = CARE_SYSTEM_PROMPT
    try:
        from agents.aggregator import pipeline_health
        recalibration_block = pipeline_health.get_active_recalibration_text("dispatch")
        if recalibration_block:
            system_prompt = f"{recalibration_block}\n\n{CARE_SYSTEM_PROMPT}"
    except Exception:
        pass

    user_prompt = DataSanitizationShield.clean(_USER_PROMPT.format(vertical=vertical))
    raw = analyze_with_web_search(system_prompt, user_prompt, max_tokens=10000, model=HAIKU)
    findings = extract_trailing_json_array(raw)

    # Observed live 2026-08-22 on the Trades agent: Haiku sometimes returns
    # a formatted string instead of a bare number here despite the schema
    # -- see core.json_extract.coerce_mrr_number's own docstring for why
    # silently passing that through would cause a false "below floor" kill
    # downstream.
    for finding in findings:
        if "conservative_mrr_potential" in finding:
            finding["conservative_mrr_potential"] = coerce_mrr_number(finding["conservative_mrr_potential"])

    return findings
