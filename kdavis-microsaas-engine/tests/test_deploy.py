import json
from pathlib import Path

import pytest

from agents.factory.deploy import DeployAuthError, deploy_backend, deploy_frontend, deploy_product, set_railway_env_vars


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Matches subprocess.run's call signature. Responses keyed by the
    command's first 2-3 tokens (enough to disambiguate railway/vercel
    subcommands without full argv matching)."""

    def __init__(self, responses: dict[tuple, FakeResult]):
        self._responses = responses
        self.calls = []

    def __call__(self, cmd, capture_output=True, text=True, cwd=None, input=None, env=None):
        self.calls.append({"cmd": cmd, "cwd": cwd, "input": input, "env": env})
        for key_prefix, resp in self._responses.items():
            if tuple(cmd[: len(key_prefix)]) == key_prefix:
                return resp
        raise AssertionError(f"No fake response configured for {cmd}")


def test_deploy_backend_full_flow(tmp_path):
    runner = FakeRunner({
        ("railway", "init"): FakeResult(0, stdout='{"id": "proj-1"}'),
        ("railway", "variable"): FakeResult(0, stdout='{"keys": ["FOO"]}'),
        ("railway", "up"): FakeResult(0, stdout='{"deploymentId": "d1"}'),
        ("railway", "service", "list"): FakeResult(0, stdout=json.dumps([
            {"latestDeployment": {"status": "SUCCESS"}}
        ])),
        ("railway", "domain"): FakeResult(0, stdout='{"domain": "freight-audit-copilot-api-production.up.railway.app"}'),
    })

    result = deploy_backend(tmp_path, "freight-audit-copilot", {"FOO": "bar"}, runner=runner, poll_interval=0)

    assert result["url"] == "freight-audit-copilot-api-production.up.railway.app"
    assert result["service"] == "freight-audit-copilot-api"

    init_call = next(c for c in runner.calls if c["cmd"][:2] == ["railway", "init"])
    assert "--name" in init_call["cmd"]
    assert "freight-audit-copilot-api" in init_call["cmd"]


def test_deploy_backend_raises_on_failed_deployment(tmp_path):
    runner = FakeRunner({
        ("railway", "init"): FakeResult(0, stdout='{}'),
        ("railway", "variable"): FakeResult(0, stdout='{}'),
        ("railway", "up"): FakeResult(0, stdout='{}'),
        ("railway", "service", "list"): FakeResult(0, stdout=json.dumps([
            {"latestDeployment": {"status": "FAILED"}}
        ])),
    })

    with pytest.raises(RuntimeError, match="ended in status=FAILED"):
        deploy_backend(tmp_path, "freight-audit-copilot", {}, runner=runner, poll_interval=0)


def test_deploy_backend_times_out_if_never_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.factory.deploy.time.sleep", lambda s: None)
    runner = FakeRunner({
        ("railway", "init"): FakeResult(0, stdout='{}'),
        ("railway", "variable"): FakeResult(0, stdout='{}'),
        ("railway", "up"): FakeResult(0, stdout='{}'),
        ("railway", "service", "list"): FakeResult(0, stdout=json.dumps([
            {"latestDeployment": {"status": "BUILDING"}}
        ])),
    })

    with pytest.raises(TimeoutError):
        deploy_backend(tmp_path, "freight-audit-copilot", {}, runner=runner, max_wait_seconds=5, poll_interval=5)


def test_deploy_backend_raises_on_command_failure(tmp_path):
    runner = FakeRunner({
        ("railway", "init"): FakeResult(1, stdout="", stderr="Free plan resource provision limit exceeded"),
    })

    with pytest.raises(RuntimeError, match="provision limit exceeded"):
        deploy_backend(tmp_path, "freight-audit-copilot", {}, runner=runner)


def test_deploy_frontend_full_flow(tmp_path):
    runner = FakeRunner({
        ("vercel", "project", "add"): FakeResult(0, stdout='{}'),
        ("vercel", "link"): FakeResult(0, stdout=""),
        ("vercel", "env", "add"): FakeResult(0, stdout=""),
        ("vercel", "--prod"): FakeResult(0, stdout=json.dumps({"deployment": {"url": "freight-audit-copilot.vercel.app"}})),
        ("vercel", "domains", "add"): FakeResult(0, stdout=""),
    })

    result = deploy_frontend(tmp_path, "freight-audit-copilot", {"NEXT_PUBLIC_API_URL": "https://api.example.com"}, runner=runner)

    assert result["url"] == "freight-audit-copilot.vercel.app"
    assert result["domain"] == "freight-audit-copilot.thdstack.com"

    env_call = next(c for c in runner.calls if c["cmd"][:2] == ["vercel", "env"])
    assert env_call["input"] == "https://api.example.com"

    domain_call = next(c for c in runner.calls if c["cmd"][:2] == ["vercel", "domains"])
    assert "freight-audit-copilot.thdstack.com" in domain_call["cmd"]


def test_deploy_frontend_raises_if_env_set_fails(tmp_path):
    runner = FakeRunner({
        ("vercel", "project", "add"): FakeResult(0, stdout='{}'),
        ("vercel", "link"): FakeResult(0, stdout=""),
        ("vercel", "env", "add"): FakeResult(1, stdout="", stderr="not linked"),
    })

    with pytest.raises(RuntimeError, match="Failed to set Vercel env var"):
        deploy_frontend(tmp_path, "freight-audit-copilot", {"FOO": "bar"}, runner=runner)


def test_deploy_product_wires_backend_url_into_frontend_env(tmp_path):
    calls = []

    def recording_runner(cmd, capture_output=True, text=True, cwd=None, input=None, env=None):
        calls.append({"cmd": cmd, "cwd": cwd, "input": input, "env": env})
        if cmd[:2] == ["railway", "domain"]:
            return FakeResult(0, stdout='{"domain": "freight-api.up.railway.app"}')
        if cmd[:3] == ["railway", "service", "list"]:
            return FakeResult(0, stdout=json.dumps([{"latestDeployment": {"status": "SUCCESS"}}]))
        if cmd[0] == "railway":
            return FakeResult(0, stdout='{}')
        if cmd[:2] == ["vercel", "--prod"]:
            return FakeResult(0, stdout=json.dumps({"deployment": {"url": "freight.vercel.app"}}))
        return FakeResult(0, stdout="")

    result = deploy_product(tmp_path, "freight", {"SUPABASE_URL": "x"}, {"OTHER": "y"}, runner=recording_runner)

    assert result["backend"]["url"] == "freight-api.up.railway.app"
    assert result["frontend"]["domain"] == "freight.thdstack.com"

    env_calls = [c for c in calls if c["cmd"][:2] == ["vercel", "env"]]
    api_url_call = next(c for c in env_calls if "NEXT_PUBLIC_API_URL" in c["cmd"])
    assert api_url_call["input"] == "https://freight-api.up.railway.app"


# ── Non-interactive auth (added 2026-07-20) ─────────────────────────
# Real gap found in a launch-readiness audit: deploy.py assumed railway/
# vercel were already logged in interactively -- true for the session
# that first verified these CLI workflows by hand, almost certainly not
# true for the actual mse-api service that executes a real build. These
# tests cover the fix: fail fast with a specific message before any CLI
# command runs, and confirm each token actually reaches its CLI calls.

def test_deploy_backend_fails_fast_with_no_cli_calls_if_railway_token_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("RAILWAY_TOKEN", raising=False)
    runner = FakeRunner({})  # no responses configured -- any CLI call would raise AssertionError

    with pytest.raises(DeployAuthError, match="RAILWAY_TOKEN"):
        deploy_backend(tmp_path, "freight", {}, runner=runner)

    assert runner.calls == []


def test_set_railway_env_vars_fails_fast_if_railway_token_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("RAILWAY_TOKEN", raising=False)
    runner = FakeRunner({})

    with pytest.raises(DeployAuthError, match="RAILWAY_TOKEN"):
        set_railway_env_vars(tmp_path, "freight-api", {"FOO": "bar"}, runner=runner)

    assert runner.calls == []


def test_deploy_frontend_fails_fast_with_no_cli_calls_if_vercel_token_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("VERCEL_TOKEN", raising=False)
    runner = FakeRunner({})

    with pytest.raises(DeployAuthError, match="VERCEL_TOKEN"):
        deploy_frontend(tmp_path, "freight", {}, runner=runner)

    assert runner.calls == []


def test_deploy_product_fails_fast_before_touching_railway_if_vercel_token_missing(tmp_path, monkeypatch):
    # The more dangerous ordering: if only the Vercel token were missing,
    # a naive implementation might still create and deploy the Railway
    # project before discovering the frontend can't be deployed.
    # deploy_product validates both up front, before either stage starts.
    monkeypatch.delenv("VERCEL_TOKEN", raising=False)
    runner = FakeRunner({})

    with pytest.raises(DeployAuthError, match="VERCEL_TOKEN"):
        deploy_product(tmp_path, "freight", {}, {}, runner=runner)

    assert runner.calls == []  # no railway project was created either


def test_railway_token_reaches_the_cli_subprocess_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("RAILWAY_TOKEN", "rw-secret-123")
    runner = FakeRunner({
        ("railway", "init"): FakeResult(0, stdout='{}'),
        ("railway", "variable"): FakeResult(0, stdout='{}'),
        ("railway", "up"): FakeResult(0, stdout='{}'),
        ("railway", "service", "list"): FakeResult(0, stdout=json.dumps([{"latestDeployment": {"status": "SUCCESS"}}])),
        ("railway", "domain"): FakeResult(0, stdout='{"domain": "x.up.railway.app"}'),
    })

    deploy_backend(tmp_path, "freight", {"FOO": "bar"}, runner=runner, poll_interval=0)

    init_call = next(c for c in runner.calls if c["cmd"][:2] == ["railway", "init"])
    assert init_call["env"]["RAILWAY_TOKEN"] == "rw-secret-123"


def test_vercel_token_reaches_the_cli_as_an_explicit_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("VERCEL_TOKEN", "vc-secret-456")
    runner = FakeRunner({
        ("vercel", "project", "add"): FakeResult(0, stdout='{}'),
        ("vercel", "link"): FakeResult(0, stdout=""),
        ("vercel", "env", "add"): FakeResult(0, stdout=""),
        ("vercel", "--prod"): FakeResult(0, stdout=json.dumps({"deployment": {"url": "x.vercel.app"}})),
        ("vercel", "domains", "add"): FakeResult(0, stdout=""),
    })

    deploy_frontend(tmp_path, "freight", {"FOO": "bar"}, runner=runner)

    link_call = next(c for c in runner.calls if c["cmd"][:2] == ["vercel", "link"])
    assert "--token" in link_call["cmd"]
    assert "vc-secret-456" in link_call["cmd"]
