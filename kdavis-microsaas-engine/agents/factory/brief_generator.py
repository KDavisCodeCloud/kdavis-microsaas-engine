"""
Brief generator — MSE Factory Expansion, RULE: POST-VERDICT BUILD BRIEF
GENERATION / RULE: BRIEF_GENERATOR AGENT (see CLAUDE.md, 2026-07-17
section).

Triggered when Verdict issues a PASS on an opportunity
(`opportunity_pipeline.status` -> `READY_TO_BUILD`), via
`POST /factory/generate-brief/{opportunity_id}` (see
api/routers/factory.py) — mirrors the existing `/factory/build/{id}`
HITL-gated trigger pattern. This step runs BEFORE a human decides to
spend money on `run_build_pipeline` (Phase 6e); it produces the two
briefs a human reviews before clicking "Build This Product".

Responsibilities:
1. Read the opportunity + research report from opportunity_pipeline
2. Look up the industry-specific palette from industry_color_map,
   matched on opportunity_pipeline.vertical (real vertical names — see
   migration 20260717000012; falls back to the 'open' palette from
   migration 20260717000011 if a vertical somehow isn't seeded, since
   mse_build_briefs.vertical has an FK to industry_color_map.vertical
   and an unmatched string would fail the insert)
3. Generate BUILD_BRIEF_CLAUDE_CODE.md and BUILD_BRIEF_CLAUDE_DESIGN.md
   (Sonnet — this is analysis/synthesis, not high-volume scraping, so
   core.llm_router.analyze is the correct routing per CLAUDE.md's model
   rule)
4. Push both files to a new branch `brief/{product-slug}` via GitHub's
   REST API (not a local `git` checkout — the deployed container has no
   .git directory at all, see _push_brief_branch's docstring)
5. Insert into mse_build_briefs (Realtime publication is already enabled
   on that table — the insert itself is what notifies subscribed
   dashboards, no separate broadcast step needed)

No infrastructure is provisioned here and no money is spent — this is
pure content generation plus a git branch push, so unlike
run_build_pipeline this does not require a Stripe key and does not flip
opportunity_pipeline.status. triggered_by is still required, matching
every other agent action in this repo, so brief generation always has a
named human in the audit trail even though it isn't itself a spend
decision.
"""
import json
import os
import re
from typing import Any, Callable, Optional

import httpx

from core.supabase_client import get_supabase
from core.llm_router import analyze
from core.naming import derive_product_name

AGENT_ID = "factory-brief-generator"
FALLBACK_VERTICAL = "open"

# Pushing branches via `git` subprocess calls was removed 2026-08-04: it
# only ever worked in an interactive session with a real local checkout.
# The deployed Railway container has no .git directory at all -- Railpack
# copies app source, not a git working tree -- confirmed live via the real
# failure: "fatal: not a git repository (or any of the parent
# directories): .git". Every dashboard-triggered brief generation failed
# this way. Pushing through GitHub's REST API instead needs no local git
# state at all, so it works identically in any environment.
_GITHUB_REPO = "KDavisCodeCloud/kdavis-microsaas-engine"
_GITHUB_API_BASE = "https://api.github.com"
_BASE_BRANCH = "main"


def _emit_event(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": event_type,
        "metadata": metadata,
    }).execute()


