"""
Factory deploy automation — Phase 6d of the MSE build/deploy pipeline.

Deploys a scaffolded product's backend (Railway) and frontend (Vercel),
matching the exact CLI workflows proven working by hand throughout the
2026-07-16 session (railpack.json builder, native-filesystem deploy
workaround, domain attachment). Shells out to the `railway` and `vercel`
CLIs rather than their REST APIs — both are already authenticated in this
environment and their CLI behavior is what was actually verified live.

IMPORTANT — WSL filesystem gotcha (see memory: reference_railway_wsl_upload_bug):
`railway up` silently corrupts small config files when run from a path
under /mnt/c/... on this machine. repo_path passed in here MUST be a
native Linux filesystem path (e.g. under ~/deploy-cache/), not /mnt/c/...
— this module does not copy/relocate the repo itself, that's the caller's
responsibility (the scaffold generator should write directly to a native
path when this pipeline is wired end-to-end).

Every function shells out via an injectable `runner` (defaults to
subprocess.run) so the exact commands can be asserted in tests without
touching real infrastructure.
"""
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

AGENT_ID = "factory-deploy"

Runner = Callable[..., Any]


def _run(runner: Runner, cmd: list[str], cwd: Optional[Path] = None) -> str:
    result = runner(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {result.stderr or result.stdout}")
    return result.stdout


def set_railway_env_vars(
    repo_path: Path, service_name: str, env_vars: dict[str, str],
    runner: Runner = subprocess.run, skip_deploys: bool = True,
) -> None:
    """Sets one or more env vars on an already-existing Railway service.
    Reusable both by deploy_backend's initial setup and by the build
    pipeline's post-Stripe-provisioning step (the webhook secret only
    exists after the backend already has a live URL to register against,
    so it's set as a follow-up update, not part of the first deploy)."""
    for key, value in env_vars.items():
        _run(runner, ["railway", "variable", "set", f"{key}={value}", "--service", service_name, "--skip-deploys", "--json"], cwd=repo_path)
    if not skip_deploys:
        _run(runner, ["railway", "up", "--service", service_name, "-y", "-d", "--json"], cwd=repo_path)


def deploy_backend(
    repo_path: Path, product_slug: str, env_vars: dict[str, str],
    runner: Runner = subprocess.run, max_wait_seconds: int = 300, poll_interval: int = 10,
) -> dict:
    """Creates a new Railway project + service, sets env vars, deploys,
    and generates a public domain. Returns {"url": str, "service": str}.
    Raises on any failure — never silent."""
    service_name = f"{product_slug}-api"

    _run(runner, ["railway", "init", "--name", service_name, "--json"], cwd=repo_path)

    set_railway_env_vars(repo_path, service_name, env_vars, runner=runner, skip_deploys=True)

    _run(runner, ["railway", "up", "--service", service_name, "-y", "-d", "--json"], cwd=repo_path)

    elapsed = 0
    while elapsed < max_wait_seconds:
        out = _run(runner, ["railway", "service", "list", "--json"], cwd=repo_path)
        services = json.loads(out)
        status = services[0]["latestDeployment"]["status"] if services else None
        if status == "SUCCESS":
            break
        if status in ("FAILED", "CRASHED"):
            raise RuntimeError(f"Railway deploy for {service_name} ended in status={status}")
        time.sleep(poll_interval)
        elapsed += poll_interval
    else:
        raise TimeoutError(f"Railway deploy for {service_name} did not reach SUCCESS within {max_wait_seconds}s")

    domain_out = _run(runner, ["railway", "domain", "--service", service_name, "--json"], cwd=repo_path)
    domain = json.loads(domain_out)

    return {"url": domain["domain"], "service": service_name}


def deploy_frontend(
    repo_path: Path, product_slug: str, env_vars: dict[str, str],
    runner: Runner = subprocess.run,
) -> dict:
    """Creates a new Vercel project, links it, sets env vars, deploys to
    production, and attaches {product_slug}.thdstack.com. Returns
    {"url": str, "domain": str}. Raises on any failure — never silent."""
    frontend_path = repo_path / "frontend"
    domain = f"{product_slug}.thdstack.com"

    _run(runner, ["vercel", "project", "add", product_slug, "--json"], cwd=frontend_path)
    _run(runner, ["vercel", "link", "--yes", "--project", product_slug], cwd=frontend_path)

    for key, value in env_vars.items():
        result = runner(
            ["vercel", "env", "add", key, "production"],
            capture_output=True, text=True, cwd=str(frontend_path), input=value,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to set Vercel env var {key}: {result.stderr or result.stdout}")

    deploy_out = _run(runner, ["vercel", "--prod", "--yes"], cwd=frontend_path)
    deploy_result = json.loads(deploy_out)

    _run(runner, ["vercel", "domains", "add", domain, product_slug], cwd=frontend_path)

    return {"url": deploy_result.get("deployment", {}).get("url", ""), "domain": domain}


def deploy_product(
    repo_path: Path, product_slug: str, backend_env: dict[str, str], frontend_env: dict[str, str],
    runner: Runner = subprocess.run,
) -> dict:
    """Full 6d flow: backend then frontend (frontend needs the backend's
    URL for NEXT_PUBLIC_API_URL, so order matters — caller is responsible
    for merging deploy_backend's url into frontend_env before calling
    deploy_frontend; this function does that wiring)."""
    backend = deploy_backend(repo_path, product_slug, backend_env, runner=runner)

    full_frontend_env = {**frontend_env, "NEXT_PUBLIC_API_URL": f"https://{backend['url']}"}
    frontend = deploy_frontend(repo_path, product_slug, full_frontend_env, runner=runner)

    return {"backend": backend, "frontend": frontend}
