"""
agents/aggregator/pipeline_health.py — the rolling-10-submission build-rate
monitor and auto-recalibration mechanism (Kelvin's rule, 2026-08-15).
Uses tests/conftest.py's FakeSupabase directly (no filter-narrowing on the
fake, per that module's own documented convention) — these tests seed
`responses["opportunity_pipeline"]`/`responses["opportunity_pipeline_rejections"]`
with exactly the rows each scenario needs.
"""
import agents.aggregator.pipeline_health as ph
from tests.conftest import FakeSupabase


def _pipeline_row(status, **overrides):
    row = {
        "status": status,
        "vertical": "Test vertical",
        "solution_concept": "Test concept",
        "rejection_reason": overrides.pop("rejection_reason", None),
        "verdict_v2_output": {},
        "created_at": "2026-08-15T00:00:00Z",
    }
    row.update(overrides)
    return row


def _rejection_row(**original_opportunity_overrides):
    orig = {
        "vertical": "Test vertical", "solution_concept": "Test concept",
        "rejection_reason": "Hard MRR gate", "verdict_v2_output": {},
    }
    orig.update(original_opportunity_overrides)
    return {"original_opportunity": orig, "archived_at": "2026-08-15T00:00:00Z"}


# ── classify_result ──────────────────────────────────────────────

def test_classify_killed_below_floor_as_mrr_floor():
    assert ph.classify_result({"status": "killed_below_floor"}) == "MRR_FLOOR"


def test_classify_failed_at_step_1_as_pain_not_confirmed():
    row = {"status": "rejected", "verdict_v2_output": {"failed_at_step": "1"}}
    assert ph.classify_result(row) == "PAIN_NOT_CONFIRMED"


def test_classify_failed_at_step_2_as_gap_not_identified():
    row = {"status": "rejected", "verdict_v2_output": {"failed_at_step": "2"}}
    assert ph.classify_result(row) == "GAP_NOT_IDENTIFIED"


def test_classify_failed_at_step_3_as_mrr_floor():
    row = {"status": "rejected", "verdict_v2_output": {"failed_at_step": "3"}}
    assert ph.classify_result(row) == "MRR_FLOOR"


def test_classify_failed_at_step_2_5_api_capability():
    row = {"status": "rejected", "verdict_v2_output": {
        "failed_at_step": "2.5", "step_2_5_failure_reason": "API_CANNOT_PERFORM_CORE_ACTION",
    }}
    assert ph.classify_result(row) == "API_CAPABILITY"


def test_classify_failed_at_step_2_5_third_party_approval_gate():
    row = {"status": "rejected", "verdict_v2_output": {
        "failed_at_step": "2.5", "step_2_5_failure_reason": "THIRD_PARTY_APPROVAL_GATE",
    }}
    assert ph.classify_result(row) == "THIRD_PARTY_APPROVAL_GATE"


def test_classify_failed_at_step_2_5_mid_acquisition_platform():
    row = {"status": "rejected", "verdict_v2_output": {
        "failed_at_step": "2.5", "step_2_5_failure_reason": "MID_ACQUISITION_PLATFORM",
    }}
    assert ph.classify_result(row) == "MID_ACQUISITION_PLATFORM"


def test_classify_code_level_floor_demotion_as_mrr_floor():
    # No failed_at_step (Step 3 itself said it cleared) but agent.py's own
    # code-level floor re-check demoted it -- floor_cleared: False is the tell.
    row = {"status": "rejected", "verdict_v2_output": {"floor_cleared": False}}
    assert ph.classify_result(row) == "MRR_FLOOR"


def test_classify_integration_dependency_soft_reject():
    row = {"status": "rejected", "verdict_v2_output": {"integration_dependency_count": 2}}
    assert ph.classify_result(row) == "INTEGRATION_DEPENDENCY"


def test_classify_falls_back_to_other():
    row = {"status": "watch", "verdict_v2_output": {}}
    assert ph.classify_result(row) == "OTHER"


# ── get_recent_window ─────────────────────────────────────────────

def test_get_recent_window_merges_and_sorts_both_tables():
    fake_db = FakeSupabase(responses={
        "opportunity_pipeline": [
            {"status": "READY_TO_BUILD", "vertical": "V1", "solution_concept": "S1",
             "rejection_reason": None, "verdict_v2_output": {}, "created_at": "2026-08-15T10:00:00Z"},
        ],
        "opportunity_pipeline_rejections": [_rejection_row()],
    })
    window = ph.get_recent_window(fake_db, window=10)
    assert len(window) == 2
    statuses = {row["status"] for row in window}
    assert statuses == {"READY_TO_BUILD", "killed_below_floor"}