def _write_audit(db, outcome: str, product_id: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "brief_generation_run",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if len(slug) > 60:
        # Defense in depth even though the name normally comes from
        # derive_product_name — an LLM can still ignore instructions and
        # return something long (observed for real 2026-07-17: a response
        # with reasoning text left in it, ~80 chars once slugified).
        slug = slug[:60].rsplit("-", 1)[0]
    return slug or "product"


def _gh(client: httpx.Client, token: str, method: str, path: str, **kwargs) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = client.request(method, f"{_GITHUB_API_BASE}{path}", headers=headers, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"GitHub API call failed ({method} {path}): [{response.status_code}] {response.text}")
    return response.json()


def _push_brief_branch(client: httpx.Client, branch: str, files: dict[str, str], commit_message: str) -> None:
    """
    Creates `branch` off `_BASE_BRANCH` and commits `files` (path -> raw
    text content) to it in one commit, entirely through GitHub's Git Data
    API — no local git working tree required. Uses the low-level blob/
    tree/commit/ref endpoints (not the simpler Contents API) so both
    files land in a single commit rather than two, matching the previous
    `git add both files; git commit` behavior.
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set — cannot push a brief branch to GitHub without it")

    base_ref = _gh(client, token, "GET", f"/repos/{_GITHUB_REPO}/git/ref/heads/{_BASE_BRANCH}")
    base_sha = base_ref["object"]["sha"]
    base_commit = _gh(client, token, "GET", f"/repos/{_GITHUB_REPO}/git/commits/{base_sha}")
    base_tree_sha = base_commit["tree"]["sha"]

    tree_entries = []
    for path, content in files.items():
        blob = _gh(client, token, "POST", f"/repos/{_GITHUB_REPO}/git/blobs", json={"content": content, "encoding": "utf-8"})
        tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})

    new_tree = _gh(
        client, token, "POST", f"/repos/{_GITHUB_REPO}/git/trees",
        json={"base_tree": base_tree_sha, "tree": tree_entries},
    )
    new_commit = _gh(
        client, token, "POST", f"/repos/{_GITHUB_REPO}/git/commits",
        json={"message": commit_message, "tree": new_tree["sha"], "parents": [base_sha]},
    )
    _gh(
        client, token, "POST", f"/repos/{_GITHUB_REPO}/git/refs",
        json={"ref": f"refs/heads/{branch}", "sha": new_commit["sha"]},
    )


# opportunity_pipeline.vertical is free-text written by the research
# swarm (e.g. "Real Estate — Buyer's Agents at Independent Teams"), not
# constrained to industry_color_map's 6 seeded category names (e.g. "Real
# Estate / Property Management") -- an exact-match lookup against it was
# never going to hit. Confirmed live 2026-08-05: every one of the 6
# current opportunities missed the exact match and silently fell back to
# the generic 'open' palette, meaning the industry-specific design system
# (a real CLAUDE.md requirement) had never actually fired once. Keyword
# matching against each seeded category is a deterministic, no-cost
# improvement over always-open -- not perfect (free text can't be mapped
# to 6 buckets with full precision), but real estate/e-commerce/
# healthcare-shaped text now actually reaches its real palette instead of
# silently genericizing every brief.
_VERTICAL_KEYWORDS: dict[str, list[str]] = {
    "Healthcare / Medical Front Desk": ["healthcare", "medical", "therapist", "therapy", "clinic", "patient"],
    "Legal / Professional Services": ["legal", "law firm", "attorney", "paralegal"],
    "E-commerce / Retail Ops": ["e-commerce", "ecommerce", "shopify", " dtc ", "retail"],
    "Real Estate / Property Management": ["real estate", "property management", "landlord", "buyer's agent", "realtor"],
    "HR / Ops / People Management": ["human resources", "people management", "onboarding", "payroll", " hr "],
    "Finance / Accounting / Bookkeeping": ["accounting", "bookkeeping", "invoice", "finance", "financial"],
}


def _classify_vertical(vertical: str) -> Optional[str]:
    lowered = f" {vertical.lower()} "
    for seeded_vertical, keywords in _VERTICAL_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return seeded_vertical
    return None


# Design tone per vertical agent (2026-08-22) -- keyed on the "agent" field
# the vertical-specific research agents stamp onto every finding
# (agents/{trades,care,service,field}_intel/agent.py's output format
# sections). This is separate from _get_industry_palette's 6-vertical
# color lookup above: that one drives literal accent colors from
# industry_color_map (a DB table), this one is a short qualitative
# direction string with no color values of its own -- the design brief
# LLM call combines both (see _build_llm_input) rather than one
# replacing the other. Falls back to no tone override (None) for any
# opportunity that didn't come from one of these four vertical agents,
# which is the large majority today -- the six original verticals still
# fall through to Dispatch's generic prompt (see agents/orchestrator/
# agent.py's VERTICAL_MODULE_MAP comment) and have no per-vertical
# design agent of their own yet.
_DESIGN_TONE_BY_VERTICAL_AGENT: dict[str, str] = {
    "Trades": (
        "Clean, utilitarian, no-nonsense. Builder aesthetic. Dark mode "
        "option. Large tap targets. Job-site readable."
    ),
    "Care": (
        "Warm, friendly, approachable. Soft colors. Parents and educators "
        "are the secondary audience so trust signals matter. Never clinical."
    ),
    "Service": (
        "Visually polished. Beauty industry has high aesthetic standards. "
        "Clean, modern, aspirational. The product should look as good as "
        "the salon it serves."
    ),
    "Field": (
        "Practical, fast, readable in a garage. High contrast. Large "
        "text. No decoration — function first."
    ),
    "Ledger": (
        "Professional but not corporate. Clean financial tool aesthetic. "
        "Trust signals for money handling."
    ),
}


def _get_design_tone(vertical_agent_extras: dict) -> Optional[str]:
    return _DESIGN_TONE_BY_VERTICAL_AGENT.get(vertical_agent_extras.get("agent", ""))


def _get_industry_palette(db, vertical: Optional[str]) -> dict:
    def _lookup(v: str) -> Optional[dict]:
        result = db.table("industry_color_map").select(
            "vertical,primary_accent,secondary_accent,mood,benchmark_brands"
        ).eq("vertical", v).maybe_single().execute()
        return result.data if result is not None else None

    palette = _lookup(vertical) if vertical else None
    if not palette and vertical:
        classified = _classify_vertical(vertical)
        if classified:
            palette = _lookup(classified)
    if not palette:
        palette = _lookup(FALLBACK_VERTICAL)
    if not palette:
        raise RuntimeError(
            f"No industry_color_map row for vertical '{vertical}' and no '{FALLBACK_VERTICAL}' "
            "fallback row exists — check migrations 20260717000011/000012 have been applied"
        )
    return palette


CODE_BRIEF_SYSTEM_PROMPT = """You write BUILD_BRIEF_CLAUDE_CODE.md documents for THD Agentic Systems' \
Micro SaaS Engine. Input is a validated opportunity's research report. Output a complete build brief \
a fresh Claude Code session can execute against with no other context: product name, one-paragraph \
pitch, target customer, core feature list (in build order), the 6 required retention loops for this \
vertical, data model sketch, and the CLAUDE.md non-negotiables that apply (tenant_id + RLS on every \
table, POST /events on every agent action, $4K MRR floor). Write in markdown. Do not invent metrics \
that are not in the research report — if a number isn't given, say so instead of guessing.

If the input's `vertical_agent_context` field is non-empty, add a "## Vertical agent context" section \
covering: which vertical agent sourced this (its `agent` field), the specific segment field it \
reported (e.g. `trade_segment`/`care_segment`/`service_segment`/`field_segment`), the named incumbent \
tools from `competitor_examples`, and the core differentiator (flat fee vs. the incumbent's pricing \
model, from `pain_point`/`mrr_calculation`). If `parts_integration_verdict` or \
`free_tier_differentiation` are present, state them explicitly — these directly affect MVP scope \
(a `hard_requirement` parts verdict means the MVP must either include that integration or the brief \
must flag it as an open risk, never silently omitted).

