from datetime import datetime, timedelta, timezone

import pytest

from agents.dist.deploy_freshness_monitor import (
    STALE_HOURS,
    ServiceTarget,
    check_service_freshness,
    raise_stale_alert,
    run_freshness_check,
)

# Self-contained fake, matching test_dist_indexation.py's own precedent --
# avoids coordinating on tests/conftest.py while other DIST tasks are
# touching this repo concurrently.


class _FakeQuery:
    def __init__(self, store, table_name):
        self.store = store
        self.table_name = table_name
        self._filters = []
        self._payload = None

    def select(self, *args, **kwargs):
        return self

    def insert(self, payload):
        self._payload = payload
        return self

    def eq(self, key, value):
        self._filters.append((key, value))
        return self

    def _matching(self, rows):
        result = rows
        for key, value in self._filters:
            result = [r for r in result if r.get(key) == value]
        return result

    def execute(self):
        if self._payload is not None:
            self.store.inserted.setdefault(self.table_name, []).append(self._payload)
            return type("Result", (), {"data": [self._payload]})()
        rows = self._matching(self.store.data.get(self.table_name, []))
        return type("Result", (), {"data": rows})()


class _FakeSupabase:
    def __init__(self, data=None):
        self.data = data or {}
        self.inserted = {}

    def table(self, name):
        return _FakeQuery(self, name)


def _svc(name="test-service"):
    return ServiceTarget(name, "railway", "KDavisCodeCloud/test-repo", "main")


NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)


# ---- check_service_freshness (pure logic) ----


def test_fresh_when_commit_matches_head():
    result = check_service_freshness(_svc(), "abc123", NOW - timedelta(hours=1), "abc123", now=NOW)
    assert result["status"] == "fresh"


def test_behind_but_recent_within_stale_window():
    result = check_service_freshness(_svc(), "old123", NOW - timedelta(hours=2), "new456", now=NOW)
    assert result["status"] == "behind_but_recent"
    assert result["age_hours"] == 2.0


def test_stale_beyond_24_hours():
    result = check_service_freshness(
        _svc(), "old123", NOW - timedelta(hours=STALE_HOURS + 6), "new456", now=NOW
    )
    assert result["status"] == "stale"
    assert result["age_hours"] == 30.0


def test_unknown_when_no_commit_metadata():
    result = check_service_freshness(_svc(), None, None, "new456", now=NOW)
    assert result["status"] == "unknown"


# ---- raise_stale_alert ----


async def test_raise_stale_alert_writes_real_row_shape():
    fake = _FakeSupabase()
    result = {
        "service": "mse-api",
        "status": "stale",
        "deployed_commit": "fcb3ab06dd9ddc28f67cad58067d9017cc66a6f0",
        "head_commit": "eb49e96bd62a1557f52cc72ab8eabc0a80e55002",
        "age_hours": 30.5,
    }
    raise_stale_alert(result, supabase_client=fake)

    events = fake.inserted["mse_monitoring_events"]
    assert len(events) == 1
    event = events[0]
    assert event["product_slug"] == "dist-infra"
    assert event["severity"] == "P2"
    assert event["context"] == "mse-api"
    assert event["status"] == "open"
    assert event["requires_human_decision"] is True
    assert "mse-api" in event["recommended_action"]


async def test_raise_stale_alert_dedups_against_open_event():
    fake = _FakeSupabase(
        data={
            "mse_monitoring_events": [
                {"id": "1", "product_slug": "dist-infra", "context": "mse-api", "status": "open"}
            ]
        }
    )
    result = {
        "service": "mse-api", "status": "stale", "deployed_commit": "abc",
        "head_commit": "def", "age_hours": 40.0,
    }
    raise_stale_alert(result, supabase_client=fake)
    assert fake.inserted == {}


# ---- run_freshness_check (real orchestration, injected dependencies) ----


async def test_run_freshness_check_reports_fresh_and_stale_correctly():
    fake = _FakeSupabase()
    services = [_svc("fresh-svc"), _svc("stale-svc")]

    def get_deployed_state(service):
        if service.name == "fresh-svc":
            return ("same-sha", NOW - timedelta(hours=1))
        return ("old-sha", NOW - timedelta(hours=48))

    def get_head_commit(repo, branch):
        return "same-sha" if repo == "KDavisCodeCloud/test-repo" else "other-sha"

    results = run_freshness_check(services, get_deployed_state, get_head_commit, supabase_client=fake, now=NOW)

    statuses = {r["service"]: r["status"] for r in results}
    assert statuses["fresh-svc"] == "fresh"
    assert statuses["stale-svc"] == "stale"
    # Only the genuinely stale service raised a real alert.
    assert len(fake.inserted["mse_monitoring_events"]) == 1
    assert fake.inserted["mse_monitoring_events"][0]["context"] == "stale-svc"


async def test_run_freshness_check_does_not_alert_when_all_fresh():
    fake = _FakeSupabase()
    services = [_svc("a"), _svc("b")]

    def get_deployed_state(service):
        return ("sha1", NOW - timedelta(hours=1))

    def get_head_commit(repo, branch):
        return "sha1"

    run_freshness_check(services, get_deployed_state, get_head_commit, supabase_client=fake, now=NOW)
    assert fake.inserted == {}
