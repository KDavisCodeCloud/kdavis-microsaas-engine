import pytest

import agents.factory.brief_generator as brief_generator
from agents.factory.brief_generator import _get_industry_palette, generate_build_brief
from core.naming import NAME_SYSTEM_PROMPT


def _fake_llm(system, user):
    if system == NAME_SYSTEM_PROMPT:
        return "Freight Audit Copilot"
    return f"# brief for {system[:10]}"


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, capture_output=True, text=True, cwd=None):
        self.calls.append({"cmd": cmd, "cwd": cwd})
        return FakeResult(0)


def _seed_happy_path(fake_db):
    fake_db.responses["opportunity_pipeline"] = [{
        "solution_concept": "Freight Audit Copilot",
        "vertical": "Finance / Accounting / Bookkeeping",
        "pain_point": "Manual freight invoice audits",
        "mrr_calculation": "50 customers x $49",
        "conservative_mrr_potential": 2450,
        "build_confidence_score": 82,
        "retention_hooks": ["weekly savings report"],
        "source_urls": ["https://example.com"],
        "tier_structure": {"starter": 49},
        "status": "READY_TO_BUILD",
    }]
    fake_db.responses["industry_color_map"] = [{
        "vertical": "Finance / Accounting / Bookkeeping",
        "primary_accent": "#2563eb",
        "secondary_accent": "#16a34a",
        "mood": "trusted/professional",
        "benchmark_brands": ["QuickBooks", "Xero", "Bench"],
    }]
    fake_db.responses["mse_build_briefs"] = [{"id": "brief-1", "product_slug": "freight-audit-copilot"}]


def test_refuses_without_triggered_by(fake_db, tmp_path):
    with pytest.raises(ValueError, match="triggered_by is required"):
        generate_build_brief("opp-1", "", tmp_path, supabase_client=fake_db)


def test_happy_path_writes_branch_and_inserts_brief(monkeypatch, fake_db, tmp_path):
    _seed_happy_path(fake_db)
    runner = FakeRunner()

    result = generate_build_brief(
        "opp-1", "kelvin", tmp_path, supabase_client=fake_db,
        llm_analyze=_fake_llm,
        runner=runner,
    )

    assert result == {"id": "brief-1", "product_slug": "freight-audit-copilot"}

    branch_calls = [c["cmd"] for c in runner.calls if c["cmd"][:2] == ["git", "checkout"]]
    assert ["git", "checkout", "-b", "brief/freight-audit-copilot"] in branch_calls
    assert ["git", "checkout", "main"] in branch_calls
    push_calls = [c["cmd"] for c in runner.calls if c["cmd"][:2] == ["git", "push"]]
    assert push_calls == [["git", "push", "-u", "origin", "brief/freight-audit-copilot"]]

    # Files are committed to the brief branch, then cleaned up locally so
    # they don't sit as untracked cruft in main's working tree after checkout.
    assert not (tmp_path / "BUILD_BRIEF_CLAUDE_CODE.md").exists()
    assert not (tmp_path / "BUILD_BRIEF_CLAUDE_DESIGN.md").exists()

    inserts = [c for c in fake_db.executed if c.table_name == "mse_build_briefs" and c.calls[0][0] == "insert"]
    assert inserts[0]._payload["product_slug"] == "freight-audit-copilot"
    assert inserts[0]._payload["vertical"] == "Finance / Accounting / Bookkeeping"
    assert inserts[0]._payload["repo_branch"] == "brief/freight-audit-copilot"

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "win"
    assert audits[-1]._payload["metadata"]["triggered_by"] == "kelvin"


def test_missing_opportunity_raises_and_logs_failure(fake_db, tmp_path):
    fake_db.responses["opportunity_pipeline"] = []

    with pytest.raises(RuntimeError, match="Brief generation failed"):
        generate_build_brief("opp-missing", "kelvin", tmp_path, supabase_client=fake_db)

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "lose"
    assert "not found" in audits[-1]._payload["metadata"]["error"]


def test_git_failure_is_wrapped_and_logged(monkeypatch, fake_db, tmp_path):
    _seed_happy_path(fake_db)

    def failing_runner(cmd, capture_output=True, text=True, cwd=None):
        if cmd[:2] == ["git", "checkout"] and "-b" in cmd:
            return FakeResult(1, stderr="branch already exists")
        return FakeResult(0)

    with pytest.raises(RuntimeError, match="Brief generation failed"):
        generate_build_brief(
            "opp-1", "kelvin", tmp_path, supabase_client=fake_db,
            llm_analyze=lambda system, user: "# brief",
            runner=failing_runner,
        )

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "lose"
    assert "branch already exists" in audits[-1]._payload["metadata"]["error"]


class _SequencedQuery:
    """FakeQuery.eq() doesn't actually filter in the shared fake (it always
    returns the whole responses list), so testing the real
    match-then-fallback behavior needs a query stub that returns a
    different row depending on which vertical was actually requested."""

    def __init__(self, rows_by_vertical):
        self._rows_by_vertical = rows_by_vertical
        self._requested = None

    def select(self, *a, **k):
        return self

    def eq(self, key, value):
        self._requested = value
        return self

    def maybe_single(self):
        return self

    def execute(self):
        row = self._rows_by_vertical.get(self._requested)
        return None if row is None else type("Result", (), {"data": row})()


class _SequencedDB:
    def __init__(self, rows_by_vertical):
        self._rows_by_vertical = rows_by_vertical

    def table(self, name):
        assert name == "industry_color_map"
        return _SequencedQuery(self._rows_by_vertical)


def test_palette_falls_back_to_open_when_vertical_unmatched():
    db = _SequencedDB({
        "open": {
            "vertical": "open",
            "primary_accent": "#5a96ff",
            "secondary_accent": "#f5a623",
            "mood": "neutral/adaptable",
            "benchmark_brands": [],
        },
    })

    palette = _get_industry_palette(db, "Some Unseeded Vertical Name")

    assert palette["vertical"] == "open"


def test_palette_raises_if_no_fallback_row_exists():
    db = _SequencedDB({})

    with pytest.raises(RuntimeError, match="No industry_color_map row"):
        _get_industry_palette(db, "Some Unseeded Vertical Name")


def test_push_uses_plain_git_when_no_github_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    runner = FakeRunner()

    brief_generator._push_branch(runner, "brief/x", brief_generator.Path("."))

    assert runner.calls[0]["cmd"] == ["git", "push", "-u", "origin", "brief/x"]


def test_push_uses_inline_auth_header_when_github_token_set(monkeypatch):
    # No other code path in this repo ever pushes to git from a running
    # process — Railway's deployed FastAPI has no ambient `gh`/git
    # credentials, confirmed by a real "could not read Username" failure
    # 2026-07-17. GITHUB_TOKEN must be used via an inline header, never a
    # global git config rewrite (that would leak the token into
    # .git/config on disk).
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-123")
    runner = FakeRunner()

    brief_generator._push_branch(runner, "brief/x", brief_generator.Path("."))

    cmd = runner.calls[0]["cmd"]
    assert cmd[0] == "git"
    assert "credential.helper=" in cmd
    assert any("Authorization: Basic" in c for c in cmd)
    assert cmd[-4:] == ["push", "-u", "origin", "brief/x"]