Also add a "## ICP design constraints for build" section derived from the `icp` field: technical \
level (non-technical/semi-technical/technical, inferred from the ICP description — trades/care/field \
ICPs described as non-technical or time-constrained should specify a 16px minimum font size and a \
fast onboarding target), primary device (mobile-first if the ICP is described as checking a phone \
between jobs/on site, desktop/both otherwise), and any language rules implied by the vertical (e.g. \
"no jargon" for non-technical ICPs). If `vertical_agent_context` is empty, omit both new sections \
rather than writing them with invented content."""

DESIGN_BRIEF_SYSTEM_PROMPT = """You write BUILD_BRIEF_CLAUDE_DESIGN.md documents for THD Agentic \
Systems' Micro SaaS Engine. Input is a validated opportunity's research report plus an industry color \
palette (primary/secondary accent, mood, benchmark brands). Output a complete design brief: visual \
personality statement, how the given palette should be applied (never invent new brand colors), \
typography (Space Grotesk/IBM Plex Sans/JetBrains Mono per the base design system — never Inter/ \
Roboto/Arial), landing page structure, and the SXO requirements from CLAUDE.md's SEARCH VISIBILITY \
LAYER rule (above-fold CTA, no dead ends, mobile-first). Write in markdown.

If the input's `design_tone_override` field is non-null, treat it as the primary source of truth for \
this product's visual personality — apply it on top of (not instead of) the given `industry_palette`'s \
actual accent colors. State it in the brief as its own short "## Design tone" subsection under visual \
personality, quoted close to verbatim, before elaborating.

