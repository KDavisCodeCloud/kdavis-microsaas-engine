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
        lambda product_id, run_id, limit=None, pipeline="v2": captured.update(
            product_id=product_id, run_id=run_id, limit=limit, pipeline=pipeline),
    )

    resp = client.post("/marketing/leads/find", json={"product_id": "prod-1"}, headers=AUTH)

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["run_id"] == "run-1"
    assert payload["status"] == "pending"
    # The Brave budget decision now rides along on the response (decision 1e,
    # 2026-10-02) so a caller -- n8n included -- can report the budget without
    # a second request.
    assert payload["brave_budget"]["allowed"] is True
    assert payload["brave_budget"]["cap"] == 900
    assert payload["brave_budget"]["threshold"] == 720

    inserts = [c for c in fake_db.executed if c.table_name == "mse_lead_finder_runs" and c.calls[0][0] == "insert"]
    assert inserts[0]._payload["product_id"] == "prod-1"
    assert inserts[0]._payload["status"] == "pending"

    # Background task ran (TestClient executes it synchronously) with the
    # real run_id the insert produced -- not a placeholder. No limit was
    # given in the request, so it must reach the background task as None
    # (run_lead_finder_for_product then falls back to the ICP config's
    # own target_count).
    assert captured == {"product_id": "prod-1", "run_id": "run-1", "limit": None,
                        "pipeline": "v2"}


def test_find_leads_threads_limit_through_to_background_task(fake_db, monkeypatch):
    """Real gap fixed 2026-09-22: `limit` was accepted in the request body
    but silently dropped before ever reaching the background task, so a
    caller had no way to request a smaller/faster run than the ICP
    config's full target_count."""
    fake_db.responses["mse_lead_finder_runs"] = [{"id": "run-1"}]
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)

    captured = {}
    monkeypatch.setattr(
        leads_router, "_run_lead_finder_background",
        lambda product_id, run_id, limit=None, pipeline="v2": captured.update(
            product_id=product_id, run_id=run_id, limit=limit, pipeline=pipeline),
    )

    resp = client.post("/marketing/leads/find", json={"product_id": "prod-1", "limit": 3}, headers=AUTH)

    assert resp.status_code == 200
    assert captured == {"product_id": "prod-1", "run_id": "run-1", "limit": 3,
                        "pipeline": "v2"}


def test_run_lead_finder_background_passes_limit_through(monkeypatch):
    """One layer deeper than the route test above: _run_lead_finder_background
    itself must forward `limit` into run_lead_finder_for_product (imported
    locally inside the function, from agents.marketing.mkt_lead_finder),
    not just accept it."""
    import agents.marketing.mkt_lead_finder as mlf

    captured = {}
    monkeypatch.setattr(mlf, "run_lead_finder_for_product", lambda **kwargs: captured.update(kwargs))

    # pipeline="v1" is now explicit: a bare trigger runs scraper v2 instead
    # (2026-10-02), and this test is specifically about v1's forwarding.
    leads_router._run_lead_finder_background("prod-1", "run-1", limit=3, pipeline="v1")

    assert captured == {"product_id": "prod-1", "run_id": "run-1", "limit": 3}


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


# ── Brave budget guard on the trigger endpoint (decision 1e, 2026-10-02) ──

def test_find_leads_refuses_when_the_brave_budget_is_spent(fake_db, monkeypatch):
    """A refused run must leave NO run row behind -- a 'pending' row for a run
    that was never started is exactly the kind of misleading artifact this
    codebase keeps having to clean up."""
    from agents.marketing import brave_budget as bb
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)
    monkeypatch.setattr(
        leads_router, "_run_lead_finder_background",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not start a run")),
    )
    monkeypatch.setattr(
        bb, "check_budget",
        lambda db, **kw: bb.BudgetDecision(
            allowed=False, used=800, cap=900, threshold=720, projected=835,
            reason="already at 800/900 Brave queries this month"),
    )

    resp = client.post("/marketing/leads/find", json={"product_id": "prod-1"}, headers=AUTH)

    assert resp.status_code == 409, "409: the budget is exhausted, retrying now cannot help"
    detail = resp.json()["detail"]
    assert detail["error"] == "brave_budget_exhausted"
    assert detail["budget"]["used"] == 800
    inserts = [c for c in fake_db.executed
               if c.table_name == "mse_lead_finder_runs" and c.calls[0][0] == "insert"]
    assert not inserts, "a refused run must not create a run row"


