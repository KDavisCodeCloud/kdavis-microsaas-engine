import httpx
import pytest

import agents.factory.brief_generator as brief_generator
from agents.factory.brief_generator import _classify_vertical, _get_industry_palette, generate_build_brief
from core.naming import NAME_SYSTEM_PROMPT


def _fake_llm(system, user):
    if system == NAME_SYSTEM_PROMPT:
        return "Freight Audit Copilot"
    return f"# brief for {system[:10]}"


def _make_github_client(fail_on: str | None = None):
    """
    Fake GitHub API via httpx.MockTransport -- covers the real call
    sequence _push_brief_branch makes (get base ref -> get base commit ->
    create 2 blobs -> create tree -> create commit -> create ref) without
    a real repo or network call. `fail_on` simulates one specific step
    returning an error (e.g. "git/blobs") to test failure handling.
    """
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if fail_on and fail_on in path:
            return httpx.Response(422, json={"message": f"simulated failure at {fail_on}"})
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-sha-123"}})
        if path.endswith("/git/commits/base-sha-123"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}})
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": f"blob-sha-{len(calls)}"})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "new-tree-sha"})
        if path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "new-commit-sha"})
        if path.endswith("/git/refs"):
            return httpx.Response(201, json={"ref": request.url.path})
        return httpx.Response(404, json={"message": f"unexpected path {path}"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return client, calls


def _seed_happy_path(fake_db, verdict_v2_output=None):
    fake_db.responses["opportunity_pipeline"] = [{
        "solution_concept": "Freight Audit Copilot",
        "vertical": "Finance / Accounting / Bookkeeping",
        "pain_point": "Manual freight invoice audits",
        "mrr_calculation": "50 customers x $49",
        "conservative_mrr_potential": 2450,
        "build_confidence_score": 82,
        "verdict_v2_output": verdict_v2_output,
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
        generate_build_brief("opp-1", "", supabase_client=fake_db)


def test_happy_path_pushes_branch_via_github_api_and_inserts_brief(monkeypatch, fake_db):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-123")
    _seed_happy_path(fake_db)
    client, calls = _make_github_client()

    result = generate_build_brief(
        "opp-1", "kelvin", supabase_client=fake_db,
        llm_analyze=_fake_llm,
        http_client=client,
    )

    assert result == {"id": "brief-1", "product_slug": "freight-audit-copilot"}

    # the real call sequence: ref -> commit -> 2 blobs -> tree -> commit -> ref
    paths = [c.url.path for c in calls]
    assert paths == [
        "/repos/KDavisCodeCloud/kdavis-microsaas-engine/git/ref/heads/main",
        "/repos/KDavisCodeCloud/kdavis-microsaas-engine/git/commits/base-sha-123",
        "/repos/KDavisCodeCloud/kdavis-microsaas-engine/git/blobs",
        "/repos/KDavisCodeCloud/kdavis-microsaas-engine/git/blobs",
        "/repos/KDavisCodeCloud/kdavis-microsaas-engine/git/trees",
        "/repos/KDavisCodeCloud/kdavis-microsaas-engine/git/commits",
        "/repos/KDavisCodeCloud/kdavis-microsaas-engine/git/refs",
    ]
    assert calls[0].headers["authorization"] == "Bearer test-token-123"

    create_ref_body = calls[-1].content.decode()
    assert "refs/heads/brief/freight-audit-copilot" in create_ref_body

    inserts = [c for c in fake_db.executed if c.table_name == "mse_build_briefs" and c.calls[0][0] == "insert"]
    assert inserts[0]._payload["product_slug"] == "freight-audit-copilot"
    assert inserts[0]._payload["vertical"] == "Finance / Accounting / Bookkeeping"
    assert inserts[0]._payload["repo_branch"] == "brief/freight-audit-copilot"

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "win"
    assert audits[-1]._payload["metadata"]["triggered_by"] == "kelvin"


def test_verdict_score_prefers_v2_confidence_score_over_legacy_column(monkeypatch, fake_db):
    # Real bug found and fixed 2026-08-04: every recent brief showed
    # "0/100" on the dashboard because this read the older, always-0
    # build_confidence_score column instead of Verdict v5.0's real score
    # inside verdict_v2_output. This locks in the fix.
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-123")
    _seed_happy_path(fake_db, verdict_v2_output={"confidence_score": 91})
    client, _ = _make_github_client()

    generate_build_brief("opp-1", "kelvin", supabase_client=fake_db, llm_analyze=_fake_llm, http_client=client)

    inserts = [c for c in fake_db.executed if c.table_name == "mse_build_briefs" and c.calls[0][0] == "insert"]
    assert inserts[0]._payload["verdict_score"] == 91


def test_verdict_score_falls_back_to_legacy_column_when_v2_output_missing(monkeypatch, fake_db):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-123")
    _seed_happy_path(fake_db, verdict_v2_output=None)
    client, _ = _make_github_client()

    generate_build_brief("opp-1", "kelvin", supabase_client=fake_db, llm_analyze=_fake_llm, http_client=client)

    inserts = [c for c in fake_db.executed if c.table_name == "mse_build_briefs" and c.calls[0][0] == "insert"]
    assert inserts[0]._payload["verdict_score"] == 82


def test_missing_opportunity_raises_and_logs_failure(fake_db):
    fake_db.responses["opportunity_pipeline"] = []

    with pytest.raises(RuntimeError, match="Brief generation failed"):
        generate_build_brief("opp-missing", "kelvin", supabase_client=fake_db)

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "lose"
    assert "not found" in audits[-1]._payload["metadata"]["error"]


def test_github_api_failure_is_wrapped_and_logged(monkeypatch, fake_db):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-123")
    _seed_happy_path(fake_db)
    client, _ = _make_github_client(fail_on="git/blobs")

    with pytest.raises(RuntimeError, match="Brief generation failed"):
        generate_build_brief(
            "opp-1", "kelvin", supabase_client=fake_db,
            llm_analyze=lambda system, user: "# brief",
            http_client=client,
        )

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "lose"
    assert "GitHub API call failed" in audits[-1]._payload["metadata"]["error"]


def test_push_brief_branch_requires_github_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    client, _ = _make_github_client()

    with pytest.raises(RuntimeError, match="GITHUB_TOKEN is not set"):
        brief_generator._push_brief_branch(client, "brief/x", {"a.md": "content"}, "commit message")


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


# Real bug found and fixed 2026-08-05: opportunity_pipeline.vertical is
# free text from the research swarm, never the exact seeded category name,
# so the exact-match lookup missed for every one of the 6 live
# opportunities checked -- the industry-specific palette had never
# actually fired once; every brief silently got the generic 'open' one.

@pytest.mark.parametrize("vertical, expected", [
    ("Real Estate — Buyer's Agents at Independent Teams", "Real Estate / Property Management"),
    ("Independent landlords and small property management companies (10-50 units)", "Real Estate / Property Management"),
    ("Shopify DTC inventory forecasting — ad-spend-aware reorder recommendations", "E-commerce / Retail Ops"),
    ("Solo therapists and small group practices (1-5 therapists)", "Healthcare / Medical Front Desk"),
    ("Mid-market B2B SaaS companies (10-50 agents) using Intercom", None),
])
def test_classify_vertical_matches_real_live_opportunity_text(vertical, expected):
    assert _classify_vertical(vertical) == expected


def test_get_industry_palette_uses_classified_vertical_when_exact_match_misses():
    db = _SequencedDB({
        "Real Estate / Property Management": {
            "vertical": "Real Estate / Property Management",
            "primary_accent": "#2563eb",
            "secondary_accent": "#16a34a",
            "mood": "trusted/local",
            "benchmark_brands": ["Zillow", "Redfin"],
        },
        "open": {
            "vertical": "open",
            "primary_accent": "#5a96ff",
            "secondary_accent": "#f5a623",
            "mood": "neutral/adaptable",
            "benchmark_brands": [],
        },
    })

    palette = _get_industry_palette(db, "Real Estate — Buyer's Agents at Independent Teams")

    assert palette["vertical"] == "Real Estate / Property Management"