If `vertical_agent_context` is non-empty, also add an "## ICP design profile" section: who the ICP is \
(from `icp.business_type`/`icp.decision_maker`), technical comfort level and primary device (same \
inference rule as the code brief — non-technical/time-constrained ICPs get mobile-first, 16px minimum \
body text, 48px minimum button height, 375px primary design viewport), and a "## Key screens to \
design first" list (landing page with trust signals, first onboarding step, core daily dashboard, \
mobile home screen, and a pricing page with no asterisks). If `free_tier_differentiation` is present, \
the pricing page section must explicitly address how the design communicates that differentiation \
against the named free competitor. Every MSE product's trust-signal footer is standard regardless of \
vertical: SSL visible, "THD Agentic Systems LLC" + real mailing address, 14-day money-back guarantee, \
"your data is encrypted and secure", "cancel anytime — we send you all your data" — always include \
this list once, near the end. If `vertical_agent_context` is empty, omit the ICP design profile \
section rather than inventing one."""


def _build_llm_input(opp: dict, palette: dict, product_name: str) -> str:
    vertical_agent_extras = opp.get("vertical_agent_extras") or {}
    return json.dumps({
        "product_name": product_name,
        "solution_concept": opp.get("solution_concept"),
        "vertical": opp.get("vertical"),
        "pain_point": opp.get("pain_point"),
        "mrr_calculation": opp.get("mrr_calculation"),
        "conservative_mrr_potential": opp.get("conservative_mrr_potential"),
        "retention_hooks": opp.get("retention_hooks"),
        "tier_structure": opp.get("tier_structure"),
        "source_urls": opp.get("source_urls"),
        "industry_palette": palette,
        "icp": opp.get("icp"),
        "competitor_examples": opp.get("competitor_examples"),
        # Populated only for opportunities sourced from one of the four
        # vertical research agents (agents/{trades,care,service,field}_
        # intel/agent.py) -- see opportunity_pipeline.vertical_agent_extras'
        # own migration comment for why this exists as freeform JSONB
        # instead of fixed columns. Empty {} for every other opportunity
        # (the six original verticals, still on Dispatch's generic
        # fallback prompt) -- the two system prompts below only ask for a
        # "Vertical agent context" / "ICP design constraints" section when
        # this is non-empty, so a brief for those six doesn't grow a
        # section with nothing real to say.
        "vertical_agent_context": vertical_agent_extras,
        "design_tone_override": _get_design_tone(vertical_agent_extras),
    }, default=str)


