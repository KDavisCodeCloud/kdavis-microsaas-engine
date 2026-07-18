"""
Covers two real bugs found while wiring Verdict Agent v2.0 into
node_write_pipeline (agents/orchestrator/agent.py):

1. Rejected opportunities were silently skipped entirely (`if status not
   in (...): continue`) — the Opportunities dashboard has had a
   "rejected" filter tab since it was built, but it has always shown
   empty because nothing rejected ever got a row.
2. `"conservative_mrr_potential": max(mrr, 4000)` artificially inflated
   a below-floor MRR figure up to look like it cleared $4,000 — exactly
   the "floor inflation" failure mode Verdict v2.0 exists to eliminate.
   The floor must be enforced by rejecting, never by lying about the
   number.
"""
import agents.orchestrator.agent as orchestrator


def _state(aggregated_results, raw_findings=None):
    return {
        "session_id": "sess-1",
        "raw_findings": raw_findings or [{"solution_concept": r.get("solution_concept", "")} for r in aggregated_results],
        "aggregated_results": aggregated_results,
    }


def test_rejected_opportunities_are_now_inserted_not_skipped(monkeypatch, fake_db):
    monkeypatch.setattr(orchestrator, "get_supabase", lambda: fake_db)

    orchestrator.node_write_pipeline(_state([{
        "vertical": "E-commerce / Retail Ops",
        "solution_concept": "Rejected Thing",
        "status": "rejected",
        "rejection_reason": "Competitor exists: Trunk ($35/mo, Shopify App Store)",
        "verdict_v2_output": {"verdict": "DO_NOT_BUILD"},
    }]))

    inserts = [c for c in fake_db.executed if c.table_name == "opportunity_pipeline" and c.calls[0][0] == "insert"]
    assert len(inserts) == 1
    payload = inserts[0]._payload[0]
    assert payload["status"] == "rejected"
    assert "Trunk" in payload["rejection_reason"]


def test_no_floor_inflation_sub_4000_mrr_is_stored_as_is(monkeypatch, fake_db):
    monkeypatch.setattr(orchestrator, "get_supabase", lambda: fake_db)

    orchestrator.node_write_pipeline(_state([{
        "vertical": "Finance / Accounting / Bookkeeping",
        "solution_concept": "Below Floor Thing",
        "status": "rejected",
        "rejection_reason": "Code-level floor check failed: final_mrr_floor $2,100 is below the $4,000 floor.",
        "verdict_v2_output": {"verdict": "DO_NOT_BUILD", "final_mrr_floor": 2100},
    }]))

    inserts = [c for c in fake_db.executed if c.table_name == "opportunity_pipeline" and c.calls[0][0] == "insert"]
    payload = inserts[0]._payload[0]
    # Must reflect the real, sub-floor number — never clamped up to 4000.
    assert payload["conservative_mrr_potential"] == 2100.0


def test_ready_to_build_opportunity_stores_verdict_v2_output(monkeypatch, fake_db):
    monkeypatch.setattr(orchestrator, "get_supabase", lambda: fake_db)
    v2_output = {"verdict": "BUILD", "final_mrr_floor": 5500, "tam_source": "Shopify public filings"}

    orchestrator.node_write_pipeline(_state([{
        "vertical": "E-commerce / Retail Ops",
        "solution_concept": "Good Thing",
        "status": "READY_TO_BUILD",
        "rejection_reason": None,
        "verdict_v2_output": v2_output,
    }]))

    inserts = [c for c in fake_db.executed if c.table_name == "opportunity_pipeline" and c.calls[0][0] == "insert"]
    payload = inserts[0]._payload[0]
    assert payload["conservative_mrr_potential"] == 5500.0
    assert payload["verdict_v2_output"] == v2_output
    assert payload["status"] == "READY_TO_BUILD"


def test_v3_nested_scenarios_floor_is_read_correctly(monkeypatch, fake_db):
    # Real bug found wiring v3.0 in: v3.0 nests the MRR figure under
    # scenarios.floor.final_mrr_floor (three required scenarios replace
    # v2.0's single top-level final_mrr_floor) -- this line still read the
    # old top-level key, which is always None on a v3.0 result, silently
    # falling through to the unverified upstream vertical-agent figure on
    # every single v3.0 opportunity until this was caught and fixed.
    monkeypatch.setattr(orchestrator, "get_supabase", lambda: fake_db)
    v3_output = {
        "verdict": "BUILD",
        "tam_source": "NAR property manager count",
        "scenarios": {"floor": {"final_mrr_floor": 4700, "gate_clear": True}},
    }

    orchestrator.node_write_pipeline(_state([{
        "vertical": "Real Estate / Property Management",
        "solution_concept": "V3 Nested Thing",
        "status": "READY_TO_BUILD",
        "rejection_reason": None,
        "verdict_v2_output": v3_output,
    }], raw_findings=[{"solution_concept": "V3 Nested Thing", "conservative_mrr_potential": 99999}]))

    inserts = [c for c in fake_db.executed if c.table_name == "opportunity_pipeline" and c.calls[0][0] == "insert"]
    payload = inserts[0]._payload[0]
    # Must use the agent's real nested figure (4700), never the unrelated
    # upstream self-report (99999) that a silent fallback would have used.
    assert payload["conservative_mrr_potential"] == 4700.0