def test_outbound_products_only_lists_enabled_ones(fake_db, monkeypatch):
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)
    fake_db.responses["mse_icp_configs"] = [
        {"product_id": "p-consulting", "vertical": None, "target_count": 50,
         "selling_stage": "active", "outbound_enabled": True},
    ]
    fake_db.responses["mse_products"] = [
        {"id": "p-consulting", "name": "THD Agentic Systems Consulting", "status": "active"},
    ]
    resp = client.get("/marketing/leads/outbound-products", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["products"][0]["name"] == "THD Agentic Systems Consulting"
    # The filter must be applied as a query, not in Python, so a large config
    # table cannot leak disabled products through pagination.
    calls = [c for c in fake_db.executed if c.table_name == "mse_icp_configs"]
    assert any("outbound_enabled" in str(c.calls) for c in calls)


def test_weekly_funnel_reports_real_reply_and_call_counts(fake_db, monkeypatch):
    """Decision 3 (2026-10-06): replies are marked by hand in the
    Conversations lane, so they are MEASURED data. 0 now means zero replies,
    not "we do not measure this"."""
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)
    fake_db.responses["mse_lead_finder_runs"] = []
    fake_db.responses["mse_products"] = []
    fake_db.responses["mse_leads"] = []
    fake_db.responses["mse_dm_sequences"] = []
    fake_db.responses["mse_outreach_conversations"] = []

    funnel = client.get("/marketing/leads/weekly-summary", headers=AUTH).json()["funnel"]
    assert funnel["replies"] == 0, "a measured zero, not null"
    assert funnel["calls_booked"] == 0
    assert "NOT MEASURED" not in funnel["notes"]["replies"]
    assert "marked by hand" in funnel["notes"]["replies"]


def test_the_funnel_counts_each_stage_from_its_own_timestamp(fake_db, monkeypatch):
    """A deal that replied Monday and booked a call Thursday must count in
    BOTH columns. Counting by current stage would only ever show it in the
    furthest one."""
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)
    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    fake_db.responses["mse_lead_finder_runs"] = []
    fake_db.responses["mse_products"] = []
    fake_db.responses["mse_leads"] = []
    fake_db.responses["mse_dm_sequences"] = []
    fake_db.responses["mse_outreach_conversations"] = [
        {"replied_at": recent, "call_booked_at": recent, "proposal_sent_at": None,
         "won_at": None, "lost_at": None},
        # Outside the window: must not be counted.
        {"replied_at": old, "call_booked_at": old, "proposal_sent_at": None,
         "won_at": None, "lost_at": None},
    ]
    funnel = client.get("/marketing/leads/weekly-summary", headers=AUTH).json()["funnel"]
    assert funnel["replies"] == 1
    assert funnel["calls_booked"] == 1
    assert funnel["proposals"] == 0

# ── Pipeline default: v2, not the legacy keyword path (2026-10-02) ────────

def test_find_leads_defaults_to_scraper_v2(monkeypatch):
    """The automated Sunday run was executing v1 -- the pipeline v2 was built
    to replace -- and reporting 'complete' after 1.4s with zero leads. A bare
    trigger must now reach run_scraper_v2_scout."""
    import agents.marketing.mkt_lead_finder as mlf
    called = {}
    monkeypatch.setattr(mlf, "run_scraper_v2_scout", lambda **kw: called.update(v2=kw))
    monkeypatch.setattr(mlf, "run_lead_finder_for_product",
                        lambda **kw: called.update(v1=kw))

    leads_router._run_lead_finder_background("prod-1", "run-1")

    assert "v2" in called, "a bare trigger must run scraper v2"
    assert "v1" not in called
    assert called["v2"]["product_id"] == "prod-1"
    assert called["v2"]["run_id"] == "run-1", "v2 must adopt the caller's run row"
    assert called["v2"]["max_queries"] == 40, "the standing per-run Brave cap"


