"""
Dispatch's idea-generation call (agents/orchestrator/agent.py's
_run_one_vertical) switched from plain analyze() to analyze_with_web_search()
2026-07-19, after two real v5.0 batches where 4/4 rejections traced back to
Dispatch claiming a FEATURE_GAP the anchor tool had already shipped --
Verdict's own live search caught it every time, but only after burning a
full paid Verdict cycle on a dead-on-arrival idea. Giving Dispatch the same
tool means its responses now narrate research before the final JSON array,
the same way Verdict's do -- this covers the trailing-array extraction that
change requires (mirrors agents/aggregator/agent.py's own
_extract_trailing_json for objects).
"""
import json

from agents.orchestrator.agent import _extract_trailing_json_array


def test_extracts_bare_array_with_no_narrative():
    text = json.dumps([{"solution_concept": "Thing"}])
    result = _extract_trailing_json_array(text)
    assert result == [{"solution_concept": "Thing"}]


def test_extracts_trailing_array_after_web_search_narrative():
    text = (
        "I'll check Wave's current help docs and release notes before finalizing.\n\n"
        "Confirmed: no native feature for this. Proceeding with the submission.\n\n"
        + json.dumps([{"solution_concept": "Wave invoice reminder add-on", "existing_tool": {"name": "Wave Accounting"}}])
    )
    result = _extract_trailing_json_array(text)
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
    result = _extract_trailing_json_array(text)
    assert len(result) == 2
    assert result[0]["solution_concept"] == "Thing One"
    assert result[1]["solution_concept"] == "Thing Two"


def test_returns_empty_list_when_nothing_parses():
    result = _extract_trailing_json_array("Just narrative, no array anywhere.")
    assert result == []
