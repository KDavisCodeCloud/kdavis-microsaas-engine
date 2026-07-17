"""
Factory build pipeline orchestration — Phase 6e of the MSE build/deploy
pipeline. Chains scaffold (6a) -> Supabase provisioning (6b) -> deploy
(6d) -> Stripe provisioning (6c, after deploy since the webhook needs a
real live URL to register against) for one READY_TO_BUILD opportunity.

HITL gate: this function must only ever be invoked by an explicit human
action (a dashboard button + admin-gated API endpoint — see
api/routers/factory.py), never automatically the moment an opportunity
reaches READY_TO_BUILD. Per CLAUDE.md's "No autonomous outbound —
approved status required in DB" non-negotiable, spending real money on
new Supabase/Stripe/Railway/Vercel infrastructure requires a human
decision, not just automated quality-gate passage. triggered_by is a
required parameter, not optional, specifically so a build can never run
without a named human behind it in the audit trail.
"""
import os
import secrets
import subprocess
from pathlib import Path
from typing import Any, Optional

from core.supabase_client import get_supabase
from agents.factory.scaffold_generator import generate_scaffold
from agents.factory.provision_supabase import provision_supabase_project
from agents.factory.provision_stripe import provision_stripe
from agents.factory.deploy import deploy_product, set_railway_env_vars

AGENT_ID = "factory-build-pipeline"


def _emit_event(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": event_type,
        "metadata": metadata,
    }).execute()


def _write_audit(db, outcome: str, product_id: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "build_pipeline_run",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def run_build_pipeline(
    product_id: str,
    scaffold_output_root: Path,
    stripe_api_key: str,
    triggered_by: str,
    supabase_client: Optional[Any] = None,
    org_id: Optional[str] = None,
    db_password: Optional[str] = None,
    subprocess_runner: Any = subprocess.run,
) -> dict:
    """
    Full 6a-6d flow for one opportunity. Raises on any failure at any
    stage — never fails silently. On failure, reverts
    opportunity_pipeline.status back to READY_TO_BUILD so it can be
    retried rather than getting stuck in "building" forever. Returns the
    full provisioning/deploy result on success.
    """
    if not triggered_by:
        raise ValueError("triggered_by is required — the build pipeline must never fire without a named human trigger")

    db = supabase_client if supabase_client is not None else get_supabase()
    org_id = org_id or os.environ["SUPABASE_ORG_ID"]
    db_password = db_password or secrets.token_urlsafe(24)

    _emit_event(db, "build_pipeline_started", {"product_id": product_id, "triggered_by": triggered_by})
    db.table("opportunity_pipeline").update({"status": "building"}).eq("id", product_id).execute()

    try:
        repo_path = generate_scaffold(product_id, scaffold_output_root, supabase_client=db)
        product_slug = repo_path.name

        migration_sql = (repo_path / "supabase" / "migrations" / "001_core_schema.sql").read_text()
        supabase_result = provision_supabase_project(product_slug, org_id, migration_sql, db_password)

        # maybe_single().execute() returns bare None (not a Response with
        # .data=None) when zero rows match.
        opp_result = db.table("opportunity_pipeline").select("solution_concept,tier_structure").eq("id", product_id).maybe_single().execute()
        opp = opp_result.data if opp_result is not None else None
        if not opp:
            raise RuntimeError(f"opportunity_pipeline row for {product_id} disappeared mid-build")

        backend_env = {
            "SUPABASE_URL": supabase_result["url"],
            "SUPABASE_SERVICE_KEY": supabase_result["service_role_key"],
            "SUPABASE_ANON_KEY": supabase_result["anon_key"],
            "STRIPE_SECRET_KEY": stripe_api_key,
            "APP_ENV": "production",
        }
        frontend_env = {
            "NEXT_PUBLIC_SUPABASE_URL": supabase_result["url"],
            "NEXT_PUBLIC_SUPABASE_ANON_KEY": supabase_result["anon_key"],
        }

        deploy_result = deploy_product(repo_path, product_slug, backend_env, frontend_env, runner=subprocess_runner)

        stripe_result = provision_stripe(
            opp["solution_concept"], opp.get("tier_structure") or {},
            f"https://{deploy_result['backend']['url']}", stripe_api_key,
        )

        # The webhook secret only exists after Stripe provisioning, which
        # only happens after the backend has a real live URL — so it's a
        # follow-up env var update, not part of the first deploy. Same
        # ordering constraint hit setting up kdavis-microsaas-engine's own
        # webhook 2026-07-16.
        set_railway_env_vars(
            repo_path, deploy_result["backend"]["service"],
            {"STRIPE_WEBHOOK_SECRET": stripe_result["webhook_secret"]},
            runner=subprocess_runner, skip_deploys=False,
        )

    except Exception as exc:
        db.table("opportunity_pipeline").update({"status": "READY_TO_BUILD"}).eq("id", product_id).execute()
        _write_audit(db, "lose", product_id, {"triggered_by": triggered_by, "error": str(exc)})
        raise RuntimeError(f"Build pipeline failed for {product_id}: {exc}") from exc

    db.table("opportunity_pipeline").update({"status": "launched"}).eq("id", product_id).execute()
    _write_audit(db, "win", product_id, {
        "triggered_by": triggered_by,
        "backend_url": deploy_result["backend"]["url"],
        "frontend_url": deploy_result["frontend"]["domain"],
    })
    _emit_event(db, "build_pipeline_completed", {"product_id": product_id, "product_slug": product_slug})

    return {
        "product_slug": product_slug,
        "supabase": supabase_result,
        "stripe": stripe_result,
        "deploy": deploy_result,
    }