def test_get_recent_window_respects_window_size():
    fake_db = FakeSupabase(responses={
        "opportunity_pipeline": [_pipeline_row("rejected", created_at=f"2026-08-1{i}T00:00:00Z") for i in range(5)],
        "opportunity_pipeline_rejections": [],
    })
    window = ph.get_recent_window(fake_db, window=3)
    assert len(window) == 3


# ── compute_build_rate ────────────────────────────────────────────

def test_compute_build_rate_empty_window_is_not_a_trigger():
    assert ph.compute_build_rate([]) == 1.0


def test_compute_build_rate_counts_ready_and_validated_as_qualifying():
    rows = [
        {"status": "READY_TO_BUILD"}, {"status": "validated"},
        {"status": "rejected"}, {"status": "watch"}, {"status": "killed_below_floor"},
    ]
    assert ph.compute_build_rate(rows) == 2 / 5


# ── rejection_distribution ────────────────────────────────────────

def test_rejection_distribution_percentages_are_of_full_window():
    rows = [
        {"status": "killed_below_floor"},  # MRR_FLOOR
        {"status": "killed_below_floor"},  # MRR_FLOOR
        {"status": "READY_TO_BUILD"},      # excluded (qualifying)
        {"status": "rejected", "verdict_v2_output": {"failed_at_step": "1"}},  # PAIN_NOT_CONFIRMED
    ]
    dist = ph.rejection_distribution(rows)
    assert dist["MRR_FLOOR"] == 2 / 4
    assert dist["PAIN_NOT_CONFIRMED"] == 1 / 4
    assert "READY_TO_BUILD" not in dist


# ── research_pool_exhausted ────────────────────────────────────────

def test_research_pool_exhausted_when_fewer_rows_than_window():
    rows = [{"status": "rejected", "verdict_v2_output": {}}] * 3
    assert ph.research_pool_exhausted(rows, window=10) is True


def test_research_pool_exhausted_when_too_few_distinct_tools():
    rows = [
        {"status": "rejected", "verdict_v2_output": {"existing_tool": {"name": "ToolA"}}}
        for _ in range(10)
    ]
    assert ph.research_pool_exhausted(rows, window=10) is True


def test_research_pool_not_exhausted_with_enough_distinct_tools():
    rows = [
        {"status": "rejected", "verdict_v2_output": {"existing_tool": {"name": f"Tool{i}"}}}
        for i in range(10)
    ]
    assert ph.research_pool_exhausted(rows, window=10) is False


# ── check_and_recalibrate (end-to-end) ────────────────────────────

def test_check_and_recalibrate_no_trigger_when_healthy():
    fake_db = FakeSupabase(responses={
        "opportunity_pipeline": [_pipeline_row("READY_TO_BUILD") for _ in range(2)] + [_pipeline_row("rejected") for _ in range(8)],
        "opportunity_pipeline_rejections": [],
    })
    assert ph.check_and_recalibrate(fake_db, window=10) is None
    inserts = [c for c in fake_db.executed if c.table_name == "mse_pipeline_recalibrations" and c.calls[0][0] == "insert"]
    assert inserts == []


def test_check_and_recalibrate_triggers_on_research_pool_exhausted():
    fake_db = FakeSupabase(responses={
        "opportunity_pipeline": [_pipeline_row("rejected") for _ in range(3)],  # fewer than window=10
        "opportunity_pipeline_rejections": [],
    })
    result = ph.check_and_recalibrate(fake_db, window=10)
    assert result is not None
    assert result["dominant_category"] == "RESEARCH_POOL_EXHAUSTED"
    assert result["target"] == "dispatch"

    inserts = [c for c in fake_db.executed if c.table_name == "mse_pipeline_recalibrations" and c.calls[0][0] == "insert"]
    assert len(inserts) == 1
    assert "Reddit" in inserts[0]._payload["action_taken"]


