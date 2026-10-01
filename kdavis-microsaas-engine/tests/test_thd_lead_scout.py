"""
agents/marketing/thd_lead_scout.py — score_lead's point system and
find_and_score_leads/run_lead_scout orchestration. Scrapers, email_finder,
and company_signals are mocked; this covers scoring + orchestration logic,
not real scraping/SMTP/HTTP behavior. Mirrors tests/test_mkt_lead_finder.py's
style.
"""
import asyncio
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

import agents.marketing.thd_lead_scout as scout
from core.email_finder import EmailResult
from scrapers.base import RawLead
from scrapers.company_signals import CompanySignals
from tests.conftest import FakeSupabase


@dataclass
class _FakeSignals:
    reachable: bool = True
    mentions_msp: bool = False
    mentions_security_cert: bool = False
    mentions_dedicated_it: bool = False
    outdated_stack_hint: str | None = None


def _fake_brave_scraper(leads):
    scraper = MagicMock()
    scraper.scrape.return_value = leads
    scraper.query_count = 1
    return scraper


# ── score_lead ────────────────────────────────────────────────────────────

def test_score_lead_baseline_no_signals_is_neutral():
    # reachable=True + mentions_msp=False is itself a real, observed signal
    # ("no MSP mentioned"), not nothing -- only an unreachable site or an
    # unrecognized industry/employee-range contributes zero.
    score, breakdown = scout.score_lead("unknown_industry", None, _FakeSignals())
    assert score == 7
    assert breakdown == {"no_msp_mentioned": 2}


def test_score_lead_target_industry_and_employee_range_add_points():
    score, breakdown = scout.score_lead("construction", "25-150", _FakeSignals())
    assert breakdown["target_industry"] == 2
    assert breakdown["employee_range_match"] == 2
    assert breakdown["no_msp_mentioned"] == 2
    assert score == 10  # clamped


def test_score_lead_msp_mentioned_is_a_strong_negative():
    score, breakdown = scout.score_lead("construction", "25-150", _FakeSignals(mentions_msp=True))
    assert breakdown["msp_mentioned"] == -3
    assert "no_msp_mentioned" not in breakdown


def test_score_lead_security_cert_and_dedicated_it_both_penalize():
    signals = _FakeSignals(mentions_security_cert=True, mentions_dedicated_it=True)
    score, breakdown = scout.score_lead("construction", "25-150", signals)
    assert breakdown["security_cert_mentioned"] == -3
    assert breakdown["dedicated_it_mentioned"] == -2
    assert score >= 1  # never below the floor


def test_score_lead_never_goes_below_one_or_above_ten():
    worst = _FakeSignals(mentions_msp=True, mentions_security_cert=True, mentions_dedicated_it=True)
    score, _ = scout.score_lead("unknown", None, worst)
    assert score == 1

    best = _FakeSignals(outdated_stack_hint="wordpress 4.2")
    score, _ = scout.score_lead("construction", "25-150", best)
    assert score == 10


def test_score_lead_unreachable_site_gets_a_small_floor_not_a_penalty():
    score, breakdown = scout.score_lead("construction", "25-150", _FakeSignals(reachable=False))
    assert breakdown["site_unreachable_no_disqualifying_signal"] == 1
    assert "no_msp_mentioned" not in breakdown  # can't claim a signal we never observed


# ── find_and_score_leads orchestration ──────────────────────────────────────

