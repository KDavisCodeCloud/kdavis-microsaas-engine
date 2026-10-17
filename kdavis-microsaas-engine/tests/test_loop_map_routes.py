"""Loop Map write paths: check / undo / lap / reset / notes / snapshot.

Uses a small in-memory stand-in for the supabase client that really filters,
because the behavior under test (current lap, auto-recorded laps, undo
touching only the current lap) is all about WHICH rows a query touches.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers import loop_map

AUTH = {"Authorization": "Bearer test-marketing-api-key", "X-Loop-Actor": "kelvin@example.com"}
P = "11111111-1111-1111-1111-111111111111"


class Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload = db, table, [], "select", None
        self._order, self._limit, self._conflict, self._ignore = None, None, None, False

    def select(self, *a, **k): return self
    def eq(self, c, v): self.filters.append(lambda r: r.get(c) == v); return self
    def gte(self, c, v): self.filters.append(lambda r: str(r.get(c) or "") >= v); return self
    def order(self, c, desc=False): self._order = (c, desc); return self
    def limit(self, n): self._limit = n; return self
    def insert(self, p): self.op, self.payload = "insert", p; return self
    def update(self, p): self.op, self.payload = "update", p; return self
    def delete(self): self.op = "delete"; return self

    def upsert(self, p, on_conflict=None, ignore_duplicates=False):
        self.op, self.payload, self._conflict, self._ignore = "upsert", p, on_conflict, ignore_duplicates
        return self

    def _match(self):
        return [r for r in self.db.rows(self.table) if all(f(r) for f in self.filters)]

    def execute(self):
        rows = self.db.rows(self.table)
        if self.op == "select":
            out = self._match()
            if self._order:
                out.sort(key=lambda r: str(r.get(self._order[0]) or ""), reverse=self._order[1])
            return type("R", (), {"data": out[: self._limit] if self._limit else out})
        if self.op in ("insert", "upsert"):
            new = []
            for p in self.payload if isinstance(self.payload, list) else [self.payload]:
                r = {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
                     "completed_at": datetime.now(timezone.utc).isoformat(), **p}
                if self._conflict:
                    keys = self._conflict.split(",")
                    dup = [x for x in rows if all(x.get(k) == r.get(k) for k in keys)]
                    if dup:
                        if not self._ignore:
                            dup[0].update(p)
                        continue
                rows.append(r); new.append(r)
            return type("R", (), {"data": new})
        hit = self._match()
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return type("R", (), {"data": hit})
        for r in hit:
            rows.remove(r)
        return type("R", (), {"data": hit})


class DB:
    def __init__(self):
        self.t = {}

    def rows(self, name):
        return self.t.setdefault(name, [])

    def table(self, name):
        return Q(self, name)


@pytest.fixture
def db(monkeypatch):
    d = DB()
    d.rows("mse_loop_stations").extend(
        {"product_id": P, "step_order": i, "name": f"S{i}", "owner": "you", "description": "",
         "next_action": "", "status_rule_key": "none.not_built", "lane_link": None, "target_hint": None}
        for i in (1, 2, 3))
    d.rows("mse_loop_products").append({
        "product_id": P, "tab_order": 1, "slug": "p", "display_name": "P", "playbook": "content",
        "playbook_line": "", "verdict_tone": "ok", "verdict_title": "", "verdict_body": "", "math": [], "also": []})
    monkeypatch.setattr(loop_map, "get_supabase", lambda: d)
    monkeypatch.setattr(loop_map, "load_sources", lambda db, now=None: {"products": [], "leads": [], "seqs": [],
                                                                          "convs": []})
    return d


@pytest.fixture
def client():
    return TestClient(app)


def post(client, path, json=None):
    r = client.post(f"/marketing/loop-map{path}", headers=AUTH, json=json)
    assert r.status_code == 200, r.text
    return r.json()


def test_requires_marketing_key(client):
    assert client.get("/marketing/loop-map").status_code in (401, 403)


def test_completing_last_step_records_the_lap_and_resets_checks(client, db):
    post(client, f"/{P}/steps/1/complete")
    post(client, f"/{P}/steps/2/complete")
    last = post(client, f"/{P}/steps/3/complete")
    assert last["lap_completed"] == 1
    assert db.rows("mse_loop_laps")[0]["lap"] == 1
    assert {r["completed_by"] for r in db.rows("mse_loop_progress")} == {"kelvin@example.com"}
    m = loop_map.build_map(db)
    prod = m["products"][0]
    assert prod["lap"] == 2 and prod["laps_finished"] == 1
    assert not any(s["done"] for s in prod["stations"])


def test_undo_touches_only_the_current_lap(client, db):
    post(client, f"/{P}/laps/complete")
    post(client, f"/{P}/steps/1/complete")
    post(client, f"/{P}/steps/1/undo")
    laps = sorted((r["lap"], r["step_order"]) for r in db.rows("mse_loop_progress"))
    assert laps == [(1, 1), (1, 2), (1, 3)]


def test_reset_clears_current_lap_without_recording_one(client, db):
    post(client, f"/{P}/steps/1/complete")
    post(client, f"/{P}/laps/reset")
    assert db.rows("mse_loop_progress") == [] and db.rows("mse_loop_laps") == []


def test_lap_records_open_holes_and_notes_never_block(client, db):
    note = post(client, f"/{P}/notes", {"step_order": 2, "kind": "hole", "text": "missing buyer"})["note"]
    post(client, f"/{P}/notes", {"step_order": 2, "kind": "note", "text": "fyi"})
    done = post(client, f"/{P}/laps/complete")
    assert done["open_holes_at_completion"] == 1
    post(client, f"/notes/{note['id']}/resolve", {"resolved": True})
    assert db.rows("mse_loop_notes")[0]["resolved_at"]
    post(client, f"/notes/{note['id']}/resolve", {"resolved": False})
    assert db.rows("mse_loop_notes")[0]["resolved_at"] is None
    assert len(db.rows("mse_loop_notes")) == 2


def test_note_validation(client, db):
    r = client.post(f"/marketing/loop-map/{P}/notes", headers=AUTH,
                    json={"step_order": 1, "kind": "hole", "text": "x" * 2001})
    assert r.status_code == 422
    r = client.post(f"/marketing/loop-map/{P}/notes", headers=AUTH, json={"step_order": 9, "kind": "note", "text": "x"})
    assert r.status_code == 404


def test_there_is_no_delete_route_for_notes(client, db):
    assert client.delete(f"/marketing/loop-map/notes/{uuid.uuid4()}", headers=AUTH).status_code in (404, 405)


def test_snapshot_writes_once_per_hour(db):
    first = loop_map.snapshot(db)
    assert first["written"] == 3 and first["by_status"] == {"not_built": 3}
    assert loop_map.snapshot(db)["skipped"] is True
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    assert loop_map.snapshot(db, now=later)["written"] == 3


def test_history_strip_uses_the_days_worst_status(db):
    today = datetime.now(timezone.utc)
    db.rows("mse_loop_status_history").extend([
        {"product_id": P, "step_order": 1, "status": "working", "captured_at": today.isoformat()},
        {"product_id": P, "step_order": 1, "status": "blocked", "captured_at": (today - timedelta(minutes=5)).isoformat()},
    ])
    desk = loop_map.build_map(db, today)["products"][0]["stations"][0]
    assert len(desk["history"]) == loop_map.HISTORY_DAYS
    assert desk["history"][-1] == {"day": today.date().isoformat(), "status": "blocked"}
