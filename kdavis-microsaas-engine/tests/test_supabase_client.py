"""Unit coverage for core/supabase_client.py's client-selection contract —
the actual RLS enforcement only happens inside live Postgres and can't be
verified without a real DB connection, but the code deciding whether a
request gets the service-role client (bypasses RLS) or the anon-key client
scoped to the caller's JWT (RLS enforced) is fully testable, and is exactly
what CLAUDE.md's "never service role in routes" non-negotiable depends on.
"""
import core.supabase_client as supabase_client_module


def test_admin_client_uses_service_role_key(monkeypatch):
    supabase_client_module._admin_client = None
    captured = {}

    def fake_create_client(url, key):
        captured["url"] = url
        captured["key"] = key
        return object()

    monkeypatch.setattr(supabase_client_module, "create_client", fake_create_client)

    supabase_client_module.get_supabase()

    assert captured["key"] == "placeholder-service-key"


def test_admin_client_is_memoized(monkeypatch):
    supabase_client_module._admin_client = None
    call_count = {"n": 0}

    def fake_create_client(url, key):
        call_count["n"] += 1
        return object()

    monkeypatch.setattr(supabase_client_module, "create_client", fake_create_client)

    first = supabase_client_module.get_supabase()
    second = supabase_client_module.get_supabase()

    assert first is second
    assert call_count["n"] == 1


def test_request_client_uses_anon_key_never_service_role(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self):
            self.postgrest = FakePostgrest()

    class FakePostgrest:
        def __init__(self):
            self.auth_called_with = None

        def auth(self, jwt):
            self.auth_called_with = jwt

    def fake_create_client(url, key):
        captured["key"] = key
        return FakeClient()

    monkeypatch.setattr(supabase_client_module, "create_client", fake_create_client)

    client = supabase_client_module.get_supabase_for_request("user-jwt-token")

    assert captured["key"] == "placeholder-anon-key"
    assert captured["key"] != "placeholder-service-key"
    assert client.postgrest.auth_called_with == "user-jwt-token"


def test_request_client_is_not_memoized_across_calls(monkeypatch):
    """Each request must get its own client scoped to that caller's JWT —
    memoizing this like the admin client would leak one user's auth into
    another user's request."""
    created = []

    class FakeClient:
        def __init__(self):
            self.postgrest = type("P", (), {"auth": lambda self, jwt: None})()

    def fake_create_client(url, key):
        c = FakeClient()
        created.append(c)
        return c

    monkeypatch.setattr(supabase_client_module, "create_client", fake_create_client)

    first = supabase_client_module.get_supabase_for_request("jwt-a")
    second = supabase_client_module.get_supabase_for_request("jwt-b")

    assert first is not second
    assert len(created) == 2
