"""Tests for Phase 6a's scaffold generator. Beyond checking files exist,
these actually import and boot the generated FastAPI app in a subprocess-
free way (via importlib against a temp sys.path) to catch the class of bug
that pure file-existence checks would miss — e.g. a templated file with a
syntax error, or a copied file whose import path doesn't resolve from the
new location.
"""
import importlib
import json
import sys
import time
from pathlib import Path

import jwt
import pytest

from agents.factory.scaffold_generator import generate_scaffold, _slugify


READY_OPPORTUNITY = {
    "id": "opp-1",
    "status": "READY_TO_BUILD",
    "solution_concept": "Freight Audit Copilot",
    "vertical": "Logistics / Freight",
    "tier_structure": {"starter": 29, "growth": 59, "scale": 99},
    "mcp_integration_surface": "Reads invoice discrepancies, writes dispute drafts.",
}


def test_refuses_non_ready_opportunity(fake_db, tmp_path):
    fake_db.responses["opportunity_pipeline"] = [{**READY_OPPORTUNITY, "status": "validated"}]
    with pytest.raises(ValueError, match="not READY_TO_BUILD"):
        generate_scaffold("opp-1", tmp_path, supabase_client=fake_db)


def test_refuses_missing_opportunity(fake_db, tmp_path):
    fake_db.responses["opportunity_pipeline"] = []
    with pytest.raises(ValueError, match="No opportunity_pipeline row"):
        generate_scaffold("opp-1", tmp_path, supabase_client=fake_db)


def test_refuses_overwriting_existing_output(fake_db, tmp_path):
    fake_db.responses["opportunity_pipeline"] = [READY_OPPORTUNITY]
    (tmp_path / "freight-audit-copilot").mkdir()
    with pytest.raises(FileExistsError):
        generate_scaffold("opp-1", tmp_path, supabase_client=fake_db)


def test_generates_expected_file_tree(fake_db, tmp_path):
    fake_db.responses["opportunity_pipeline"] = [READY_OPPORTUNITY]
    out = generate_scaffold("opp-1", tmp_path, supabase_client=fake_db)

    assert out == tmp_path / "freight-audit-copilot"
    expected = [
        "requirements.txt", "railpack.json", "runtime.txt", "README.md", ".env.example",
        "api/main.py", "api/middleware/auth.py", "api/middleware/tenant_context.py",
        "api/routers/events.py", "api/routers/milestones.py", "api/routers/digest.py",
        "api/routers/reengagement.py", "api/routers/stripe.py", "api/routers/mcp.py",
        "core/supabase_client.py", "core/sanitization.py", "core/llm_router.py",
        "core/retention/digest_generator.py", "core/retention/milestone_detector.py",
        "core/retention/reengagement_trigger.py",
        "supabase/migrations/001_core_schema.sql",
        "frontend/package.json", "frontend/.nvmrc", "frontend/middleware.ts",
        "frontend/app/login/page.tsx", "frontend/app/layout.tsx",
        "frontend/components/UsageTracker.tsx",
        "frontend/tsconfig.json", "frontend/next.config.ts", "frontend/postcss.config.js",
        "frontend/tailwind.config.ts", "frontend/app/auth/callback/route.ts", "frontend/app/page.tsx",
    ]
    for rel in expected:
        assert (out / rel).exists(), f"missing {rel}"


def test_tailwind_config_uses_global_tokens_not_mse_dashboard_palette(fake_db, tmp_path):
    """MSE's own frontend/tailwind.config.ts uses Inter, which the global
    CLAUDE.md explicitly bans as a font for shipped products (MSE's
    dashboard is an internal tool, exempt; shipped products are not)."""
    fake_db.responses["opportunity_pipeline"] = [READY_OPPORTUNITY]
    out = generate_scaffold("opp-1", tmp_path, supabase_client=fake_db)

    config = (out / "frontend/tailwind.config.ts").read_text()
    assert "Inter" not in config
    assert "Space Grotesk" in config
    assert "IBM Plex Sans" in config
    assert "JetBrains Mono" in config