def test_find_and_score_leads_filters_below_min_signal_score():
    fake_db = FakeSupabase(responses={"thd_consulting_leads": []})
    raw = [RawLead(name="Jane Doe", company="acme.com", domain="acme.com", source="brave_search", location="construction:Dallas TX")]

    with patch.object(scout, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(scout, "TradesScraper", return_value=MagicMock(scrape=MagicMock(return_value=[]))), \
         patch.object(scout, "fetch_company_signals", return_value=CompanySignals(reachable=True, mentions_msp=True, mentions_security_cert=True)), \
         patch.object(scout, "find_email", return_value=EmailResult(email=None, pattern_used=None, verification_status="unverified", confidence_score=0.0)):
        leads = scout.find_and_score_leads(
            {"industries": ["construction"], "locations": ["Dallas TX"], "min_signal_score": 6},
            supabase_client=fake_db,
        )

    # MSP + security cert mentioned -> score well below 6 -> filtered out
    assert leads == []


def test_find_and_score_leads_keeps_qualifying_leads():
    fake_db = FakeSupabase(responses={"thd_consulting_leads": []})
    raw = [RawLead(name="Jane Doe", company="acme.com", domain="acme.com", source="brave_search", location="construction:Dallas TX")]

    with patch.object(scout, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(scout, "TradesScraper", return_value=MagicMock(scrape=MagicMock(return_value=[]))), \
         patch.object(scout, "fetch_company_signals", return_value=CompanySignals(reachable=True)), \
         patch.object(scout, "find_email", return_value=EmailResult(email=None, pattern_used=None, verification_status="unverified", confidence_score=0.0)):
        leads = scout.find_and_score_leads(
            {"industries": ["construction"], "locations": ["Dallas TX"], "min_signal_score": 6},
            supabase_client=fake_db,
        )

    assert len(leads) == 1
    assert leads[0]["domain"] == "acme.com"
    assert leads[0]["industry"] == "construction"
    assert leads[0]["signal_score"] >= 6


def test_find_and_score_leads_deduplicates_by_domain_against_existing():
    fake_db = FakeSupabase(responses={"thd_consulting_leads": [{"domain": "acme.com"}]})
    raw = [RawLead(name="Jane Doe", company="acme.com", domain="acme.com", source="brave_search", location="construction:Dallas TX")]

    with patch.object(scout, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(scout, "TradesScraper", return_value=MagicMock(scrape=MagicMock(return_value=[]))):
        leads = scout.find_and_score_leads(
            {"industries": ["construction"], "locations": ["Dallas TX"], "min_signal_score": 1},
            supabase_client=fake_db,
        )

    assert leads == []


def test_run_lead_scout_writes_qualified_leads_and_completes_run():
    """DEPRECATED PATH (2026-10-01). Kept, with the explicit
    allow_deprecated=True override, because the orchestration it covers is
    still reachable for a deliberate one-off backfill -- and if that
    override is ever used, it should still work rather than be the one
    code path nobody tests. The default-raises behaviour is covered by
    TestThdLeadScoutDeprecated below."""
    fake_db = FakeSupabase(responses={
        # Same canned-row shape doubles as both the dedup existing-lookup
        # (no "domain" key -> contributes nothing to the existing-domains
        # set) and the insert's returned data, mirroring
        # test_mkt_lead_finder.py's identical mse_leads: [{"id": ...}] trick.
        "thd_consulting_leads": [{"id": "lead-row-1"}],
        "usage_events": [],
        "thd_consulting_scrape_runs": [{"id": "run-1"}],
    })
    raw = [RawLead(name="Jane Doe", company="acme.com", domain="acme.com", source="brave_search", location="construction:Dallas TX")]

    with patch.object(scout, "BraveSearchScraper", return_value=_fake_brave_scraper(raw)), \
         patch.object(scout, "TradesScraper", return_value=MagicMock(scrape=MagicMock(return_value=[]))), \
         patch.object(scout, "fetch_company_signals", return_value=CompanySignals(reachable=True)), \
         patch.object(scout, "find_email", return_value=EmailResult(email=None, pattern_used=None, verification_status="unverified", confidence_score=0.0)):
        result = scout.run_lead_scout(
            {"industries": ["construction"], "locations": ["Dallas TX"], "min_signal_score": 1},
            supabase_client=fake_db,
            allow_deprecated=True,
        )

    assert result["status"] == "complete"
    assert result["leads_qualified"] == 1

    insert_calls = [c for c in fake_db.tables_touched if c == "thd_consulting_leads"]
    assert insert_calls  # at least one call touched the leads table


# ── Deprecation (2026-10-01) ─────────────────────────────────────────────

class TestThdLeadScoutDeprecated:
    """Kelvin, 2026-10-01: "Deprecate thd_consulting_leads formally.
    Approved." The write path must be closed LOUDLY -- a silent second
    lead-writing path is exactly the duplication this removes."""

    def test_run_lead_scout_raises_by_default(self):
        with pytest.raises(RuntimeError) as exc:
            scout.run_lead_scout({"locations": ["Dallas TX"]}, supabase_client=FakeSupabase(responses={}))
        assert "DEPRECATED" in str(exc.value)

    def test_the_error_names_the_successor_path(self):
        """A deprecation that doesn't say what to use instead just moves
        the confusion."""
        with pytest.raises(RuntimeError) as exc:
            scout.run_lead_scout({}, supabase_client=FakeSupabase(responses={}))
        message = str(exc.value)
        assert "run_scraper_v2_scout" in message
        assert "mse_leads" in message

    def test_nothing_is_written_when_it_raises(self):
        db = FakeSupabase(responses={})
        with pytest.raises(RuntimeError):
            scout.run_lead_scout({"locations": ["x"]}, supabase_client=db)
        assert "thd_consulting_leads" not in db.tables_touched
        assert "thd_consulting_scrape_runs" not in db.tables_touched

    def test_module_declares_itself_deprecated(self):
        assert scout.THD_LEAD_SCOUT_DEPRECATED is True


class TestThdConsultingRouteDeprecated:
    def test_scrape_find_returns_410_and_creates_no_run_row(self):
        """The route must 410 rather than create a run row and dispatch a
        task that now raises -- _run_scout_background used to swallow the
        exception, which would leave the row at "pending" forever with
        nothing explaining why."""
        from fastapi import HTTPException

        import api.routers.thd_consulting as router_mod

        with patch.object(router_mod, "require_marketing_api_key", return_value=None), \
             patch.object(router_mod, "get_supabase") as mock_db:
            with pytest.raises(HTTPException) as exc:
                asyncio.run(router_mod.trigger_scrape(
                    router_mod.FindLeadsRequest(locations=["Dallas TX"]), authorization="Bearer k",
                ))
        assert exc.value.status_code == 410
        assert "run_scraper_v2_scout" in exc.value.detail
        mock_db.assert_not_called(), "no DB row may be created for a deprecated route"