def generate_build_brief(
    opportunity_id: str,
    triggered_by: str,
    supabase_client: Optional[Any] = None,
    llm_analyze: Callable[..., str] = analyze,
    http_client: Optional[httpx.Client] = None,
) -> dict:
    """
    Generates both briefs for one opportunity, pushes them to
    `brief/{product-slug}` via the GitHub API, and inserts the
    mse_build_briefs row. Raises on any failure — never fails silently.
    Returns the inserted row.
    """
    if not triggered_by:
        raise ValueError("triggered_by is required — brief generation must never fire without a named human trigger")

    db = supabase_client if supabase_client is not None else get_supabase()
    owns_client = http_client is None
    client = http_client if http_client is not None else httpx.Client(timeout=30)

    _emit_event(db, "brief_generation_started", {"opportunity_id": opportunity_id, "triggered_by": triggered_by})

    try:
        opp_result = db.table("opportunity_pipeline").select(
            "id,vertical,pain_point,solution_concept,mrr_calculation,conservative_mrr_potential,"
            "build_confidence_score,verdict_v2_output,retention_hooks,source_urls,tier_structure,status,"
            "icp,competitor_examples,vertical_agent_extras"
        ).eq("id", opportunity_id).maybe_single().execute()
        opp = opp_result.data if opp_result is not None else None
        if not opp:
            raise RuntimeError(f"opportunity_pipeline row for {opportunity_id} not found")

        palette = _get_industry_palette(db, opp.get("vertical"))
        # solution_concept is a full descriptive sentence, not a short name
        # (no dedicated name field exists in the schema) — slugifying it
        # directly crashed a real 2026-07-17 run with "File name too long"
        # on the git branch ref. Derive a short name first.
        product_name = derive_product_name(opp["solution_concept"], llm_analyze=llm_analyze)
        product_slug = _slugify(product_name)
        llm_input = _build_llm_input(opp, palette, product_name)

        code_brief_md = llm_analyze(CODE_BRIEF_SYSTEM_PROMPT, llm_input)
        design_brief_md = llm_analyze(DESIGN_BRIEF_SYSTEM_PROMPT, llm_input)

        branch = f"brief/{product_slug}"
        _push_brief_branch(
            client, branch,
            files={
                "BUILD_BRIEF_CLAUDE_CODE.md": code_brief_md,
                "BUILD_BRIEF_CLAUDE_DESIGN.md": design_brief_md,
            },
            commit_message=f"Build brief: {product_name}",
        )

        # verdict_v2_output.confidence_score is Verdict v5.0's real,
        # independently-derived score (CLAUDE.md's RULE: VERDICT AGENT
        # v5.0). build_confidence_score is Dispatch's own older
        # self-reported column and reads 0 for every v5.0-era row --
        # using it here is why every recent brief showed "0/100" on the
        # dashboard regardless of the opportunity's real, approved score.
        verdict_output = opp.get("verdict_v2_output") or {}
        confidence_score = verdict_output.get("confidence_score")
        if confidence_score is None:
            confidence_score = opp.get("build_confidence_score")

        insert_result = db.table("mse_build_briefs").insert({
            "opportunity_id": opportunity_id,
            "product_name": product_name,
            "product_slug": product_slug,
            "verdict_score": confidence_score,
            "vertical": palette["vertical"],
            "claude_code_brief": {"markdown": code_brief_md},
            "claude_design_brief": {"markdown": design_brief_md},
            "repo_branch": branch,
            "status": "pending_review",
        }).execute()

    except Exception as exc:
        _write_audit(db, "lose", opportunity_id, {"triggered_by": triggered_by, "error": str(exc)})
        raise RuntimeError(f"Brief generation failed for {opportunity_id}: {exc}") from exc
    finally:
        if owns_client:
            client.close()

    brief_row = insert_result.data[0] if insert_result.data else None
    _write_audit(db, "win", opportunity_id, {"triggered_by": triggered_by, "product_slug": product_slug, "branch": branch})
    _emit_event(db, "brief_generation_completed", {"opportunity_id": opportunity_id, "product_slug": product_slug})

    return brief_row


