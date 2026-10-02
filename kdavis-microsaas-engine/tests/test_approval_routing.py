"""
tests/test_approval_routing.py

HITL approval routing (found 2026-10-01 while setting up the first real send).

THE BUG: api/routers/outreach.py decided the post-approval status from a
hardcoded source allowlist, ("apollo", "lead_finder"). Scraper v2 writes
lead_source='job_posting_signal' / 'cloud_decoded_job_signal', so every v2
lead approved through HITL landed on 'approved_manual' -- a status
mkt_o5_sequence_sender NEVER polls. Approving looked like it worked, and
nothing would ever have sent.

This is the same "active but inert" class as the two bugs that kept the daily
enrollment engine silent for weeks: a gate keyed on provenance instead of on
the question that matters.
"""

import pytest

from api.routers.outreach import _approval_target_status


class FakeQ:
    def __init__(self, row):
        self.row = row

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return type("R", (), {"data": self.row})()


class FakeDb:
    def __init__(self, row=None):
        self.row = row

    def table(self, _name):
        return FakeQ(self.row)


class TestScraperV2SourcesCanSend:
    """The regression that made this file necessary."""

    @pytest.mark.parametrize("source", ["job_posting_signal", "cloud_decoded_job_signal"])
    def test_v2_source_with_a_sendable_lead_reaches_approved_hitl(self, source):
        db = FakeDb({"lead_route": "outbound_email", "email": "a@b.com", "email_grade": "valid"})
        assert _approval_target_status(db, source, "lead-1") == "approved_hitl"

    @pytest.mark.parametrize("source", ["job_posting_signal", "cloud_decoded_job_signal"])
    def test_the_old_allowlist_would_have_blocked_these(self, source):
        """Documents the exact defect: these sources are not in the legacy
        allowlist, so provenance alone would send them to approved_manual."""
        from api.routers.outreach import _EMAILABLE_LEGACY_SOURCES

        assert source not in _EMAILABLE_LEGACY_SOURCES


class TestRoutingReadsTheLead:
    def test_manual_linkedin_route_stays_manual(self):
        db = FakeDb({"lead_route": "manual_linkedin", "email": None, "email_grade": "unknown"})
        assert _approval_target_status(db, "job_posting_signal", "lead-1") == "approved_manual"

    def test_outbound_route_with_no_address_stays_manual(self):
        """Route says email, but there is nothing to send to."""
        db = FakeDb({"lead_route": "outbound_email", "email": None, "email_grade": "valid"})
        assert _approval_target_status(db, "job_posting_signal", "lead-1") == "approved_manual"

    def test_risky_grade_still_reaches_the_sender(self):
        """Decision 3b lets catch-all addresses send under a warmup sub-cap,
        so the APPROVAL must not pre-empt that -- MKT-O5 owns the cap."""
        db = FakeDb({"lead_route": "outbound_email", "email": "a@b.com", "email_grade": "risky"})
        assert _approval_target_status(db, "job_posting_signal", "lead-1") == "approved_hitl"


class TestLegacyBehaviourPreserved:
    @pytest.mark.parametrize("source", ["apollo", "lead_finder"])
    def test_legacy_email_sources_still_reach_approved_hitl(self, source):
        assert _approval_target_status(FakeDb(None), source, None) == "approved_hitl"

    def test_linkedin_source_still_lands_on_manual(self):
        assert _approval_target_status(FakeDb(None), "linkedin_engager", None) == "approved_manual"

    def test_a_pre_v2_lead_with_no_route_falls_back_to_the_source(self):
        """A row predating migration 057 has lead_route NULL. Guessing from an
        absent field would be worse than using the provenance we do have."""
        db = FakeDb({"lead_route": None, "email": "a@b.com", "email_grade": None})
        assert _approval_target_status(db, "lead_finder", "lead-1") == "approved_hitl"
        assert _approval_target_status(db, "linkedin_engager", "lead-1") == "approved_manual"

    def test_a_missing_lead_row_falls_back_to_the_source(self):
        assert _approval_target_status(FakeDb(None), "lead_finder", "lead-1") == "approved_hitl"


class TestSenderContract:
    def test_mkt_o5_only_polls_approved_hitl(self):
        """The reason approved_manual is inert. If this ever changes, the
        routing above needs revisiting."""
        import inspect

        import agents.marketing.mkt_o5_sequence_sender as o5

        src = inspect.getsource(o5.run_send_touch_1)
        assert '"approved_hitl"' in src
        assert '"approved_manual"' not in src
