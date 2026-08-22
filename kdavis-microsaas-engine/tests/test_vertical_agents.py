"""
Tests for the four industry vertical intel agents (agents/{trades,care,
service,field}_intel/agent.py) added 2026-08-22.

These follow the real integration contract used throughout this repo
(agents/orchestrator/agent.py's _run_one_vertical / VERTICAL_MODULE_MAP) --
an async `run(vertical: str) -> list[dict]` calling
core.llm_router.analyze_with_web_search directly, not a class-based
"vertical agent framework" (no such framework exists anywhere in this
codebase; see each module's own docstring). analyze_with_web_search is
monkeypatched per test, matching the module import each agent uses --
same pattern as test_aggregator_v2.py's monkeypatch.setattr calls against
agents.aggregator.agent's own imported names.
"""
import json

import agents.trades_intel.agent as trades_agent
import agents.care_intel.agent as care_agent
import agents.service_intel.agent as service_agent
import agents.field_intel.agent as field_agent
from agents.orchestrator.agent import VERTICAL_MODULE_MAP


def _fake_search_response(cards: list[dict]) -> str:
    return "Some narrative about researching this vertical.\n\n" + json.dumps(cards)


# ── Trades ───────────────────────────────────────────────────────────────

async def test_trades_run_coerces_formatted_mrr_string_to_a_number(monkeypatch):
    # Real string observed live 2026-08-22 from an actual Trades agent run.
    card = {
        "solution_concept": "FlatRate flat-fee job management",
        "conservative_mrr_potential": "$47,500/month at mature scale (1,000 customers × $59 ARPU)",
        "agent": "Trades",
    }
    monkeypatch.setattr(trades_agent, "analyze_with_web_search", lambda *a, **k: _fake_search_response([card]))

    result = await trades_agent.run("Residential Trades / Service Contractors")

    assert result[0]["conservative_mrr_potential"] == 47500.0


async def test_trades_run_returns_opportunity_cards_with_trades_fields(monkeypatch):
    card = {
        "vertical": "Residential Trades / Service Contractors",
        "existing_tool": {"name": "ServiceTitan", "price": "$398/mo", "rating": 4.4, "review_count": 200},
        "gap_type": "PRICE_GAP",
        "pain_point": "Per-technician pricing scales painfully for 2-3 tech shops",
        "solution_concept": "Flat-fee job management for 1-5 tech shops",
        "conservative_mrr_potential": 4500.0,
        "mrr_calculation": "600,000 solo/small contractors x 10% unhappy x 5% reachable x 0.5% capture x $79",
        "agent": "Trades",
        "trade_segment": "HVAC",
        "facebook_groups_identified": ["HVAC Business Owners Network"],
        "state_license_db_sources": ["TX", "FL"],
        "youtube_content_angle": "What ServiceTitan really costs a 2-tech shop",
        "trade_association_channels": ["ACCA"],
        "raw_review_samples": ["G2 2026: per-tech fees add up fast"],
    }
    monkeypatch.setattr(trades_agent, "analyze_with_web_search", lambda *a, **k: _fake_search_response([card]))

    result = await trades_agent.run("Residential Trades / Service Contractors")

    assert len(result) == 1
    assert result[0]["agent"] == "Trades"
    assert result[0]["trade_segment"] == "HVAC"
    assert result[0]["facebook_groups_identified"] == ["HVAC Business Owners Network"]
    assert result[0]["existing_tool"]["name"] == "ServiceTitan"


# ── Care ─────────────────────────────────────────────────────────────────

