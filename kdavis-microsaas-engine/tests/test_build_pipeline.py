from pathlib import Path

import pytest

import agents.factory.build_pipeline as pipeline


def _patch_happy_path(monkeypatch, tmp_path):
    repo_path = tmp_path / "freight-audit-copilot"
    repo_path.mkdir()
    (repo_path / "supabase" / "migrations").mkdir(parents=True)
    (repo_path / "supabase" / "migrations" / "001_core_schema.sql").write_text("CREATE TABLE tenants();")

    monkeypatch.setattr(pipeline, "generate_scaffold", lambda *a, **k: repo_path)
    monkeypatch.setattr(pipeline, "provision_supabase_project", lambda *a, **k: {
        "project_ref": "new-ref", "url": "https://new-ref.supabase.co",
        "anon_key": "anon-x", "service_role_key": "service-x", "db_password": "pw",
    })
    monkeypatch.setattr(pipeline, "deploy_product", lambda *a, **k: {
        "backend": {"url": "freight-api.up.railway.app", "service": "freight-audit-copilot-api"},
        "frontend": {"url": "freight.vercel.app", "domain": "freight-audit-copilot.thdstack.com"},
    })
    monkeypatch.setattr(pipeline, "provision_stripe", lambda *a, **k: {
        "tiers": {"starter": {"product_id": "prod_1", "price_id": "price_1"}},
        "webhook_id": "we_1", "webhook_secret": "whsec_generated",
    })
    monkeypatch.setattr(pipeline, "set_railway_env_vars", lambda *a, **k: None)
    return repo_path


def test_refuses_without_triggered_by(fake_db, tmp_path):
    with pytest.raises(ValueError, match="triggered_by is required"):
        pipeline.run_build_pipeline(
            "opp-1", tmp_path, "sk_test_x", "", supabase_client=fake_db, org_id="org-1",
        )


def test_happy_path_transitions_status_and_returns_full_result(monkeypatch, fake_db, tmp_path):
    _patch_happy_path(monkeypatch, tmp_path)
    fake_db.responses["opportunity_pipeline"] = [{"solution_concept": "Freight Audit Copilot", "tier_structure": {"starter": 29}}]

    result = pipeline.run_build_pipeline(
        "opp-1", tmp_path, "sk_test_x", "kelvin", supabase_client=fake_db, org_id="org-1",
    )

    assert result["product_slug"] == "freight-audit-copilot"
    assert result["supabase"]["project_ref"] == "new-ref"
    assert result["stripe"]["webhook_secret"] == "whsec_generated"
    assert result["deploy"]["frontend"]["domain"] == "freight-audit-copilot.thdstack.com"

    status_updates = [
        c._payload["status"] for c in fake_db.executed
        if c.table_name == "opportunity_pipeline" and c._payload and "status" in c._payload
    ]
    assert status_updates == ["building", "launched"]

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "win"
    assert audits[-1]._payload["metadata"]["triggered_by"] == "kelvin"


def test_failure_reverts_status_to_ready_to_build(monkeypatch, fake_db, tmp_path):
    _patch_happy_path(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "provision_supabase_project", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("free plan project limit reached")))
    fake_db.responses["opportunity_pipeline"] = [{"solution_concept": "X", "tier_structure": {}}]

    with pytest.raises(RuntimeError, match="Build pipeline failed"):
        pipeline.run_build_pipeline(
            "opp-1", tmp_path, "sk_test_x", "kelvin", supabase_client=fake_db, org_id="org-1",
        )

    status_updates = [
        c._payload["status"] for c in fake_db.executed
        if c.table_name == "opportunity_pipeline" and c._payload and "status" in c._payload
    ]
    assert status_updates == ["building", "READY_TO_BUILD"]

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "lose"
    assert "project limit" in audits[-1]._payload["metadata"]["error"]


def test_stripe_provisioning_happens_after_deploy_not_before(monkeypatch, fake_db, tmp_path):
    """The webhook needs a real live backend URL to register against —
    provisioning Stripe before deploy would register a dead URL."""
    _patch_happy_path(monkeypatch, tmp_path)
    call_order = []
    monkeypatch.setattr(pipeline, "deploy_product", lambda *a, **k: (call_order.append("deploy"), {
        "backend": {"url": "freight-api.up.railway.app", "service": "freight-audit-copilot-api"},
        "frontend": {"url": "freight.vercel.app", "domain": "freight-audit-copilot.thdstack.com"},
    })[1])
    monkeypatch.setattr(pipeline, "provision_stripe", lambda *a, **k: (call_order.append("stripe"), {
        "tiers": {}, "webhook_id": "we_1", "webhook_secret": "whsec_x",
    })[1])
    fake_db.responses["opportunity_pipeline"] = [{"solution_concept": "X", "tier_structure": {}}]

    pipeline.run_build_pipeline("opp-1", tmp_path, "sk_test_x", "kelvin", supabase_client=fake_db, org_id="org-1")

    assert call_order == ["deploy", "stripe"]