def test_v1_is_still_reachable_explicitly(monkeypatch):
    import agents.marketing.mkt_lead_finder as mlf
    called = {}
    monkeypatch.setattr(mlf, "run_scraper_v2_scout", lambda **kw: called.update(v2=kw))
    monkeypatch.setattr(mlf, "run_lead_finder_for_product", lambda **kw: called.update(v1=kw))

    leads_router._run_lead_finder_background("prod-1", "run-1", limit=5, pipeline="v1")

    assert "v1" in called and "v2" not in called
    assert called["v1"]["limit"] == 5


def test_an_unknown_pipeline_is_rejected_not_silently_defaulted(fake_db, monkeypatch):
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)
    resp = client.post("/marketing/leads/find",
                       json={"product_id": "p", "pipeline": "v3"}, headers=AUTH)
    assert resp.status_code == 422


def test_scraper_v2_adopts_an_existing_run_row_instead_of_inserting(fake_db, monkeypatch):
    """Two rows for one run would double every per-run count."""
    import agents.marketing.mkt_lead_finder as mlf
    fake_db.responses["mse_icp_configs"] = []
    monkeypatch.setattr(mlf, "get_supabase", lambda: fake_db)
    try:
        mlf.run_scraper_v2_scout(product_id="prod-1", supabase_client=fake_db, run_id="existing-run")
    except Exception:
        pass  # the run itself will fail on fakes; only the row handling matters
    inserts = [c for c in fake_db.executed
               if c.table_name == "mse_lead_finder_runs" and c.calls[0][0] == "insert"]
    updates = [c for c in fake_db.executed
               if c.table_name == "mse_lead_finder_runs" and c.calls[0][0] == "update"]
    assert not inserts, "must not insert a second run row when given run_id"
    assert updates, "must mark the adopted row as running"


# ── Outreach header counters (decisions 3 + 5, 2026-10-05) ───────────────

def test_outreach_summary_counts_each_lane(fake_db, monkeypatch):
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)
    fake_db.responses["mse_leads"] = [
        {"id": "a", "contact_status": "pending"},
        {"id": "b", "contact_status": "needs_review"},
        {"id": "c", "contact_status": "found"},
    ]
    fake_db.responses["mse_dm_sequences"] = [
        {"id": "1", "status": "pending_hitl"},
        {"id": "2", "status": "pending_hitl"},
        {"id": "3", "status": "approved_manual"},
        {"id": "4", "status": "awaiting_contact"},
        {"id": "5", "status": "rejected_hitl"},
    ]
    body = client.get("/marketing/outreach/summary", headers=AUTH).json()
    # needs_review counts as awaiting a buyer: a contact nobody has verified
    # is not a buyer yet.
    assert body["awaiting_buyer"] == 2
    assert body["awaiting_approval"] == 2
    assert body["ready_to_paste"] == 1
    assert body["parked_awaiting_contact"] == 1
    assert body["daily_send_cap"] >= 1


def test_outreach_summary_replies_is_a_real_count(fake_db, monkeypatch):
    monkeypatch.setattr(leads_router, "get_supabase", lambda: fake_db)
    fake_db.responses["mse_leads"] = []
    fake_db.responses["mse_dm_sequences"] = []
    fake_db.responses["mse_outreach_conversations"] = []
    body = client.get("/marketing/outreach/summary", headers=AUTH).json()
    assert body["replies_this_week"] == 0, "measured zero, not null"
    assert "NOT MEASURED" not in body["notes"]["replies_this_week"]


def test_outreach_summary_requires_auth():
    assert client.get("/marketing/outreach/summary").status_code == 401


def test_outreach_summary_prefix_is_exempt_from_tenant_context():
    """A route outside the exemption list fails with the tenant middleware's
    own rejection, which looks nothing like an auth error."""
    import inspect
    source = inspect.getsource(tenant_context_middleware)
    assert "/marketing/outreach" in source