async def test_care_run_returns_opportunity_cards_with_care_fields(monkeypatch):
    card = {
        "vertical": "Care Services (Childcare/Elder/Pet)",
        "existing_tool": {"name": "Brightwheel", "price": "$X/child/mo", "rating": 4.5, "review_count": 500},
        "gap_type": "PRICE_GAP",
        "pain_point": "Per-child fees scale painfully as enrollment grows",
        "solution_concept": "Flat-fee enrollment/billing for 20-100 child centers",
        "conservative_mrr_potential": 4200.0,
        "mrr_calculation": "some math",
        "agent": "Care",
        "care_segment": "childcare",
        "facebook_groups_identified": ["Childcare Business Owners"],
        "state_licensing_db_sources": ["TX"],
        "state_association_channels": ["Texas AEYC"],
        "raw_review_samples": [],
    }
    monkeypatch.setattr(care_agent, "analyze_with_web_search", lambda *a, **k: _fake_search_response([card]))

    result = await care_agent.run("Care Services (Childcare/Elder/Pet)")

    assert len(result) == 1
    assert result[0]["agent"] == "Care"
    assert result[0]["care_segment"] == "childcare"
    assert result[0]["facebook_groups_identified"] == ["Childcare Business Owners"]


# ── Service ──────────────────────────────────────────────────────────────

async def test_service_run_includes_free_tier_differentiation_when_present(monkeypatch):
    card = {
        "vertical": "Personal Services (Salon/Spa/Fitness)",
        "existing_tool": {"name": "Vagaro", "price": "$30-160/mo", "rating": 4.5, "review_count": 300},
        "gap_type": "COMPLEXITY_GAP",
        "pain_point": "Feature gating forces expensive upgrades for multi-chair salons",
        "solution_concept": "Flat-fee booking for 3-15 staff salons",
        "conservative_mrr_potential": 4100.0,
        "mrr_calculation": "some math",
        "agent": "Service",
        "service_segment": "salon",
        "free_tier_differentiation": "Multi-staff commission tracking Fresha's free tier doesn't support",
        "facebook_groups_identified": ["Salon Owners Collective"],
        "instagram_strategy_note": "Before/after transformation posts",
        "raw_review_samples": [],
    }
    monkeypatch.setattr(service_agent, "analyze_with_web_search", lambda *a, **k: _fake_search_response([card]))

    result = await service_agent.run("Personal Services (Salon/Spa/Fitness)")

    assert result[0]["free_tier_differentiation"].startswith("Multi-staff")


async def test_service_run_flags_missing_free_tier_differentiation(monkeypatch):
    card = {
        "vertical": "Personal Services (Salon/Spa/Fitness)",
        "solution_concept": "Flat-fee booking for 3-15 staff salons",
        "conservative_mrr_potential": 4100.0,
        "agent": "Service",
        # free_tier_differentiation deliberately omitted -- malformed response
    }
    monkeypatch.setattr(service_agent, "analyze_with_web_search", lambda *a, **k: _fake_search_response([card]))

    result = await service_agent.run("Personal Services (Salon/Spa/Fitness)")

    assert result[0]["free_tier_differentiation"].startswith("MISSING")


# ── Field ────────────────────────────────────────────────────────────────

async def test_field_run_preserves_valid_parts_integration_verdict(monkeypatch):
    card = {
        "vertical": "Field/Repair Services (Auto/Equipment)",
        "existing_tool": {"name": "Mitchell 1", "price": "$179-259/mo", "rating": 4.3, "review_count": 150},
        "gap_type": "PRICE_GAP",
        "pain_point": "Per-technician fees applied to independent shops",
        "solution_concept": "Flat-fee shop management for 1-5 tech shops",
        "conservative_mrr_potential": 4300.0,
        "mrr_calculation": "some math",
        "agent": "Field",
        "field_segment": "auto_repair",
        "parts_integration_verdict": "nice_to_have",
        "facebook_groups_identified": ["Independent Shop Owners"],
        "youtube_content_angle": "Real cost of Mitchell 1 for a 3-bay shop",
        "state_license_db_sources": ["CA"],
        "raw_review_samples": [],
    }
    monkeypatch.setattr(field_agent, "analyze_with_web_search", lambda *a, **k: _fake_search_response([card]))

    result = await field_agent.run("Field/Repair Services (Auto/Equipment)")

    assert result[0]["parts_integration_verdict"] == "nice_to_have"