def test_check_and_recalibrate_triggers_on_dominant_mrr_floor_category():
    fake_db = FakeSupabase(responses={
        "opportunity_pipeline": [
            _pipeline_row(
                "rejected", verdict_v2_output={"failed_at_step": "3", "existing_tool": {"name": f"Tool{i}"}},
                vertical=f"V{i}", solution_concept=f"S{i}",
            )
            for i in range(10)
        ],
        "opportunity_pipeline_rejections": [],
    })
    result = ph.check_and_recalibrate(fake_db, window=10)
    assert result is not None
    assert result["dominant_category"] == "MRR_FLOOR"
    assert result["target"] == "verdict"
    assert result["build_rate_pct"] == 0.0

    inserts = [c for c in fake_db.executed if c.table_name == "mse_pipeline_recalibrations" and c.calls[0][0] == "insert"]
    assert "price" in inserts[0]._payload["action_taken"].lower()


def test_check_and_recalibrate_no_action_when_no_category_dominates():
    # 0% build rate (clearly below the 10% floor, so this genuinely
    # exercises the dominance check rather than short-circuiting on the
    # outer health-floor comparison), but rejections are spread across
    # enough distinct categories that none clears the 40% dominance bar,
    # and there are 10 distinct tools so the pool isn't exhausted either.
    rows = []
    categories = [
        {"failed_at_step": "1"}, {"failed_at_step": "1"},
        {"failed_at_step": "2"}, {"failed_at_step": "2"},
        {"failed_at_step": "3"}, {"failed_at_step": "3"},
        {"integration_dependency_count": 2}, {"integration_dependency_count": 2},
        {"failed_at_step": "2.5", "step_2_5_failure_reason": "API_CANNOT_PERFORM_CORE_ACTION"},
        {"failed_at_step": "2.5", "step_2_5_failure_reason": "MID_ACQUISITION_PLATFORM"},
    ]
    for i, v2 in enumerate(categories):
        v2 = {**v2, "existing_tool": {"name": f"Tool{i}"}}
        rows.append(_pipeline_row("rejected", verdict_v2_output=v2, vertical=f"V{i}", solution_concept=f"S{i}"))

    fake_db = FakeSupabase(responses={"opportunity_pipeline": rows, "opportunity_pipeline_rejections": []})
    assert ph.check_and_recalibrate(fake_db, window=10) is None


def test_check_and_recalibrate_logs_but_takes_no_action_for_permanent_hard_stop_category():
    rows = [
        _pipeline_row(
            "rejected",
            verdict_v2_output={"failed_at_step": "2.5", "step_2_5_failure_reason": "THIRD_PARTY_APPROVAL_GATE", "existing_tool": {"name": f"Tool{i}"}},
            vertical=f"V{i}", solution_concept=f"S{i}",
        )
        for i in range(10)
    ]
    fake_db = FakeSupabase(responses={"opportunity_pipeline": rows, "opportunity_pipeline_rejections": []})

    result = ph.check_and_recalibrate(fake_db, window=10)
    assert result is not None
    assert result["dominant_category"] == "THIRD_PARTY_APPROVAL_GATE"
    assert result["target"] == "none"
    assert result["active"] is False  # never injectable -- permanent hard stop, no auto-loosening


def test_check_and_recalibrate_replaces_not_stacks_prior_active_row_for_same_target():
    fake_db = FakeSupabase(responses={
        "opportunity_pipeline": [
            _pipeline_row(
                "rejected", verdict_v2_output={"failed_at_step": "3", "existing_tool": {"name": f"Tool{i}"}},
                vertical=f"V{i}", solution_concept=f"S{i}",
            )
            for i in range(10)
        ],
        "opportunity_pipeline_rejections": [],
        "mse_pipeline_recalibrations": [{"id": "prior-row", "target": "verdict", "active": True}],
    })
    ph.check_and_recalibrate(fake_db, window=10)

    updates = [c for c in fake_db.executed if c.table_name == "mse_pipeline_recalibrations" and c.calls[0][0] == "update"]
    assert updates
    assert updates[0]._payload == {"active": False}
    assert ("target", "verdict") in updates[0]._filters
    assert ("active", True) in updates[0]._filters


# ── get_active_recalibration_text ─────────────────────────────────

def test_get_active_recalibration_text_returns_text_when_active():
    fake_db = FakeSupabase(responses={
        "mse_pipeline_recalibrations": [{"action_taken": "Do the thing."}],
    })
    assert ph.get_active_recalibration_text("verdict", fake_db) == "Do the thing."


def test_get_active_recalibration_text_empty_when_none_active():
    fake_db = FakeSupabase(responses={"mse_pipeline_recalibrations": []})
    assert ph.get_active_recalibration_text("verdict", fake_db) == ""
