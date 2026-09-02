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
os.environ.setdefault("UNSUBSCRIBE_SECRET", "test-unsubscribe-secret")
os.environ.setdefault("MARKETING_API_BASE_URL", "https://mse-api-production-f8bd.up.railway.app")
os.environ.setdefault("COMPLIANCE_MAILING_ADDRESS", "THD Agentic Systems LLC, [test address]")
os.environ.setdefault("RAILWAY_TOKEN", "test-railway-token")
os.environ.setdefault("VERCEL_TOKEN", "test-vercel-token")
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
        self._count_requested = False

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

    def delete(self):
        self.calls.append(("delete",))
        return self

    def select(self, *args, **kwargs):
        self.calls.append(("select", args, kwargs))
        self._count_requested = kwargs.get("count") == "exact"
        return self

    def order(self, *args, **kwargs):
        self.calls.append(("order", args, kwargs))
        return self

    def limit(self, count):
        self.calls.append(("limit", count))
        return self

    def range(self, start, end):
        self.calls.append(("range", start, end))
        return self

    def eq(self, key, value):
        self._filters.append((key, value))
        return self

    def lte(self, key, value):
        self.calls.append(("lte", key, value))
        self._filters.append((key, value))
        return self

    def lt(self, key, value):
        self.calls.append(("lt", key, value))
        self._filters.append((key, value))
        return self

    def gte(self, key, value):
        self.calls.append(("gte", key, value))
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
        # Lets a test simulate one specific row failing a DB constraint
        # (e.g. mrr_floor_check) without a real Postgres — used to verify
        # node_write_pipeline inserts row-by-row instead of one all-or-
        # nothing batch insert.
        if self.table_name == "opportunity_pipeline" and isinstance(self._payload, list):
            for row in self._payload:
                if row.get("solution_concept") in self.store.fail_on_insert:
                    raise Exception(
                        f"simulated constraint violation for {row.get('solution_concept')!r}"
                    )
        self.store.executed.append(self)
        # Lets a test simulate a lost atomic-claim race (a concurrent run
        # already flipped this row's status, so the conditional UPDATE this
        # call represents would affect zero rows on real Postgres) without a
        # real DB to enforce .eq() filters against -- real Postgres does
        # honor them as a WHERE clause; this fake normally can't, since
        # every execute() on a table returns the same canned response
        # regardless of verb or filters. Keyed on (table, target status) so
        # a specific update() call can be made to look lost without also
        # catching an unrelated update on the same table that happens to
        # run first (e.g. a stale-approval void check ahead of the claim) --
        # one-shot, consumed on match.
        target_status = self._payload.get("status") if isinstance(self._payload, dict) else None
        key = (self.table_name, target_status)
        if self.calls[0][0] == "update" and key in self.store.next_update_returns_empty:
            self.store.next_update_returns_empty.remove(key)
            return type("Result", (), {"data": []})()
        result_data = self.store.responses.get(self.table_name, [])
        if self._count_requested:
            return type("Result", (), {"data": result_data, "count": len(result_data)})()
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

    def __init__(self, responses=None, fail_on_insert=None):
        self.responses = responses or {}
        self.executed = []
        self.tables_touched = []
        self.fail_on_insert = fail_on_insert or set()
        self.rpc_calls = []
        self.next_update_returns_empty = set()

    def table(self, name):
        self.tables_touched.append(name)
        return FakeQuery(name, self)

    def rpc(self, name, params=None):
        # Additive (DIST Phase 5, 2026-08-30) -- no prior test in this repo
        # called .rpc(), so this can't break an existing one. Records the
        # call for assertion (self.rpc_calls) and returns whatever the test
        # pre-seeded under responses[f"rpc:{name}"] (default []), matching
        # the same responses-dict convention .table()/.execute() already use.
        self.rpc_calls.append((name, params or {}))
        return FakeQuery(f"rpc:{name}", self)


@pytest.fixture
def fake_db():
    return FakeSupabase()
