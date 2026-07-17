"""
Factory Supabase provisioning — Phase 6b of the MSE build/deploy pipeline.

Creates a genuinely dedicated Supabase project per product (ADR-001 — never
shared) via the Management API, waits for it to come up, runs the product's
retention-scaffold migration against it, and returns connection info.

Unlike Stripe (ADR-006), a new Supabase PROJECT — not account — really is
just an API call away within the existing org, so this is fully
automatable. See docs/architecture-decisions.md for why the two differ:
Stripe accounts are independent business entities, Supabase projects are
isolated resources under one umbrella org.

Requires SUPABASE_ACCESS_TOKEN — a Management API personal access token
(prefix sbp_...), NOT any project's anon/service key. Generate one at
supabase.com/dashboard/account/tokens.
"""
import os
import time
from typing import Any, Optional

import httpx

AGENT_ID = "factory-provision-supabase"
MANAGEMENT_API_BASE = "https://api.supabase.com/v1"


def _headers() -> dict:
    token = os.environ["SUPABASE_ACCESS_TOKEN"]
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def create_project(
    product_slug: str, org_id: str, db_password: str, region: str = "us-east-1",
    http_client: Optional[Any] = None,
) -> dict:
    """Creates a new Supabase project. Returns the raw creation response —
    status will be COMING_UP, not yet usable. Caller must wait_for_ready()."""
    client = http_client or httpx
    resp = client.post(
        f"{MANAGEMENT_API_BASE}/projects",
        headers=_headers(),
        json={
            "name": product_slug,
            "organization_id": org_id,
            "db_pass": db_password,
            "region": region,
            "plan": "free",
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase project creation failed ({resp.status_code}): {resp.text}")
    return resp.json()


def wait_for_project_ready(
    project_ref: str, http_client: Optional[Any] = None,
    max_wait_seconds: int = 300, poll_interval: int = 10,
) -> dict:
    """Polls until the project's status is ACTIVE_HEALTHY. Real Supabase
    project provisioning takes a couple of minutes — this is not a bug in
    the caller, it's the actual infrastructure spin-up time."""
    client = http_client or httpx
    elapsed = 0
    while elapsed < max_wait_seconds:
        resp = client.get(f"{MANAGEMENT_API_BASE}/projects/{project_ref}", headers=_headers(), timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"Failed to check project status ({resp.status_code}): {resp.text}")
        data = resp.json()
        if data.get("status") == "ACTIVE_HEALTHY":
            return data
        time.sleep(poll_interval)
        elapsed += poll_interval
    raise TimeoutError(f"Supabase project {project_ref} did not become ACTIVE_HEALTHY within {max_wait_seconds}s")


def get_api_keys(project_ref: str, http_client: Optional[Any] = None) -> dict:
    """Returns {'anon': str, 'service_role': str} for the project."""
    client = http_client or httpx
    resp = client.get(f"{MANAGEMENT_API_BASE}/projects/{project_ref}/api-keys", headers=_headers(), timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to fetch API keys ({resp.status_code}): {resp.text}")
    keys = {k["name"]: k["api_key"] for k in resp.json()}
    if "anon" not in keys or "service_role" not in keys:
        raise RuntimeError(f"Project {project_ref} is missing expected API keys: got {list(keys.keys())}")
    return {"anon": keys["anon"], "service_role": keys["service_role"]}


def run_migration(project_ref: str, sql: str, http_client: Optional[Any] = None) -> None:
    """Executes SQL against the new project via the Management API's query
    endpoint — same underlying mechanism as `supabase db query --linked`."""
    client = http_client or httpx
    resp = client.post(
        f"{MANAGEMENT_API_BASE}/projects/{project_ref}/database/query",
        headers=_headers(),
        json={"query": sql},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Migration failed for project {project_ref} ({resp.status_code}): {resp.text}")


def provision_supabase_project(
    product_slug: str, org_id: str, migration_sql: str, db_password: str,
    region: str = "us-east-1", http_client: Optional[Any] = None,
) -> dict:
    """Full 6b flow: create project -> wait for ready -> run the retention
    migration -> return connection info. Raises on any failure at any
    stage — never fails silently. This is the only function the
    orchestrator should call; the others are building blocks kept
    separately testable."""
    project = create_project(product_slug, org_id, db_password, region=region, http_client=http_client)
    ref = project["id"]
    wait_for_project_ready(ref, http_client=http_client)
    run_migration(ref, migration_sql, http_client=http_client)
    keys = get_api_keys(ref, http_client=http_client)

    return {
        "project_ref": ref,
        "url": f"https://{ref}.supabase.co",
        "anon_key": keys["anon"],
        "service_role_key": keys["service_role"],
        "db_password": db_password,
    }
