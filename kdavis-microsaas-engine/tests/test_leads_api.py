"""
api/routers/leads.py — POST /marketing/leads/find, GET /marketing/leads/runs/{id},
GET /marketing/leads, POST /marketing/icp, GET /marketing/icp/{product_id}. Same
MARKETING_API_KEY auth shape as tests/test_linkedin_intake_routes.py; see that
file's module docstring for why the /marketing/leads and /marketing/icp prefix
checks exist in tenant_context.py at all.
"""
from fastapi.testclient import TestClient

import api.routers.leads as leads_router
from api.main import app
from api.middleware.tenant_context import tenant_context_middleware

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-marketing-api-key"}


def test_leads_prefix_is_covered_by_a_public_path_check_source():
    import inspect
    source = inspect.getsource(tenant_context_middleware)
    assert "/marketing/leads" in source and "/marketing/icp" in source


def test_find_leads_requires_auth():
    resp = client.post("/marketing/leads/find", json={"product_id": "prod-1"})
    assert resp.status_code == 401


def test_find_leads_rejects_wrong_api_key():
    resp = client.post(
        "/marketing/leads/find", json={"product_id": "prod-1"},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401


def test_find_leads_returns_run_id(fake_db, monkeypatch):
    fake_db.responses["mse_lead_finder_runs"] = [{"id": "run-1"}]
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    captured = {}
    monkeypatch.setattr(
        leads_router, "_run_lead_finder_background",
        lambda product_id, run_id: captured.update(product_id=product_id, run_id=run_id),
    )

    resp = client.post("/marketing/leads/find", json={"product_id": "prod-1"}, headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"run_id": "run-1", "status": "pending"}

    inserts = [c for c in fake_db.executed if c.table_name == "mse_lead_finder_runs" and c.calls[0][0] == "insert"]
    assert inserts[0]._payload["product_id"] == "prod-1"
    assert inserts[0]._payload["status"] == "pending"

    # Background task ran (TestClient executes it synchronously) with the
    # real run_id the insert produced -- not a placeholder.
    assert captured == {"product_id": "prod-1", "run_id": "run-1"}


def test_find_leads_500_when_run_row_insert_fails(fake_db, monkeypatch):
    fake_db.responses["mse_lead_finder_runs"] = []
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.post("/marketing/leads/find", json={"product_id": "prod-1"}, headers=AUTH)
    assert resp.status_code == 500


def test_get_run_requires_auth():
    resp = client.get("/marketing/leads/runs/run-1")
    assert resp.status_code == 401


def test_get_run_404_when_not_found(fake_db, monkeypatch):
    fake_db.responses["mse_lead_finder_runs"] = []
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/leads/runs/run-1", headers=AUTH)
    assert resp.status_code == 404


def test_get_run_returns_status(fake_db, monkeypatch):
    fake_db.responses["mse_lead_finder_runs"] = [{"id": "run-1", "status": "complete", "leads_found": 5}]
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/leads/runs/run-1", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"id": "run-1", "status": "complete", "leads_found": 5}


def test_list_leads_requires_auth():
    resp = client.get("/marketing/leads")
    assert resp.status_code == 401


def test_list_leads_filters_by_product_id(fake_db, monkeypatch):
    fake_db.responses["mse_leads"] = [{"id": "lead-1", "product_id": "prod-1"}]
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/leads?product_id=prod-1", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"leads": [{"id": "lead-1", "product_id": "prod-1"}], "limit": 50, "offset": 0}

    select_query = [c for c in fake_db.executed if c.table_name == "mse_leads" and c.calls[0][0] == "select"][0]
    assert ("product_id", "prod-1") in select_query._filters


def test_list_leads_applies_pagination_range(fake_db, monkeypatch):
    fake_db.responses["mse_leads"] = []
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/leads?limit=10&offset=20", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"leads": [], "limit": 10, "offset": 20}

    select_query = [c for c in fake_db.executed if c.table_name == "mse_leads" and c.calls[0][0] == "select"][0]
    range_calls = [c for c in select_query.calls if c[0] == "range"]
    assert range_calls == [("range", 20, 29)]


def test_icp_products_requires_auth():
    resp = client.get("/marketing/leads/icp-products")
    assert resp.status_code == 401


def test_icp_products_joins_product_names(fake_db, monkeypatch):
    fake_db.responses["mse_icp_configs"] = [
        {"product_id": "prod-1", "vertical": "real_estate", "target_count": 100},
    ]
    fake_db.responses["mse_products"] = [{"id": "prod-1", "name": "TradesDesk", "slug": "tradesdesk"}]
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/leads/icp-products", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {
        "products": [
            {"product_id": "prod-1", "name": "TradesDesk", "slug": "tradesdesk", "vertical": "real_estate", "target_count": 100},
        ]
    }


