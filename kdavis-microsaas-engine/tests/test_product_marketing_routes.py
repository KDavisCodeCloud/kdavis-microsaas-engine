"""
api/routers/product_marketing.py — POST /products/{id}/run-research and
/run-campaign. Dashboard-session-JWT auth (request.state.role/tenant_id,
same shape as tests/test_factory_routes.py's trigger_build), NOT the
MARKETING_API_KEY pattern api/routers/marketing.py's automation routes use.
"""
import time

import jwt
from fastapi.testclient import TestClient

from api.main import app
import api.routers.product_marketing as product_marketing_router

client = TestClient(app)


def _auth_header(role: str = "admin", sub: str = "operator-1") -> dict:
    token = jwt.encode(
        {"sub": sub, "app_metadata": {"role": role}, "aud": "authenticated", "exp": int(time.time()) + 3600},
        "placeholder-jwt-secret",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


# ── auth ──────────────────────────────────────────────────────────────

def test_run_research_requires_admin_role():
    resp = client.post("/products/prod-1/run-research", headers=_auth_header(role="marketing"))
    assert resp.status_code == 403


def test_run_research_requires_auth_at_all():
    resp = client.post("/products/prod-1/run-research")
    assert resp.status_code == 401


def test_run_campaign_requires_admin_role():
    resp = client.post("/products/prod-1/run-campaign", headers=_auth_header(role="marketing"))
    assert resp.status_code == 403


def test_run_campaign_requires_auth_at_all():
    resp = client.post("/products/prod-1/run-campaign")
    assert resp.status_code == 401


# ── run-research ──────────────────────────────────────────────────────

def test_run_research_404_when_product_not_found(fake_db, monkeypatch):
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)
    resp = client.post("/products/missing-prod/run-research", headers=_auth_header())
    assert resp.status_code == 404


def test_run_research_derives_keywords_and_queues(fake_db, monkeypatch):
    fake_db.responses["opportunity_pipeline"] = [
        {"vertical": "Real Estate / Property Management", "solution_concept": "Automated showing follow-up", "pain_point": "agents lose deals"}
    ]
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)

    captured = {}
    monkeypatch.setattr(
        product_marketing_router, "_run_research",
        lambda product_id, niche_keywords: captured.update(product_id=product_id, niche_keywords=niche_keywords),
    )

    resp = client.post("/products/prod-1/run-research", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "queued", "product_id": "prod-1",
        "niche_keywords": ["Real Estate / Property Management", "Automated showing follow-up"],
    }
    assert captured["product_id"] == "prod-1"
    assert captured["niche_keywords"] == ["Real Estate / Property Management", "Automated showing follow-up"]


# ── run-campaign ──────────────────────────────────────────────────────

def test_run_campaign_404_when_product_not_found(fake_db, monkeypatch):
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)
    resp = client.post("/products/missing-prod/run-campaign", headers=_auth_header())
    assert resp.status_code == 404


def test_run_campaign_409_when_no_research_report_yet(fake_db, monkeypatch):
    fake_db.responses["opportunity_pipeline"] = [{"vertical": "Real Estate / Property Management"}]
    fake_db.responses["mse_research_reports"] = []
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)

    resp = client.post("/products/prod-1/run-campaign", headers=_auth_header())

    assert resp.status_code == 409
    assert "research" in resp.json()["detail"].lower()


def test_run_campaign_queues_with_research_opp_id_equal_to_product_id(fake_db, monkeypatch):
    fake_db.responses["opportunity_pipeline"] = [{"vertical": "Real Estate / Property Management"}]
    fake_db.responses["mse_research_reports"] = [{"id": "report-1"}]
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)

    captured = {}
    monkeypatch.setattr(
        product_marketing_router, "_run_campaign",
        lambda product_id, vertical: captured.update(product_id=product_id, vertical=vertical),
    )

    resp = client.post("/products/prod-1/run-campaign", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json() == {"status": "queued", "product_id": "prod-1"}
    assert captured == {"product_id": "prod-1", "vertical": "Real Estate / Property Management"}


# ── linkedin-leads (CSV paste intake) ────────────────────────────────

CSV_HEADER = "first_name,last_name,title,company,linkedin_url,location\n"


def test_linkedin_leads_requires_admin_role():
    resp = client.post(
        "/products/prod-1/linkedin-leads",
        json={"csv_text": CSV_HEADER, "source": "linkedin_manual"},
        headers=_auth_header(role="marketing"),
    )
    assert resp.status_code == 403


def test_linkedin_leads_requires_auth_at_all():
    resp = client.post("/products/prod-1/linkedin-leads", json={"csv_text": CSV_HEADER})
    assert resp.status_code == 401


def test_linkedin_leads_404_when_product_not_found(fake_db, monkeypatch):
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)
    resp = client.post(
        "/products/missing-prod/linkedin-leads",
        json={"csv_text": CSV_HEADER + "Jane,Doe,Team Lead,Acme Realty,https://linkedin.com/in/janedoe,Phoenix AZ\n"},
        headers=_auth_header(),
    )
    assert resp.status_code == 404


