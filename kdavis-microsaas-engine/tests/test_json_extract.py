"""
core/json_extract.py's extract_trailing_json_array — originally lived in
agents/orchestrator/agent.py as _extract_trailing_json_array, added
2026-07-19 when Dispatch's idea-generation call (_run_one_vertical)
switched from plain analyze() to analyze_with_web_search() after two real
v5.0 batches where 4/4 rejections traced back to Dispatch claiming a
FEATURE_GAP the anchor tool had already shipped -- Verdict's own live
search caught it every time, but only after burning a full paid Verdict
cycle on a dead-on-arrival idea. Giving Dispatch the same tool means its
responses now narrate research before the final JSON array, the same way
Verdict's do -- this file covers the trailing-array extraction that change
requires (mirrors agents/aggregator/agent.py's own _extract_trailing_json
for objects).

Moved to core/json_extract.py 2026-08-22 (this test file renamed to match)
so the four new vertical intel agents (agents/{trades,care,service,field}_
intel/agent.py) can reuse it without reaching into the orchestrator module
or duplicating the scanner.
"""
import json

from core.json_extract import coerce_mrr_number, extract_trailing_json_array


def test_extracts_bare_array_with_no_narrative():
    text = json.dumps([{"solution_concept": "Thing"}])
    result = extract_trailing_json_array(text)
    assert result == [{"solution_concept": "Thing"}]


def test_extracts_trailing_array_after_web_search_narrative():
    text = (
        "I'll check Wave's current help docs and release notes before finalizing.\n\n"
        "Confirmed: no native feature for this. Proceeding with the submission.\n\n"
        + json.dumps([{"solution_concept": "Wave invoice reminder add-on", "existing_tool": {"name": "Wave Accounting"}}])
    )
    result = extract_trailing_json_array(text)
    assert result[0]["solution_concept"] == "Wave invoice reminder add-on"


def test_nested_arrays_inside_opportunity_cards_do_not_break_extraction():
    # source_evidence and milestone_sequence are themselves JSON arrays
    # nested inside each opportunity card -- depth tracking must treat
    # their closing brackets as nested, not the outer array's own close.
    text = "Some narrative.\n\n" + json.dumps([
        {
            "solution_concept": "Thing One",
            "source_evidence": ["url-a", "url-b"],
            "retention_hooks": {"milestone_sequence": ["m1", "m2", "m3"]},
        },
        {
            "solution_concept": "Thing Two",
            "source_evidence": ["url-c"],
        },
    ])
    result = extract_trailing_json_array(text)
    assert len(result) == 2
    assert result[0]["solution_concept"] == "Thing One"
    assert result[1]["solution_concept"] == "Thing Two"


def test_returns_empty_list_when_nothing_parses():
    result = extract_trailing_json_array("Just narrative, no array anywhere.")
    assert result == []


# ── coerce_mrr_number ────────────────────────────────────────────────────
# Added 2026-08-22 after a real live run of agents/trades_intel/agent.py
# returned conservative_mrr_potential as a formatted string instead of a
# bare number despite the schema -- see the function's own docstring.

def test_coerce_mrr_number_passes_through_plain_numbers():
    assert coerce_mrr_number(4500) == 4500.0
    assert coerce_mrr_number(4500.5) == 4500.5


def test_coerce_mrr_number_strips_currency_and_commas():
    assert coerce_mrr_number("$4,500") == 4500.0


def test_coerce_mrr_number_ignores_trailing_prose():
    # The exact real string observed live from a Trades agent run.
    assert coerce_mrr_number("$47,500/month at mature scale (1,000 customers × $59 ARPU)") == 47500.0


def test_coerce_mrr_number_returns_zero_for_unrecoverable_input():
    assert coerce_mrr_number("unconfirmed") == 0.0
    assert coerce_mrr_number(None) == 0.0
    assert coerce_mrr_number({}) == 0.0
