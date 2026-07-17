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
4. Write both files to a new git branch `brief/{product-slug}` and push
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
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

from core.supabase_client import get_supabase
from core.llm_router import analyze
from core.naming import derive_product_name

AGENT_ID = "factory-brief-generator"
FALLBACK_VERTICAL = "open"

Runner = Callable[..., Any]


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
    return slug or "product"


def _run(runner: Runner, cmd: list[str], cwd: Path) -> str:
    result = runner(cmd, capture_output=True, text=True, cwd=str(cwd))
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {result.stderr or result.stdout}")
    return result.stdout


def _get_industry_palette(db, vertical: Optional[str]) -> dict:
    palette = None
    if vertical:
        result = db.table("industry_color_map").select(
            "vertical,primary_accent,secondary_accent,mood,benchmark_brands"
        ).eq("vertical", vertical).maybe_single().execute()
        palette = result.data if result is not None else None
    if not palette:
        result = db.table("industry_color_map").select(
            "vertical,primary_accent,secondary_accent,mood,benchmark_brands"
        ).eq("vertical", FALLBACK_VERTICAL).maybe_single().execute()
        palette = result.data if result is not None else None
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
that are not in the research report — if a number isn't given, say so instead of guessing."""

DESIGN_BRIEF_SYSTEM_PROMPT = """You write BUILD_BRIEF_CLAUDE_DESIGN.md documents for THD Agentic \
Systems' Micro SaaS Engine. Input is a validated opportunity's research report plus an industry color \
palette (primary/secondary accent, mood, benchmark brands). Output a complete design brief: visual \
personality statement, how the given palette should be applied (never invent new brand colors), \
typography (Space Grotesk/IBM Plex Sans/JetBrains Mono per the base design system — never Inter/ \
Roboto/Arial), landing page structure, and the SXO requirements from CLAUDE.md's SEARCH VISIBILITY \
LAYER rule (above-fold CTA, no dead ends, mobile-first). Write in markdown."""


def _build_llm_input(opp: dict, palette: dict, product_name: str) -> str:
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
    }, default=str)


def generate_build_brief(
    opportunity_id: str,
    triggered_by: str,
    repo_root: Path,
    supabase_client: Optional[Any] = None,
    llm_analyze: Callable[..., str] = analyze,
    runner: Runner = subprocess.run,
) -> dict:
    """
    Generates both briefs for one opportunity, pushes them to
    `brief/{product-slug}`, and inserts the mse_build_briefs row. Raises
    on any failure — never fails silently. Returns the inserted row.
    """
    if not triggered_by:
        raise ValueError("triggered_by is required — brief generation must never fire without a named human trigger")

    db = supabase_client if supabase_client is not None else get_supabase()

    _emit_event(db, "brief_generation_started", {"opportunity_id": opportunity_id, "triggered_by": triggered_by})

    try:
        opp_result = db.table("opportunity_pipeline").select(
            "id,vertical,pain_point,solution_concept,mrr_calculation,conservative_mrr_potential,"
            "build_confidence_score,retention_hooks,source_urls,tier_structure,status"
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
        _run(runner, ["git", "checkout", "-b", branch], repo_root)
        (repo_root / "BUILD_BRIEF_CLAUDE_CODE.md").write_text(code_brief_md)
        (repo_root / "BUILD_BRIEF_CLAUDE_DESIGN.md").write_text(design_brief_md)
        _run(runner, ["git", "add", "BUILD_BRIEF_CLAUDE_CODE.md", "BUILD_BRIEF_CLAUDE_DESIGN.md"], repo_root)
        _run(runner, ["git", "commit", "-m", f"Build brief: {product_name}"], repo_root)
        _run(runner, ["git", "push", "-u", "origin", branch], repo_root)
        _run(runner, ["git", "checkout", "main"], repo_root)
        # The two files are now safely committed on the brief branch. Left
        # in place, they'd sit as untracked cruft in main's working tree
        # after the checkout above (git doesn't remove untracked files that
        # don't conflict with the target branch) — clean them up here.
        (repo_root / "BUILD_BRIEF_CLAUDE_CODE.md").unlink(missing_ok=True)
        (repo_root / "BUILD_BRIEF_CLAUDE_DESIGN.md").unlink(missing_ok=True)

        insert_result = db.table("mse_build_briefs").insert({
            "opportunity_id": opportunity_id,
            "product_name": product_name,
            "product_slug": product_slug,
            "verdict_score": opp.get("build_confidence_score"),
            "vertical": palette["vertical"],
            "claude_code_brief": {"markdown": code_brief_md},
            "claude_design_brief": {"markdown": design_brief_md},
            "repo_branch": branch,
            "status": "pending_review",
        }).execute()

    except Exception as exc:
        _write_audit(db, "lose", opportunity_id, {"triggered_by": triggered_by, "error": str(exc)})
        raise RuntimeError(f"Brief generation failed for {opportunity_id}: {exc}") from exc

    brief_row = insert_result.data[0] if insert_result.data else None
    _write_audit(db, "win", opportunity_id, {"triggered_by": triggered_by, "product_slug": product_slug, "branch": branch})
    _emit_event(db, "brief_generation_completed", {"opportunity_id": opportunity_id, "product_slug": product_slug})

    return brief_row