# ── Real marketing pipeline wiring (2026-08-22) ─────────────────────────────
#
# The original task spec for this asked for a brand-new parallel pipeline
# (mse_marketing_briefs / mse_group_registry tables, a marketing_brief_
# generator.py calling mkt_v1(context)/draft_brevo_sequence(context) with an
# invented context shape). None of that exists, and a real, working pipeline
# already does this job: agents/marketing/mkt_r1_research_core.py produces
# mse_research_reports.report_json -- the schema every downstream marketing
# agent reads from (see that module's own docstring: "No downstream agent
# does its own research") -- and agents/marketing/
# mkt_orch_campaign_orchestrator.py's run_campaign_orchestrator() already
# fans out correctly to MKT-O1 (leads), MKT-O2 (DM/cold email), MKT-O3
# (email sequences), MKT-S1 (SEO), MKT-V1 (social content) from that report.
# This function populates the real report instead of building a second,
# disconnected one.

from datetime import date  # noqa: E402 -- grouped here, not at top-of-file,
# since this whole section is a distinct later addition to an existing
# module rather than part of its original set of imports.

# Per-vertical-agent primary outreach channels, translated into the exact
# vocabulary agents/marketing/mkt_orch_campaign_orchestrator.py's
# select_channels() checks membership against ("linkedin", "reddit",
# "facebook_groups" -- "seo" and "email" are always included by
# select_channels itself regardless of this list). Deliberately excludes
# any channel a vertical agent's own system prompt marks "secondary/
# research only, not primary outreach" (e.g. Reddit for all four of these
# vertical agents) -- including it here would make MKT-V1 start posting
# outreach content to a channel its own research agent said not to use
# for that purpose. None of the four currently map to "linkedin" or
# "reddit" -- a real, correct finding (see each agent's own channel map),
# not an oversight.
_ICP_CHANNELS_BY_VERTICAL_AGENT: dict[str, list[str]] = {
    "Trades": ["facebook_groups"],
    "Care": ["facebook_groups"],
    "Service": ["facebook_groups"],
    "Field": ["facebook_groups"],
}


def _derive_icp_channels(vertical_agent_extras: dict) -> list[str]:
    return _ICP_CHANNELS_BY_VERTICAL_AGENT.get(vertical_agent_extras.get("agent", ""), [])


# scrapers/verticals/__init__.py's VERTICAL_SCRAPERS registry is keyed on
# short slugs ("real_estate"), and agents/marketing/mkt_lead_finder.py looks
# up get_vertical_scraper(icp_config["vertical"]) directly against
# mse_icp_configs.vertical -- an exact-match lookup, same brittleness
# _classify_vertical/_get_industry_palette above already had to work around
# for opportunity_pipeline.vertical's own free-text values. Writing this
# opportunity's long descriptive vertical string (e.g. "Residential Trades
# / Service Contractors") into mse_icp_configs.vertical directly would
# never match "trades" and silently skip Source 2 (the public license-
# database scraper) for every one of these four verticals. Mapped from the
# same "agent" field _derive_icp_channels/_get_design_tone key on.
_SCRAPER_SLUG_BY_VERTICAL_AGENT: dict[str, str] = {
    "Trades": "trades",
    "Care": "care",
    "Service": "service",
    "Field": "field",
}


def _derive_scraper_vertical(vertical_agent_extras: dict, fallback_vertical: str) -> str:
    return _SCRAPER_SLUG_BY_VERTICAL_AGENT.get(vertical_agent_extras.get("agent", ""), fallback_vertical)


