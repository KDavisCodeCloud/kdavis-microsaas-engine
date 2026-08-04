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
            "build_confidence_score,verdict_v2_output,retention_hooks,source_urls,tier_structure,status"
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
