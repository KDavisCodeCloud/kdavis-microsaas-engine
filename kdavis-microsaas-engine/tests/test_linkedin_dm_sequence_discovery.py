"""
tests/test_linkedin_dm_sequence_discovery.py

Regression coverage for the 2026-09-29 "active but inert" bug in
api/routers/linkedin_intake.py's _run_linkedin_dm_sequences -- the daily
enrollment engine behind n8n's "LinkedIn Outreach — Daily 8am MST".

It had been scheduled and ACTIVE for at least a week while enrolling
nothing: discovery filtered mse_leads on email_status='verified' (zero
rows qualified) and then skipped any product without an MKT-R1 research
report (no lead-bearing product had one). Either bug alone made the whole
workflow a no-op.

tests/conftest.py's FakeSupabase deliberately does NOT enforce .eq()
filters (it returns the same canned rows per table regardless), so these
tests assert on the recorded FILTER CALLS rather than on returned data --
that's the only way to prove a specific WHERE clause is or isn't being
sent to real Postgres.
"""

from unittest.mock import patch

import api.routers.linkedin_intake as li
from tests.conftest import FakeSupabase


def _filters_for(db, table_name):
    """Every (key, value) filter recorded across all queries on `table`.
    FakeQuery.eq() records into ._filters (not .calls), unlike lt/lte/gte
    which record to both."""
    pairs = []
    for q in db.executed:
        if q.table_name == table_name:
            pairs.extend(q._filters)
    return pairs


class TestDiscoveryNoLongerRequiresVerifiedEmail:
    def test_mse_leads_discovery_does_not_filter_on_email_status(self):
        """The root cause. email_status='verified' is per-source policy
        applied inside run_o2_for_linkedin_leads (correctly, to
        lead_finder only) -- it must never gate product DISCOVERY, or
        job_posting_signal / cloud_decoded_job_signal leads (which
        typically have no email at all) can never be enrolled."""
        db = FakeSupabase(responses={
            "mse_linkedin_leads": [],
            "mse_leads": [{"product_id": "prod-consulting"}],
            "mse_research_reports": [{"report_json": {"pain_language": ["x"]}}],
        })

        with patch.object(li, "get_supabase", return_value=db), \
             patch("agents.marketing.mkt_o2_cold_dm_writer.run_o2_for_linkedin_leads") as mock_o2:
            li._run_linkedin_dm_sequences()

        lead_filters = _filters_for(db, "mse_leads")
        assert ("status", "pending_dm") in lead_filters
        assert not any(k == "email_status" for k, _ in lead_filters), (
            "discovery must not filter mse_leads on email_status -- that filter "
            "belongs per-source inside run_o2_for_linkedin_leads"
        )
        mock_o2.assert_called_once()
        assert mock_o2.call_args.kwargs["product_id"] == "prod-consulting"

    def test_unverified_only_leads_still_trigger_enrollment(self):
        """Exact production shape at the time of the bug: pending_dm leads
        exist, none verified, no linkedin leads at all. Must still enroll."""
        db = FakeSupabase(responses={
            "mse_linkedin_leads": [],
            "mse_leads": [{"product_id": "p1"}],
            "mse_research_reports": [{"report_json": {}}],
        })

        with patch.object(li, "get_supabase", return_value=db), \
             patch("agents.marketing.mkt_o2_cold_dm_writer.run_o2_for_linkedin_leads") as mock_o2:
            li._run_linkedin_dm_sequences()

        mock_o2.assert_called_once()


class TestMissingResearchReportNoLongerSkips:
    def test_product_without_research_report_is_still_enrolled(self):
        """Second independent cause. job_posting_signal /
        cloud_decoded_job_signal ignore research_report entirely, so a
        missing report must not silently skip the product."""
        db = FakeSupabase(responses={
            "mse_linkedin_leads": [],
            "mse_leads": [{"product_id": "p-no-report"}],
            "mse_research_reports": [],  # none exists
        })

        with patch.object(li, "get_supabase", return_value=db), \
             patch("agents.marketing.mkt_o2_cold_dm_writer.run_o2_for_linkedin_leads") as mock_o2:
            li._run_linkedin_dm_sequences()

        mock_o2.assert_called_once()
        assert mock_o2.call_args.kwargs["research_report"] == {}

    def test_existing_research_report_is_still_passed_through(self):
        """The fix must not stop grounding copy when a report DOES exist."""
        report = {"pain_language": ["downtime"], "proof_signals": ["case study"]}
        db = FakeSupabase(responses={
            "mse_linkedin_leads": [],
            "mse_leads": [{"product_id": "p1"}],
            "mse_research_reports": [{"report_json": report}],
        })

        with patch.object(li, "get_supabase", return_value=db), \
             patch("agents.marketing.mkt_o2_cold_dm_writer.run_o2_for_linkedin_leads") as mock_o2:
            li._run_linkedin_dm_sequences()

        assert mock_o2.call_args.kwargs["research_report"] == report


class TestDiscoveryUnionUnchanged:
    def test_no_pending_leads_anywhere_enrolls_nothing(self):
        db = FakeSupabase(responses={
            "mse_linkedin_leads": [],
            "mse_leads": [],
            "mse_research_reports": [],
        })

        with patch.object(li, "get_supabase", return_value=db), \
             patch("agents.marketing.mkt_o2_cold_dm_writer.run_o2_for_linkedin_leads") as mock_o2:
            li._run_linkedin_dm_sequences()

        mock_o2.assert_not_called()

    def test_linkedin_leads_still_discovered(self):
        """mse_linkedin_leads remains half of the discovery union."""
        db = FakeSupabase(responses={
            "mse_linkedin_leads": [{"product_id": "p-li"}],
            "mse_leads": [],
            "mse_research_reports": [{"report_json": {}}],
        })

        with patch.object(li, "get_supabase", return_value=db), \
             patch("agents.marketing.mkt_o2_cold_dm_writer.run_o2_for_linkedin_leads") as mock_o2:
            li._run_linkedin_dm_sequences()

        mock_o2.assert_called_once()
        assert mock_o2.call_args.kwargs["product_id"] == "p-li"