def test_copied_files_match_source_exactly(fake_db, tmp_path):
    """The whole point of copying rather than regenerating these files is
    that they're already proven — a copy that silently diverges defeats
    that entirely."""
    fake_db.responses["opportunity_pipeline"] = [READY_OPPORTUNITY]
    out = generate_scaffold("opp-1", tmp_path, supabase_client=fake_db)

    repo_root = Path(__file__).resolve().parent.parent
    assert (out / "core/sanitization.py").read_text() == (repo_root / "core/sanitization.py").read_text()
    assert (out / "api/routers/stripe.py").read_text() == (repo_root / "api/routers/stripe.py").read_text()


def test_generated_backend_actually_boots(fake_db, tmp_path):
    """Real smoke test, not just file-existence: install the generated
    repo's own module tree on sys.path and boot its FastAPI app for real,
    the same way test_stripe_webhook.py etc. boot this repo's app."""
    fake_db.responses["opportunity_pipeline"] = [READY_OPPORTUNITY]
    out = generate_scaffold("opp-1", tmp_path, supabase_client=fake_db)

    sys.path.insert(0, str(out))
    try:
        for mod_name in list(sys.modules):
            if mod_name == "api" or mod_name.startswith("api.") or mod_name == "core" or mod_name.startswith("core."):
                del sys.modules[mod_name]

        main = importlib.import_module("api.main")
        from fastapi.testclient import TestClient
        client = TestClient(main.app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # /mcp/manifest requires auth in the generated scaffold, matching
        # kdavis-microsaas-engine's own live PUBLIC_PATHS (which also
        # doesn't exempt it) — not asserting it should be public here, just
        # that it behaves consistently with the proven production repo.
        token = jwt.encode(
            {"sub": "tenant-1", "aud": "authenticated", "exp": int(time.time()) + 3600},
            "placeholder-jwt-secret",
            algorithm="HS256",
        )
        resp = client.get("/mcp/manifest", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "freight-audit-copilot"
    finally:
        sys.path.remove(str(out))
        for mod_name in list(sys.modules):
            if mod_name == "api" or mod_name.startswith("api.") or mod_name == "core" or mod_name.startswith("core."):
                del sys.modules[mod_name]
        importlib.import_module("api.main")  # restore this repo's own api.main for later tests


def test_migration_sql_has_rls_on_every_table(fake_db, tmp_path):
    fake_db.responses["opportunity_pipeline"] = [READY_OPPORTUNITY]
    out = generate_scaffold("opp-1", tmp_path, supabase_client=fake_db)

    sql = (out / "supabase/migrations/001_core_schema.sql").read_text()
    tables = ["tenants", "usage_events", "milestones", "retention_sequences", "weekly_digest_log"]
    for table in tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql


def test_frontend_package_json_is_valid_and_pins_node_22(fake_db, tmp_path):
    fake_db.responses["opportunity_pipeline"] = [READY_OPPORTUNITY]
    out = generate_scaffold("opp-1", tmp_path, supabase_client=fake_db)

    pkg = json.loads((out / "frontend/package.json").read_text())
    assert pkg["engines"]["node"] == "22.x"
    assert pkg["name"] == "freight-audit-copilot-frontend"


@pytest.mark.parametrize("name,expected", [
    ("Freight Audit Copilot", "freight-audit-copilot"),
    ("  Weird!! Name///2000  ", "weird-name-2000"),
    ("", "product"),
])
def test_slugify(name, expected):
    assert _slugify(name) == expected


def test_slugify_caps_length_at_a_word_boundary():
    # solution_concept is a full descriptive sentence in real data, not a
    # short name — an uncapped slug crashed a real run with "File name too
    # long" on the git branch ref. Regression coverage for the cap.
    long_name = (
        "A contractor payment compliance tool that automatically collects "
        "W-9s via a branded portal, tracks cumulative payments per contractor"
    )
    slug = _slugify(long_name)
    assert len(slug) <= 60
    assert not slug.endswith("-")
