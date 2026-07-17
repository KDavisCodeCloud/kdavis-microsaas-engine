import json
from pathlib import Path

import pytest

from agents.factory.deploy import deploy_backend, deploy_frontend, deploy_product


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

    def __call__(self, cmd, capture_output=True, text=True, cwd=None, input=None):
        self.calls.append({"cmd": cmd, "cwd": cwd, "input": input})
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

    def recording_runner(cmd, capture_output=True, text=True, cwd=None, input=None):
        calls.append({"cmd": cmd, "cwd": cwd, "input": input})
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
