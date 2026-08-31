from fastapi.testclient import TestClient

from api.main import app
import api.routers.dist_attribution as dist_attribution_router

client = TestClient(app)


def _seed_product(fake_db, slug="small-portfolio-hub", product_id="prod-1"):
    fake_db.responses["mse_products"] = [{"id": product_id, "slug": slug}]


def test_track_unknown_product_404s(monkeypatch, fake_db):
    monkeypatch.setattr(dist_attribution_router, "get_supabase", lambda: fake_db)
    fake_db.responses["mse_products"] = []

    resp = client.post("/dist/attribution/track", json={
        "product_slug": "not-a-real-product", "anon_id": "anon-1", "channel": "organic",
    })
    assert resp.status_code == 404


def test_track_no_auth_required(monkeypatch, fake_db):
    # Real requirement: an anonymous pre-signup visitor has no JWT.
    monkeypatch.setattr(dist_attribution_router, "get_supabase", lambda: fake_db)
    _seed_product(fake_db)

    resp = client.post("/dist/attribution/track", json={
        "product_slug": "small-portfolio-hub", "anon_id": "anon-1", "channel": "organic",
    })
    assert resp.status_code == 201


def test_track_first_touch_is_index_1(monkeypatch, fake_db):
    monkeypatch.setattr(dist_attribution_router, "get_supabase", lambda: fake_db)
    _seed_product(fake_db)

    resp = client.post("/dist/attribution/track", json={
        "product_slug": "small-portfolio-hub", "anon_id": "anon-1", "channel": "organic",
    })
    assert resp.json()["touch_index"] == 1

    inserts = [q for q in fake_db.executed if q.table_name == "mse_attribution_touches" and q._payload is not None and "channel" in q._payload]
    assert len(inserts) == 1
    assert inserts[0]._payload["channel"] == "organic"
    assert inserts[0]._payload["product_id"] == "prod-1"


def test_track_second_touch_increments_index(monkeypatch, fake_db):
    monkeypatch.setattr(dist_attribution_router, "get_supabase", lambda: fake_db)
    _seed_product(fake_db)
    fake_db.responses["mse_attribution_touches"] = [{"id": "t1"}]  # one existing touch for this anon_id

    resp = client.post("/dist/attribution/track", json={
        "product_slug": "small-portfolio-hub", "anon_id": "anon-1", "channel": "direct",
    })
    assert resp.json()["touch_index"] == 2


def test_backfill_rejects_non_uuid_tenant_id(monkeypatch, fake_db):
    monkeypatch.setattr(dist_attribution_router, "get_supabase", lambda: fake_db)
    _seed_product(fake_db)

    resp = client.post("/dist/attribution/backfill", json={
        "product_slug": "small-portfolio-hub", "anon_id": "anon-1", "tenant_id": "not-a-uuid",
    })
    assert resp.status_code == 400


def test_backfill_updates_touches_and_writes_signup_event(monkeypatch, fake_db):
    monkeypatch.setattr(dist_attribution_router, "get_supabase", lambda: fake_db)
    _seed_product(fake_db)
    fake_db.responses["mse_funnel_events"] = []  # no existing signup event yet
    real_tenant_id = "11111111-1111-1111-1111-111111111111"

    resp = client.post("/dist/attribution/backfill", json={
        "product_slug": "small-portfolio-hub", "anon_id": "anon-1", "tenant_id": real_tenant_id,
    })
    assert resp.status_code == 200

    updates = [q for q in fake_db.executed if q.table_name == "mse_attribution_touches" and q.calls and q.calls[0][0] == "update"]
    assert len(updates) == 1
    assert updates[0]._payload["tenant_id"] == real_tenant_id

    signup_inserts = [q for q in fake_db.executed if q.table_name == "mse_funnel_events" and q.calls and q.calls[0][0] == "insert"]
    assert len(signup_inserts) == 1
    assert signup_inserts[0]._payload["step"] == "signup"
    assert signup_inserts[0]._payload["tenant_id"] == real_tenant_id


def test_backfill_does_not_duplicate_signup_event(monkeypatch, fake_db):
    monkeypatch.setattr(dist_attribution_router, "get_supabase", lambda: fake_db)
    _seed_product(fake_db)
    real_tenant_id = "11111111-1111-1111-1111-111111111111"
    fake_db.responses["mse_funnel_events"] = [{"id": "existing-signup-event"}]

    client.post("/dist/attribution/backfill", json={
        "product_slug": "small-portfolio-hub", "anon_id": "anon-1", "tenant_id": real_tenant_id,
    })

    signup_inserts = [q for q in fake_db.executed if q.table_name == "mse_funnel_events" and q.calls and q.calls[0][0] == "insert"]
    assert len(signup_inserts) == 0