def test_linkedin_leads_400_for_invalid_source(fake_db, monkeypatch):
    fake_db.responses["opportunity_pipeline"] = [{"vertical": "Real Estate / Property Management"}]
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)
    resp = client.post(
        "/products/prod-1/linkedin-leads",
        json={"csv_text": CSV_HEADER + "Jane,Doe,Team Lead,Acme Realty,https://linkedin.com/in/janedoe,Phoenix AZ\n", "source": "cold_email"},
        headers=_auth_header(),
    )
    assert resp.status_code == 400


def test_linkedin_leads_400_when_no_rows_parsed(fake_db, monkeypatch):
    fake_db.responses["opportunity_pipeline"] = [{"vertical": "Real Estate / Property Management"}]
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)
    resp = client.post(
        "/products/prod-1/linkedin-leads",
        json={"csv_text": CSV_HEADER, "source": "linkedin_manual"},  # header only, no data rows
        headers=_auth_header(),
    )
    assert resp.status_code == 400


def test_linkedin_leads_happy_path_inserts_and_returns_counts(fake_db, monkeypatch):
    import agents.marketing.mkt_li_intake as li_intake_module

    fake_db.responses["opportunity_pipeline"] = [{"vertical": "Real Estate / Property Management"}]
    fake_db.responses["mse_linkedin_leads"] = []  # no existing leads -- select for dedup finds nothing
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)
    monkeypatch.setattr(li_intake_module, "get_supabase", lambda: fake_db)

    csv_text = (
        CSV_HEADER
        + "Jane,Doe,Team Lead,Acme Realty,https://linkedin.com/in/janedoe,Phoenix AZ\n"
        + "John,Smith,Managing Broker,Smith Realty,https://linkedin.com/in/johnsmith,Dallas TX\n"
    )

    resp = client.post(
        "/products/prod-1/linkedin-leads",
        json={"csv_text": csv_text, "source": "linkedin_manual"},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    # "added" mirrors FakeQuery.execute()'s return for the insert call, which
    # (unlike real supabase-py) doesn't echo the inserted payload back -- it
    # replays whatever fake_db.responses["mse_linkedin_leads"] was seeded
    # with, same value the earlier dedup SELECT against that table already
    # consumed. The real signal that both rows were actually processed as
    # non-duplicates is duplicates_skipped == 0 plus the real insert
    # payload asserted below.
    assert resp.json()["duplicates_skipped"] == 0
    # No mse_research_reports row seeded in this test -- DM drafting has
    # nothing to ground copy in, so it must not be queued.
    assert resp.json()["dm_sequences_queued"] is False

    inserts = [c for c in fake_db.executed if c.table_name == "mse_linkedin_leads" and c.calls[0][0] == "insert"]
    assert len(inserts) == 1
    payload = inserts[0]._payload
    assert len(payload) == 2
    assert payload[0]["product_id"] == "prod-1"


def test_linkedin_leads_queues_dm_drafting_when_research_report_exists(fake_db, monkeypatch):
    import agents.marketing.mkt_li_intake as li_intake_module

    fake_db.responses["opportunity_pipeline"] = [{"vertical": "Real Estate / Property Management"}]
    # Non-empty so the fake's insert-execute() (which replays this same
    # canned list rather than echoing the real payload, see the happy-path
    # test's comment) reports added > 0 -- an unrelated URL so the dedup
    # SELECT reading this same list doesn't flag the real submitted lead
    # as a duplicate.
    fake_db.responses["mse_linkedin_leads"] = [{"linkedin_url": "https://linkedin.com/in/unrelated-existing-lead"}]
    fake_db.responses["mse_research_reports"] = [{"report_json": {"pain_language": []}}]
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)
    monkeypatch.setattr(li_intake_module, "get_supabase", lambda: fake_db)

    captured = {}
    monkeypatch.setattr(
        product_marketing_router, "_run_dm_sequences_for_leads",
        lambda product_id, research_report: captured.update(product_id=product_id, research_report=research_report),
    )

    csv_text = CSV_HEADER + "Jane,Doe,Team Lead,Acme Realty,https://linkedin.com/in/janedoe,Phoenix AZ\n"

    resp = client.post(
        "/products/prod-1/linkedin-leads",
        json={"csv_text": csv_text, "source": "linkedin_manual"},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    assert resp.json()["dm_sequences_queued"] is True
    assert captured == {"product_id": "prod-1", "research_report": {"pain_language": []}}


def test_linkedin_leads_dedupes_against_existing_rows(fake_db, monkeypatch):
    import agents.marketing.mkt_li_intake as li_intake_module

    fake_db.responses["opportunity_pipeline"] = [{"vertical": "Real Estate / Property Management"}]
    fake_db.responses["mse_linkedin_leads"] = [{"linkedin_url": "https://linkedin.com/in/janedoe"}]
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)
    monkeypatch.setattr(li_intake_module, "get_supabase", lambda: fake_db)

    csv_text = CSV_HEADER + "Jane,Doe,Team Lead,Acme Realty,https://linkedin.com/in/janedoe,Phoenix AZ\n"

    resp = client.post(
        "/products/prod-1/linkedin-leads",
        json={"csv_text": csv_text, "source": "linkedin_manual"},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    assert resp.json()["added"] == 0
    assert resp.json()["duplicates_skipped"] == 1
    assert resp.json()["dm_sequences_queued"] is False