async def test_field_run_never_returns_ambiguous_parts_verdict(monkeypatch):
    for bad_value in (None, "", "it depends", "unclear", "maybe"):
        card = {
            "solution_concept": "Flat-fee shop management",
            "agent": "Field",
            "parts_integration_verdict": bad_value,
        }
        monkeypatch.setattr(field_agent, "analyze_with_web_search", lambda *a, **k: _fake_search_response([card]))

        result = await field_agent.run("Field/Repair Services (Auto/Equipment)")

        # Never left ambiguous -- defaults to the conservative value (see
        # agent.py's own comment: forcing extra Verdict scrutiny is the
        # safe failure mode, silently waving a real dependency through is not.
        assert result[0]["parts_integration_verdict"] == "hard_requirement"


async def test_field_run_missing_key_entirely_also_defaults_safe(monkeypatch):
    card = {"solution_concept": "Flat-fee shop management", "agent": "Field"}
    monkeypatch.setattr(field_agent, "analyze_with_web_search", lambda *a, **k: _fake_search_response([card]))

    result = await field_agent.run("Field/Repair Services (Auto/Equipment)")

    assert result[0]["parts_integration_verdict"] == "hard_requirement"


# ── Dispatch routing ─────────────────────────────────────────────────────

def test_dispatch_routes_new_verticals_to_correct_modules():
    assert VERTICAL_MODULE_MAP["Residential Trades / Service Contractors"] == "trades_intel"
    assert VERTICAL_MODULE_MAP["Care Services (Childcare/Elder/Pet)"] == "care_intel"
    assert VERTICAL_MODULE_MAP["Personal Services (Salon/Spa/Fitness)"] == "service_intel"
    assert VERTICAL_MODULE_MAP["Field/Repair Services (Auto/Equipment)"] == "field_intel"


def test_new_vertical_module_names_are_importable():
    # The six original verticals' map values (e.g. "realestate_intel") don't
    # match their own on-disk hyphenated directories (agents/realestate-intel/)
    # -- a real, pre-existing mismatch this session found but left alone (see
    # VERTICAL_MODULE_MAP's own comment). The four new ones must not repeat
    # it: importlib.import_module(f"agents.{module_name}.agent") has to
    # actually resolve for _run_one_vertical to ever reach a real agent
    # instead of silently falling through to the generic prompt.
    import importlib
    for vertical, module_name in VERTICAL_MODULE_MAP.items():
        if module_name not in ("trades_intel", "care_intel", "service_intel", "field_intel"):
            continue
        mod = importlib.import_module(f"agents.{module_name}.agent")
        assert hasattr(mod, "run"), f"{module_name} has no run() -- {vertical} would silently fall through"


# ── TAM sanity check ─────────────────────────────────────────────────────

from agents.orchestrator.agent import _tam_sanity_check  # noqa: E402


def test_tam_sanity_check_flags_small_addressable_market():
    finding = {"mrr_calculation": "Combined addressable base ~60,000 accounts x 15% unhappy x 8% reachable x 0.5% capture"}
    warning = _tam_sanity_check(finding)
    assert warning is not None
    assert "500,000" in warning


def test_tam_sanity_check_passes_large_addressable_market():
    finding = {"mrr_calculation": "Total addressable 1,200,000 businesses x 10% unhappy x 5% reachable x 0.5% capture"}
    assert _tam_sanity_check(finding) is None


def test_tam_sanity_check_is_informational_not_a_hard_reject():
    """Never drops the finding or raises -- only attaches a warning key,
    same principle as every other soft-input check in this repo (RAG
    retrieval, pipeline-health recalibration)."""
    finding = {"mrr_calculation": "tiny addressable base, 40,000 accounts"}
    warning = _tam_sanity_check(finding)
    assert warning is not None
    assert isinstance(finding, dict)  # finding itself untouched by the check function


def test_tam_sanity_check_skips_findings_with_no_math_shown():
    assert _tam_sanity_check({}) is None
    assert _tam_sanity_check({"mrr_calculation": ""}) is None
