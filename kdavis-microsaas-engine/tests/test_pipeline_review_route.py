import time

import jwt
import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from api.main import app
import api.routers.pipeline as pipeline_router

client = TestClient(app)


def _auth_header(role: str = "admin", sub: str = "kelvin") -> dict:
    token = jwt.encode(
        {"sub": sub, "app_metadata": {"role": role}, "aud": "authenticated", "exp": int(time.time()) + 3600},
        "placeholder-jwt-secret",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_review_requires_admin_role():
    resp = client.post(
        "/pipeline/opp-1/review", json={"decision": "rejected"}, headers=_auth_header(role="marketing"),
    )
    assert resp.status_code == 403


def test_review_requires_auth_at_all():
    resp = client.post("/pipeline/opp-1/review", json={"decision": "rejected"})
    assert resp.status_code == 401


def test_reject_archives_full_row_then_deletes_it(monkeypatch, fake_db):
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    original_row = {
        "id": "opp-1", "solution_concept": "Some Product",
        "verdict_v2_output": {"verdict": "CONDITIONAL", "final_mrr_floor": 4200},
    }
    fake_db.responses["opportunity_pipeline"] = [original_row]

    resp = client.post(
        "/pipeline/opp-1/review",
        json={"decision": "rejected", "comment": "Overlaps with an existing tool we already tried"},
        headers=_auth_header(sub="kelvin"),
    )

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "opportunity_id": "opp-1"}

    # Full row (including the agent's research reasoning) archived before delete.
    archive_inserts = [c for c in fake_db.executed if c.table_name == "opportunity_pipeline_rejections" and c.calls[0][0] == "insert"]
    assert len(archive_inserts) == 1
    assert archive_inserts[0]._payload["original_opportunity"] == original_row
    assert archive_inserts[0]._payload["rejected_by"] == "kelvin"
    assert archive_inserts[0]._payload["rejection_comment"] == "Overlaps with an existing tool we already tried"

    # Then actually deleted from opportunity_pipeline, not just flagged.
    deletes = [c for c in fake_db.executed if c.table_name == "opportunity_pipeline" and c.calls[0][0] == "delete"]
    assert len(deletes) == 1

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "lose"
    assert audits[-1]._payload["metadata"]["decision"] == "rejected"


def test_reject_404s_when_opportunity_not_found(monkeypatch, fake_db):
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    fake_db.responses["opportunity_pipeline"] = []

    resp = client.post(
        "/pipeline/opp-missing/review", json={"decision": "rejected"}, headers=_auth_header(),
    )

    assert resp.status_code == 404
    # Nothing should be archived or deleted for a row that was never found.
    assert not [c for c in fake_db.executed if c.table_name == "opportunity_pipeline_rejections"]


def test_approve_pushes_into_the_build_queue_and_records_win(monkeypatch, fake_db):
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    fake_db.responses["opportunity_pipeline"] = [{
        "id": "opp-1", "status": "READY_TO_BUILD", "human_review_status": "approved",
        "conservative_mrr_potential": 5000,
    }]

    resp = client.post(
        "/pipeline/opp-1/review", json={"decision": "approved", "comment": "This is the one"}, headers=_auth_header(sub="kelvin"),
    )

    assert resp.status_code == 200

    updates = [c for c in fake_db.executed if c.table_name == "opportunity_pipeline" and c.calls[0][0] == "update"]
    assert updates[-1]._payload["status"] == "READY_TO_BUILD"
    assert updates[-1]._payload["human_review_status"] == "approved"
    assert updates[-1]._payload["human_review_comment"] == "This is the one"
    assert updates[-1]._payload["human_reviewed_by"] == "kelvin"

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "win"

    # Approve must never touch the delete/archive path.
    assert not [c for c in fake_db.executed if c.table_name == "opportunity_pipeline_rejections"]
    assert not [c for c in fake_db.executed if c.table_name == "opportunity_pipeline" and c.calls[0][0] == "delete"]


def test_approve_404s_when_opportunity_not_found(monkeypatch, fake_db):
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    fake_db.responses["opportunity_pipeline"] = []

    resp = client.post(
        "/pipeline/opp-missing/review", json={"decision": "approved"}, headers=_auth_header(),
    )

    assert resp.status_code == 404