def test_icp_products_empty_when_no_icp_configs(fake_db, monkeypatch):
    fake_db.responses["mse_icp_configs"] = []
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/leads/icp-products", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"products": []}


def test_pipeline_summary_requires_auth():
    resp = client.get("/marketing/leads/pipeline-summary")
    assert resp.status_code == 401


def test_pipeline_summary_counts_by_stage(fake_db, monkeypatch):
    fake_db.responses["mse_leads"] = [
        {"stage": "new"}, {"stage": "new"}, {"stage": "contacted"}, {"stage": "won"},
    ]
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/leads/pipeline-summary?product_id=prod-1", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["product_id"] == "prod-1"
    assert body["total"] == 4
    assert body["stages"] == {
        "new": 2, "contacted": 1, "replied": 0, "qualified": 0, "demo": 0, "won": 1, "lost": 0,
    }

    select_query = [c for c in fake_db.executed if c.table_name == "mse_leads" and c.calls[0][0] == "select"][0]
    assert ("product_id", "prod-1") in select_query._filters


def test_pipeline_summary_defaults_missing_stage_to_new(fake_db, monkeypatch):
    fake_db.responses["mse_leads"] = [{"stage": None}]
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/leads/pipeline-summary", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json()["stages"]["new"] == 1


def test_outreach_summary_requires_auth():
    resp = client.get("/marketing/leads/outreach-summary")
    assert resp.status_code == 401


def test_outreach_summary_counts_sent_and_meetings_per_product(fake_db, monkeypatch):
    fake_db.responses["mse_dm_sequences"] = [
        {"product_id": "prod-1", "touch_1_sent_at": "2026-09-01T00:00:00Z"},
        {"product_id": "prod-1", "touch_1_sent_at": "2026-09-02T00:00:00Z"},
        {"product_id": "prod-1", "touch_1_sent_at": None},  # never sent -- must not count
        {"product_id": "prod-2", "touch_1_sent_at": "2026-09-01T00:00:00Z"},
    ]
    fake_db.responses["mse_leads"] = [
        {"product_id": "prod-1", "stage": "demo"},
        {"product_id": "prod-1", "stage": "won"},
        {"product_id": "prod-1", "stage": "contacted"},  # not yet a meeting -- must not count
        {"product_id": "prod-2", "stage": "new"},
    ]
    fake_db.responses["mse_products"] = [
        {"id": "prod-1", "name": "TradesDesk"},
        {"id": "prod-2", "name": "DecodedSix"},
    ]
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/leads/outreach-summary", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {
        "products": [
            {"product_id": "prod-1", "name": "TradesDesk", "sent": 2, "meetings": 2},
            {"product_id": "prod-2", "name": "DecodedSix", "sent": 1, "meetings": 0},
        ]
    }


def test_outreach_summary_empty_when_no_sequences_or_leads(fake_db, monkeypatch):
    fake_db.responses["mse_dm_sequences"] = []
    fake_db.responses["mse_leads"] = []
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/leads/outreach-summary", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"products": []}


def test_upsert_icp_config_requires_auth():
    resp = client.post("/marketing/icp", json={"product_id": "prod-1"})
    assert resp.status_code == 401


def test_upsert_icp_config_writes_config(fake_db, monkeypatch):
    fake_db.responses["mse_icp_configs"] = [{"product_id": "prod-1", "vertical": "real_estate"}]
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.post(
        "/marketing/icp",
        json={"product_id": "prod-1", "vertical": "real_estate", "job_titles": ["broker"], "locations": ["Phoenix AZ"]},
        headers=AUTH,
    )

    assert resp.status_code == 200
    assert resp.json() == {"product_id": "prod-1", "vertical": "real_estate"}

    upserts = [c for c in fake_db.executed if c.table_name == "mse_icp_configs" and c.calls[0][0] == "upsert"]
    assert upserts[0]._payload["product_id"] == "prod-1"
    assert upserts[0].calls[0][2] == "product_id"  # on_conflict


def test_get_icp_config_requires_auth():
    resp = client.get("/marketing/icp/prod-1")
    assert resp.status_code == 401


def test_get_icp_config_404_when_not_found(fake_db, monkeypatch):
    fake_db.responses["mse_icp_configs"] = []
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/icp/prod-1", headers=AUTH)
    assert resp.status_code == 404