def _derive_report_fields(opp: dict) -> dict:
    """Best-effort mapping from one Verdict-scored opportunity onto MKT-R1's
    real report_json schema. This is a single opportunity's worth of
    signal, not a full weekly MKT-R1 scrape cycle -- proof_signals is
    legitimately empty (no real customers exist pre-build to generate
    proof from), and pain_language/competitor_moves each get one
    synthesized entry from the opportunity's own research rather than N
    separate review citations, since opportunity_pipeline doesn't store
    individual review-level detail. Once the product is live, MKT-R1's
    own weekly cycle takes over and produces a fuller report the normal
    way -- this only seeds the very first cycle so campaign orchestration
    has something real to fire against immediately on BUILD."""
    vertical_agent_extras = opp.get("vertical_agent_extras") or {}
    competitor_examples = opp.get("competitor_examples") or []
    tier_structure = opp.get("tier_structure") or {}
    verdict_output = opp.get("verdict_v2_output") or {}

    competitor_moves = []
    for c in competitor_examples:
        if isinstance(c, dict):
            competitor_moves.append({
                "competitor": c.get("name", "unknown"),
                "action": c.get("notable_weakness", "named incumbent in this vertical"),
                "source_url": c.get("url", ""),
            })
        elif c:
            competitor_moves.append({"competitor": c, "action": "named incumbent in this vertical", "source_url": ""})

    content_angles = [{
        "angle": opp.get("solution_concept", ""),
        "supporting_data": opp.get("pain_point", ""),
    }]
    youtube_angle = vertical_agent_extras.get("youtube_content_angle")
    if youtube_angle:
        content_angles.append({"angle": youtube_angle, "supporting_data": "vertical agent YouTube content suggestion"})
    instagram_note = vertical_agent_extras.get("instagram_strategy_note")
    if instagram_note:
        content_angles.append({"angle": instagram_note, "supporting_data": "vertical agent Instagram strategy note"})

    tier_1_price = (tier_structure.get("tier_1") or {}).get("price_monthly")
    suggested_price = verdict_output.get("proposed_price") or tier_1_price or 0

    return {
        "trending_topics": [{
            "topic": opp.get("vertical", ""),
            "why_it_matters": opp.get("pain_point", ""),
            "source_urls": opp.get("source_urls") or [],
        }],
        "pain_language": [{
            "phrase": opp.get("pain_point", ""),
            "context": opp.get("mrr_calculation", ""),
            "source": (competitor_examples[0].get("name") if competitor_examples and isinstance(competitor_examples[0], dict)
                       else (competitor_examples[0] if competitor_examples else "opportunity research")),
            "frequency": 1,
        }],
        "competitor_moves": competitor_moves,
        "content_angles": content_angles,
        "proof_signals": [],  # no real customers pre-build -- see docstring above
        "icp_channels": _derive_icp_channels(vertical_agent_extras) or ["seo", "email"],
        "willingness_to_pay_band": f"${tier_1_price}/mo" if tier_1_price else "unconfirmed",
        "wtp_evidence": [opp.get("mrr_calculation", "")] if opp.get("mrr_calculation") else [],
        "suggested_price": int(suggested_price) if suggested_price else 0,
    }


def _upsert_icp_config(db, product_id: str, opp: dict) -> None:
    icp = opp.get("icp") or {}
    vertical_agent_extras = opp.get("vertical_agent_extras") or {}

    # search_templates is generic JSONB (core/mse_icp_configs schema, see
    # migration 20260814000024_lead_finder.sql) -- exactly where the named
    # Facebook groups / state license DB sources / trade associations a
    # vertical agent found actually belong, since that's what
    # agents/marketing/mkt_lead_finder.py already reads for scraping
    # config. No separate "group registry" table needed for this.
    search_templates = {
        "facebook_groups": vertical_agent_extras.get("facebook_groups_identified", []),
        "state_license_db_sources": vertical_agent_extras.get("state_license_db_sources", []),
        "trade_association_channels": vertical_agent_extras.get("trade_association_channels", []),
        "state_association_channels": vertical_agent_extras.get("state_association_channels", []),
    }
    search_templates = {k: v for k, v in search_templates.items() if v}

    db.table("mse_icp_configs").upsert({
        "product_id": product_id,
        "job_titles": [icp["decision_maker"]] if icp.get("decision_maker") else [],
        "locations": [],
        "industries": [icp["business_type"]] if icp.get("business_type") else [],
        "vertical": _derive_scraper_vertical(vertical_agent_extras, opp.get("vertical", "")),
        "search_templates": search_templates,
        "exclude_domains": [],
    }, on_conflict="product_id").execute()


