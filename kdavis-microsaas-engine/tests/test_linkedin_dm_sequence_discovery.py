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


# ── Item 1 (2026-10-01): silent-zero-enrollment guard ────────────────────

class TestZeroEnrollmentMustBeExplained:
    """The exact failure that kept the daily 8am engine inert for weeks:
    pending_dm leads existed, zero sequences were written, and nothing
    anywhere said why. A zero-write run is only acceptable if every
    pending lead is accounted for with a named skip reason."""

    def _run(self, leads, monkeypatch, caplog):
        import agents.marketing.mkt_o2_cold_dm_writer as o2
        db = FakeSupabase(responses={
            "mse_leads": leads,
            "mse_linkedin_leads": [],
            "mse_icp_configs": [{"product_id": "p1", "selling_stage": "active"}],
            "mse_dm_sequences": [{"id": "s1"}],
            "audit_log": [], "usage_events": [],
        })
        # no drafting work: force every source query to yield nothing
        monkeypatch.setattr(o2, "run_o2_cold_dm_writer",
                            lambda **kw: {"status": "ready_for_hitl", "sequences_written": 0})
        with caplog.at_level("WARNING"):
            res = o2.run_o2_for_linkedin_leads(product_id="p1", research_report={}, supabase_client=db)
        return res, caplog.text

    def test_zero_written_with_pending_leads_logs_a_reason(self, monkeypatch, caplog):
        leads = [{"id": "l1", "source": "lead_finder", "email_status": "unverified"}]
        res, text = self._run(leads, monkeypatch, caplog)

        assert res["sequences_written"] == 0
        assert res["pending_dm_total"] == 1
        assert res["skip_reasons"], "a zero-write run with pending leads must name a skip reason"
        assert "wrote 0 sequences" in text, "zero-write with pending leads must log a WARNING"

    def test_unverified_lead_finder_lead_is_attributed_not_silent(self, monkeypatch, caplog):
        leads = [{"id": "l1", "source": "lead_finder", "email_status": "unverified"}]
        res, _ = self._run(leads, monkeypatch, caplog)
        assert res["skip_reasons"].get("lead_finder_email_not_verified") == 1

    def test_no_pending_leads_is_not_a_warning(self, monkeypatch, caplog):
        res, text = self._run([], monkeypatch, caplog)
        assert res["pending_dm_total"] == 0
        assert "wrote 0 sequences" not in text

    def test_every_pending_lead_is_accounted_for(self, monkeypatch, caplog):
        """written + skipped must equal the pending pool -- any shortfall
        is reported as UNEXPLAINED rather than silently dropped."""
        leads = [
            {"id": "l1", "source": "lead_finder", "email_status": "unverified"},
            {"id": "l2", "source": "job_posting_signal", "email_status": "unverified"},
        ]
        res, _ = self._run(leads, monkeypatch, caplog)
        accounted = res["sequences_written"] + sum(res["skip_reasons"].values())
        assert accounted == res["pending_dm_total"] == 2
