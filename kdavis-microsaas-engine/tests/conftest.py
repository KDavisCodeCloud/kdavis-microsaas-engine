import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SUPABASE_URL", "https://placeholder.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "placeholder-service-key")
os.environ.setdefault("SUPABASE_ANON_KEY", "placeholder-anon-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "placeholder-jwt-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "placeholder-anthropic-key")
os.environ.setdefault("RESEND_API_KEY", "placeholder-resend-key")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_placeholder")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test_placeholder")
os.environ.setdefault("MARKETING_API_KEY", "test-marketing-api-key")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:3000")
os.environ.setdefault("APP_ENV", "test")

import pytest


class FakeQuery:
    """Minimal stand-in for supabase-py's chainable postgrest query builder.

    Records every call made against it and returns a canned result set on
    .execute(). Good enough for asserting agents/routes call the DB with the
    right table/values without hitting a real Supabase project.
    """

    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self.calls = []
        self._payload = None
        self._filters = []
        self._single = False

    def insert(self, payload):
        self.calls.append(("insert", payload))
        self._payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.calls.append(("upsert", payload, on_conflict))
        self._payload = payload
        return self

    def update(self, payload):
        self.calls.append(("update", payload))
        self._payload = payload
        return self

    def select(self, *args, **kwargs):
        self.calls.append(("select", args, kwargs))
        return self

    def order(self, *args, **kwargs):
        self.calls.append(("order", args, kwargs))
        return self

    def eq(self, key, value):
        self._filters.append((key, value))
        return self

    def lte(self, key, value):
        self.calls.append(("lte", key, value))
        self._filters.append((key, value))
        return self

    def is_(self, key, value):
        self.calls.append(("is_", key, value))
        self._filters.append((key, value))
        return self

    def maybe_single(self):
        self.calls.append(("maybe_single",))
        self._single = True
        return self

    def execute(self):
        self.calls.append(("execute",))
        self.store.executed.append(self)
        result_data = self.store.responses.get(self.table_name, [])
        if getattr(self, "_single", False):
            # Real supabase-py's .maybe_single().execute() returns bare
            # None (not a Response object with .data=None) when zero rows
            # match — confirmed against a real live crash 2026-07-17
            # (AttributeError: 'NoneType' object has no attribute 'data').
            # Matching that exactly here so tests actually catch code that
            # forgets to guard against it, instead of masking the bug the
            # way the old always-return-an-object version did.
            if not result_data:
                return None
            return type("Result", (), {"data": result_data[0]})()
        return type("Result", (), {"data": result_data})()


class FakeSupabase:
    """Fake supabase Client — call .table(name) to get a FakeQuery, inspect
    .executed afterward for what actually ran."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.executed = []
        self.tables_touched = []

    def table(self, name):
        self.tables_touched.append(name)
        return FakeQuery(name, self)


@pytest.fixture
def fake_db():
    return FakeSupabase()