def test_approve_blocked_with_clear_message_when_mrr_never_computed(monkeypatch, fake_db):
    # Real bug 2026-07-18: approving an opportunity rejected via a
    # competitor-exists halt (MRR math never ran, conservative_mrr_potential
    # sits at 0) used to hit the DB's mrr_floor_check CHECK constraint and
    # crash as a raw, unhandled postgrest.exceptions.APIError -- which
    # bypasses CORS entirely regardless of middleware order, showing up in
    # the browser as "Failed to fetch" instead of a real error. Must now be
    # caught before ever reaching the DB, with a specific explanation.
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    fake_db.responses["opportunity_pipeline"] = [{
        "id": "opp-1", "status": "rejected", "conservative_mrr_potential": 0,
    }]

    resp = client.post(
        "/pipeline/opp-1/review", json={"decision": "approved"}, headers=_auth_header(sub="kelvin"),
    )

    assert resp.status_code == 422
    assert "MRR floor" in resp.json()["detail"]
    assert "$0" in resp.json()["detail"]


def test_approve_uses_the_row_own_price_adjusted_floor_not_a_flat_4000(monkeypatch, fake_db):
    # v3.0: a $25/mo product only needs to clear $3,500, not the old flat
    # $4,000 -- an opportunity at $3,600 MRR must be approvable even
    # though it would have been blocked under the pre-v3.0 flat floor.
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    fake_db.responses["opportunity_pipeline"] = [{
        "id": "opp-1", "status": "validated", "conservative_mrr_potential": 3600, "price_adjusted_floor": 3500,
    }]

    resp = client.post(
        "/pipeline/opp-1/review", json={"decision": "approved"}, headers=_auth_header(sub="kelvin"),
    )

    assert resp.status_code == 200


def test_approve_blocked_against_a_higher_price_adjusted_floor(monkeypatch, fake_db):
    # A $120/mo product needs $5,000, not $4,000 -- $4,200 must still be
    # blocked even though it would have cleared the old flat floor.
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    fake_db.responses["opportunity_pipeline"] = [{
        "id": "opp-1", "status": "validated", "conservative_mrr_potential": 4200, "price_adjusted_floor": 5000,
    }]

    resp = client.post(
        "/pipeline/opp-1/review", json={"decision": "approved"}, headers=_auth_header(sub="kelvin"),
    )

    assert resp.status_code == 422
    assert "$5,000" in resp.json()["detail"]

    # Must never have attempted the update at all -- the pre-check is what
    # replaces the raw DB crash.
    updates = [c for c in fake_db.executed if c.table_name == "opportunity_pipeline" and c.calls[0][0] == "update"]
    assert not updates


class _RaisingQuery:
    """Minimal fake that raises APIError on .execute(), regardless of
    which chained filter methods were called first -- used to prove a real
    DB-level failure is caught and converted to a clean HTTPException
    rather than propagating as a raw crash (which bypasses CORS)."""

    def __init__(self, error: APIError):
        self._error = error

    def select(self, *a, **k):
        return self

    def update(self, *a, **k):
        return self

    def insert(self, *a, **k):
        return self

    def delete(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        raise self._error


class _PassingQuery:
    def __init__(self, row):
        self._row = row

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return type("Result", (), {"data": self._row})()


def test_approve_db_failure_becomes_clean_502_not_a_raw_crash(monkeypatch):
    """
    The existence-check select and the later update both hit
    "opportunity_pipeline" -- to simulate a failure on specifically the
    update (not the initial read), .table() dispatches by call order: the
    first call (the select) succeeds with a real, approvable row; every
    call after that (the update) raises, standing in for a real DB-level
    failure. Confirms it surfaces as a clean 502 with a readable message,
    not a raw crash that bypasses CORS.
    """
    calls = {"n": 0}

    def table_dispatch(name):
        assert name == "opportunity_pipeline"
        calls["n"] += 1
        if calls["n"] == 1:
            return _PassingQuery({"id": "opp-1", "conservative_mrr_potential": 5000})
        return _RaisingQuery(APIError({"message": "simulated database outage", "code": "XXYYY"}))

    db = type("DB", (), {"table": staticmethod(table_dispatch)})()
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: db)

    resp = client.post(
        "/pipeline/opp-1/review", json={"decision": "approved"}, headers=_auth_header(sub="kelvin"),
    )

    assert resp.status_code == 502
    assert "simulated database outage" in resp.json()["detail"]
