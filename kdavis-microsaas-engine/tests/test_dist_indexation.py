from datetime import datetime, timedelta, timezone

import pytest

from agents.dist.indexation_monitor import (
    GSCAccessError,
    check_stale_and_alert,
    sync_indexation,
)

# Local fake, not tests/conftest.py's shared FakeSupabase -- that one has
# no .lt() support and multiple other DIST forks are touching conftest.py
# tonight; a self-contained fake here avoids any coordination risk.


class _FakeQuery:
    def __init__(self, store, table_name):
        self.store = store
        self.table_name = table_name
        self._filters = []
        self._payload = None
        self._is_upsert = False
        self._on_conflict = None

    def select(self, *args, **kwargs):
        return self

    def insert(self, payload):
        self._payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self._payload = payload
        self._is_upsert = True
        self._on_conflict = on_conflict
        return self

    def eq(self, key, value):
        self._filters.append(("eq", key, value))
        return self

    def lt(self, key, value):
        self._filters.append(("lt", key, value))
        return self

    def _matches(self, rows):
        result = rows
        for kind, key, value in self._filters:
            if kind == "eq":
                result = [r for r in result if r.get(key) == value]
            elif kind == "lt":
                result = [r for r in result if r.get(key) is not None and r[key] < value]
        return result

    def execute(self):
        rows = self.store.data.setdefault(self.table_name, [])
        if self._payload is not None:
            if self._is_upsert:
                keys = (self._on_conflict or "").split(",")
                match = next(
                    (r for r in rows if all(r.get(k) == self._payload.get(k) for k in keys)), None
                ) if keys and keys != [""] else None
                if match:
                    match.update(self._payload)
                    row = match
                else:
                    row = dict(self._payload)
                    rows.append(row)
            else:
                row = dict(self._payload)
                rows.append(row)
            self.store.writes.append({"table": self.table_name, "payload": dict(self._payload)})
            return type("Result", (), {"data": [row]})()
        matched = self._matches(rows)
        return type("Result", (), {"data": matched})()


class _FakeSupabase:
    def __init__(self, data=None):
        self.data = data or {}
        self.writes = []

    def table(self, name):
        return _FakeQuery(self, name)


def _iso(dt):
    return dt.isoformat()


# ---- sync_indexation -----------------------------------------------------


def test_sync_no_verified_property_skips_cleanly():
    db = _FakeSupabase()
    result = sync_indexation("prod-1", None, supabase_client=db)
    assert result == {"synced": 0, "skipped_reason": "no_verified_property"}
    assert db.writes == []


def test_sync_gsc_access_error_skips_cleanly_not_crash():
    db = _FakeSupabase()

    def failing_fetch(url):
        raise GSCAccessError("no verified access to this property")

    result = sync_indexation("prod-1", "https://example.com", supabase_client=db, fetch_gsc_data=failing_fetch)
    assert result["synced"] == 0
    assert "no verified access" in result["skipped_reason"]


def test_sync_upserts_real_rows():
    db = _FakeSupabase()

    def fake_fetch(url):
        return [
            {"url": "https://example.com/a", "indexed": True, "coverage_state": "Submitted and indexed"},
            {"url": "https://example.com/b", "indexed": False, "coverage_state": "Crawled - not indexed"},
        ]

    result = sync_indexation("prod-1", "https://example.com", supabase_client=db, fetch_gsc_data=fake_fetch)
    assert result["synced"] == 2
    assert len(db.data["mse_indexation"]) == 2


def test_sync_repeat_call_does_not_touch_first_seen_at():
    db = _FakeSupabase(
        data={
            "mse_indexation": [
                {
                    "product_id": "prod-1",
                    "url": "https://example.com/a",
                    "indexed": False,
                    "first_seen_at": "2026-01-01T00:00:00+00:00",
                }
            ]
        }
    )

    def fake_fetch(url):
        return [{"url": "https://example.com/a", "indexed": True}]

    sync_indexation("prod-1", "https://example.com", supabase_client=db, fetch_gsc_data=fake_fetch)
    row = db.data["mse_indexation"][0]
    assert row["first_seen_at"] == "2026-01-01T00:00:00+00:00"
    assert row["indexed"] is True


# ---- check_stale_and_alert ------------------------------------------------


def _stale_row(url, days_old=30, indexed=False):
    return {
        "product_id": "prod-1",
        "url": url,
        "indexed": indexed,
        "first_seen_at": _iso(datetime.now(timezone.utc) - timedelta(days=days_old)),
    }


def test_21_day_rule_fires_and_raises_event():
    db = _FakeSupabase(data={"mse_indexation": [_stale_row("https://example.com/a")]})
    result = check_stale_and_alert("prod-1", "example-product", "Example Product", supabase_client=db)

    assert result["stale_count"] == 1
    assert result["alerts_raised"] == 1
    assert len(db.data["mse_monitoring_events"]) == 1
    event = db.data["mse_monitoring_events"][0]
    assert event["product_slug"] == "example-product"
    assert event["status"] == "open"
    assert event["requires_human_decision"] is True


def test_not_yet_stale_page_does_not_alert():
    db = _FakeSupabase(data={"mse_indexation": [_stale_row("https://example.com/a", days_old=5)]})
    result = check_stale_and_alert("prod-1", "example-product", "Example Product", supabase_client=db)
    assert result["stale_count"] == 0
    assert result["alerts_raised"] == 0


def test_already_open_alert_for_same_url_is_not_duplicated():
    db = _FakeSupabase(
        data={
            "mse_indexation": [_stale_row("https://example.com/a")],
            "mse_monitoring_events": [
                {
                    "product_slug": "example-product",
                    "context": "https://example.com/a",
                    "status": "open",
                }
            ],
        }
    )
    result = check_stale_and_alert("prod-1", "example-product", "Example Product", supabase_client=db)
    assert result["alerts_raised"] == 0
    assert len(db.data["mse_monitoring_events"]) == 1


def test_circuit_breaker_pauses_generator_at_threshold():
    db = _FakeSupabase(
        data={
            "mse_indexation": [
                _stale_row("https://example.com/a"),
                _stale_row("https://example.com/b"),
                _stale_row("https://example.com/c"),
            ]
        }
    )
    result = check_stale_and_alert("prod-1", "example-product", "Example Product", supabase_client=db)
    assert result["generator_paused"] is True
    paused_row = db.data["mse_generator_state"][0]
    assert paused_row["product_id"] == "prod-1"
    assert paused_row["paused"] is True


def test_below_threshold_does_not_pause_generator():
    db = _FakeSupabase(
        data={
            "mse_indexation": [
                _stale_row("https://example.com/a"),
                _stale_row("https://example.com/b"),
            ]
        }
    )
    result = check_stale_and_alert("prod-1", "example-product", "Example Product", supabase_client=db)
    assert result["generator_paused"] is False
    assert "mse_generator_state" not in db.data or db.data["mse_generator_state"] == []