def generate_research_report_from_verdict(
    opportunity_id: str,
    product_id: str,
    triggered_by: str,
    supabase_client: Optional[Any] = None,
) -> dict:
    """
    Populates the real marketing pipeline from a BUILD-verdict opportunity
    so the existing MKT-R1/MKT-ORCH/MKT-O1..O3/S1/V1 machinery can fire
    against it -- see this section's module-level comment for why this
    reuses that pipeline instead of a new parallel one.

    Call this AFTER generate_build_brief() has inserted its mse_build_briefs
    row -- product_id should be that row's own `id`. There is no
    mse_products table anywhere in this schema (confirmed against every
    migration in this repo), so mse_build_briefs.id is the closest thing
    this factory has to a stable product identity at this stage, created
    at the same trigger point (a BUILD verdict) this function fires from.

    Writes mse_research_reports (real MKT-R1 schema, seeded from this one
    opportunity), upserts mse_icp_configs, then calls
    run_campaign_orchestrator() to fire the downstream agents -- which
    already includes drafting the 5-email trial-nurture sequence for HITL
    review (MKT-O3, agents/marketing/mkt_o3_email_sequence_loader.py's
    run_o3_email_sequence_loader(), into mse_email_sequences with a
    pending_hitl/hitl_approved_by/hitl_approved_at review workflow already
    built). That IS the real version of "draft Brevo nurture-email copy
    for review" -- this session's plan originally assumed that piece was
    missing, but it already exists under mse_email_sequences, not a new
    mse_brevo_sequence_drafts table; MKT-O3 fires automatically here
    because select_channels() always includes "email" in its base channel
    list. Raises on any failure -- never fails silently, matching every
    other agent action in this repo.
    """
    from agents.marketing.mkt_orch_campaign_orchestrator import run_campaign_orchestrator

    db = supabase_client if supabase_client is not None else get_supabase()
    cycle_date = date.today().isoformat()

    _emit_event(db, "research_report_seed_started", {"opportunity_id": opportunity_id, "product_id": product_id})

    try:
        opp_result = db.table("opportunity_pipeline").select(
            "id,vertical,pain_point,solution_concept,mrr_calculation,icp,competitor_examples,"
            "tier_structure,source_urls,verdict_v2_output,vertical_agent_extras"
        ).eq("id", opportunity_id).maybe_single().execute()
        opp = opp_result.data if opp_result is not None else None
        if not opp:
            raise RuntimeError(f"opportunity_pipeline row for {opportunity_id} not found")

        report = {
            "product_id": product_id,
            "cycle_date": cycle_date,
            **_derive_report_fields(opp),
        }
        # Additive, non-breaking key -- see opportunity_pipeline.
        # vertical_agent_extras' own migration comment. Existing readers
        # (select_channels, mkt_v1's _target_platforms, mkt_o1's
        # _derive_search_params) only read the specific keys they already
        # expect, so this doesn't break anything.
        report["vertical_agent_findings"] = opp.get("vertical_agent_extras") or {}

        db.table("mse_research_reports").upsert(
            {"product_id": product_id, "cycle_date": cycle_date, "report_json": report},
            on_conflict="product_id,cycle_date",
        ).execute()

        _upsert_icp_config(db, product_id, opp)

        campaign_result = run_campaign_orchestrator(
            product_id=product_id,
            research_opp_id=opportunity_id,
            vertical=opp.get("vertical", ""),
            supabase_client=db,
        )

    except Exception as exc:
        _write_audit(db, "lose", opportunity_id, {
            "triggered_by": triggered_by, "product_id": product_id, "error": str(exc),
        })
        raise RuntimeError(f"Research report seed failed for opportunity {opportunity_id}: {exc}") from exc

    _write_audit(db, "win", opportunity_id, {
        "triggered_by": triggered_by, "product_id": product_id, "campaign_build_id": campaign_result.get("campaign_build_id"),
    })
    _emit_event(db, "research_report_seed_completed", {"opportunity_id": opportunity_id, "product_id": product_id})

    return {"product_id": product_id, "report": report, "campaign_result": campaign_result}
