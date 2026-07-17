import pytest

from agents.factory.provision_supabase import (
    create_project,
    wait_for_project_ready,
    get_api_keys,
    run_migration,
    provision_supabase_project,
)


class FakeResponse:
    def __init__(self, status_code, json_data, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text or str(json_data)

    def json(self):
        return self._json


class FakeHttpClient:
    """Records every call, returns canned responses keyed by (method, path-prefix)."""

    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def _match(self, method, url):
        # Match on URL suffix, not "contains" — /projects/{ref}/api-keys
        # otherwise ambiguously also contains the shorter /projects/{ref}
        # entry as a substring (it's a literal prefix of it).
        for (m, suffix), resp in self._responses.items():
            if m == method and url.endswith(suffix):
                return resp
        raise AssertionError(f"No fake response configured for {method} {url}")

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        return self._match("POST", url)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, None))
        return self._match("GET", url)


def test_create_project_success(monkeypatch):
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "sbp_test")
    client = FakeHttpClient({
        ("POST", "/projects"): FakeResponse(201, {"id": "new-ref-123", "status": "COMING_UP"}),
    })

    result = create_project("freight-audit-copilot", "org-1", "db-pass-123", http_client=client)

    assert result["id"] == "new-ref-123"
    assert client.calls[0][2]["name"] == "freight-audit-copilot"
    assert client.calls[0][2]["organization_id"] == "org-1"


def test_create_project_raises_on_error(monkeypatch):
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "sbp_test")
    client = FakeHttpClient({
        ("POST", "/projects"): FakeResponse(402, {}, text="Payment required — project limit reached"),
    })

    with pytest.raises(RuntimeError, match="Payment required"):
        create_project("freight-audit-copilot", "org-1", "db-pass-123", http_client=client)


def test_wait_for_project_ready_polls_until_active(monkeypatch):
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "sbp_test")
    monkeypatch.setattr("agents.factory.provision_supabase.time.sleep", lambda s: None)

    call_count = {"n": 0}

    class SlowClient(FakeHttpClient):
        def get(self, url, headers=None, timeout=None):
            call_count["n"] += 1
            status = "COMING_UP" if call_count["n"] < 3 else "ACTIVE_HEALTHY"
            return FakeResponse(200, {"id": "ref-1", "status": status})

    result = wait_for_project_ready("ref-1", http_client=SlowClient({}))

    assert result["status"] == "ACTIVE_HEALTHY"
    assert call_count["n"] == 3


def test_wait_for_project_ready_times_out(monkeypatch):
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "sbp_test")
    monkeypatch.setattr("agents.factory.provision_supabase.time.sleep", lambda s: None)
    client = FakeHttpClient({
        ("GET", "/projects/ref-1"): FakeResponse(200, {"id": "ref-1", "status": "COMING_UP"}),
    })

    with pytest.raises(TimeoutError):
        wait_for_project_ready("ref-1", http_client=client, max_wait_seconds=20, poll_interval=10)


def test_get_api_keys_returns_anon_and_service_role(monkeypatch):
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "sbp_test")
    client = FakeHttpClient({
        ("GET", "/api-keys"): FakeResponse(200, [
            {"name": "anon", "api_key": "anon-key-value"},
            {"name": "service_role", "api_key": "service-key-value"},
        ]),
    })

    keys = get_api_keys("ref-1", http_client=client)

    assert keys == {"anon": "anon-key-value", "service_role": "service-key-value"}


def test_get_api_keys_raises_if_keys_missing(monkeypatch):
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "sbp_test")
    client = FakeHttpClient({
        ("GET", "/api-keys"): FakeResponse(200, [{"name": "anon", "api_key": "anon-key-value"}]),
    })

    with pytest.raises(RuntimeError, match="missing expected API keys"):
        get_api_keys("ref-1", http_client=client)


def test_run_migration_raises_on_failure(monkeypatch):
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "sbp_test")
    client = FakeHttpClient({
        ("POST", "/database/query"): FakeResponse(400, {}, text="syntax error"),
    })

    with pytest.raises(RuntimeError, match="Migration failed"):
        run_migration("ref-1", "CREATE TABLE x();", http_client=client)


def test_provision_supabase_project_full_flow(monkeypatch):
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "sbp_test")
    monkeypatch.setattr("agents.factory.provision_supabase.time.sleep", lambda s: None)

    client = FakeHttpClient({
        ("POST", "/projects"): FakeResponse(201, {"id": "new-ref", "status": "COMING_UP"}),
        ("GET", "/projects/new-ref"): FakeResponse(200, {"id": "new-ref", "status": "ACTIVE_HEALTHY"}),
        ("POST", "/database/query"): FakeResponse(200, {}),
        ("GET", "/api-keys"): FakeResponse(200, [
            {"name": "anon", "api_key": "anon-val"},
            {"name": "service_role", "api_key": "service-val"},
        ]),
    })

    result = provision_supabase_project(
        "freight-audit-copilot", "org-1", "CREATE TABLE tenants();", "db-pass", http_client=client,
    )

    assert result["project_ref"] == "new-ref"
    assert result["url"] == "https://new-ref.supabase.co"
    assert result["anon_key"] == "anon-val"
    assert result["service_role_key"] == "service-val"
    assert result["db_password"] == "db-pass"

    migration_calls = [c for c in client.calls if c[0] == "POST" and "/database/query" in c[1]]
    assert len(migration_calls) == 1
    assert migration_calls[0][2]["query"] == "CREATE TABLE tenants();"
