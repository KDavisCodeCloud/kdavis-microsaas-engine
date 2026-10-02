"""
tests/test_contact_fit.py

Contact-title fit (Kelvin's decision 4, 2026-10-01).

WHY: classify_seniority answers "how senior is this person", which is a
different question from "is this person the buyer". Both contacts that
survived the 2026-10-01 runs were senior AND wrong, and nothing caught them:

  Earnin      "VP of Data"  -- VP-level, wrong function entirely.
  SingleStore "CEO"         -- right function, wrong company size (36 open
                              roles; its CEO does not take cold outreach
                              about infrastructure consulting).

Kelvin named both as must-rejects, so they lead the file.
"""

import pytest

from agents.marketing.contact_fit import (
    FOUNDER_MAX_OPEN_ROLES,
    ContactFit,
    evaluate_contact_fit,
)


class TestTheTwoNamedRejections:
    """Kelvin, 2026-10-01: "Reject Earnin (VP of Data) and SingleStore (CEO)
    under these rules.\""""

    def test_earnin_vp_of_data_is_the_wrong_function(self):
        result = evaluate_contact_fit("VP of Data", open_role_count=36)
        assert result.accepted is False
        assert "wrong function" in result.reason.lower()

    def test_singlestore_ceo_is_the_wrong_company_size(self):
        result = evaluate_contact_fit("CEO", open_role_count=36)
        assert result.accepted is False
        assert "open roles" in result.reason

    def test_the_same_ceo_title_is_fine_at_a_small_company(self):
        """The rule is about company size, not about the word "CEO": at a
        <=50-person company the founder still owns infrastructure decisions."""
        result = evaluate_contact_fit("CEO", open_role_count=4)
        assert result.accepted is True
        assert result.category == "founder_small"


class TestExecutiveTechnologyTitles:
    @pytest.mark.parametrize("title", [
        "CTO", "Chief Technology Officer", "CIO", "CISO",
        "VP of Engineering", "VP Engineering", "SVP, Infrastructure",
        "Vice President of Platform", "Head of Engineering",
        "Head of Infrastructure", "Director of Platform Engineering",
    ])
    def test_accepted_at_any_company_size(self, title):
        """A CTO is the buyer whether the company has 3 open roles or 300."""
        for roles in (2, 12, 120):
            result = evaluate_contact_fit(title, open_role_count=roles)
            assert result.accepted is True, f"{title!r} at {roles} roles"
            assert result.category == "exec_tech"


class TestFunctionalInfrastructureTitles:
    @pytest.mark.parametrize("title", [
        "Senior DevOps Engineer", "Site Reliability Engineering Manager",
        "Platform Engineering Lead", "Infrastructure Architect",
        "Cloud Operations Manager", "Security Infrastructure Engineer",
        "Systems Engineer", "Senior DevSecOps Engineer",
    ])
    def test_accepted(self, title):
        result = evaluate_contact_fit(title, open_role_count=12)
        assert result.accepted is True, f"{title!r} should be a buyer"
        assert result.category in ("functional", "exec_tech")


class TestWrongFunctions:
    """Senior-sounding and not the buyer. Seniority alone would pass all of
    these."""

    @pytest.mark.parametrize("title", [
        "VP of Data", "VP of Sales", "VP of Product", "VP of Marketing",
        "Head of People", "Chief Revenue Officer", "Director of Finance",
        "Head of Design", "VP of Customer Success", "Head of Legal",
    ])
    def test_rejected_regardless_of_seniority(self, title):
        result = evaluate_contact_fit(title, open_role_count=5)
        assert result.accepted is False, f"{title!r} is not an infra buyer"

    def test_data_platform_roles_are_exempt(self):
        """"Data Platform Engineer" and "Head of Data Infrastructure" ARE
        infrastructure roles despite containing "data" -- a blanket "data"
        rejection would throw away real buyers."""
        for title in ("Data Platform Engineer", "Head of Data Infrastructure",
                      "Data Infrastructure Architect"):
            assert evaluate_contact_fit(title, open_role_count=8).accepted is True, title


class TestFounderSizeGate:
    @pytest.mark.parametrize("title", ["Founder", "Co-Founder", "CEO", "Owner", "co-founder"])
    def test_accepted_at_or_below_the_threshold(self, title):
        assert evaluate_contact_fit(title, open_role_count=FOUNDER_MAX_OPEN_ROLES).accepted is True

    @pytest.mark.parametrize("title", ["Founder", "Co-Founder", "CEO", "Owner"])
    def test_rejected_above_the_threshold(self, title):
        result = evaluate_contact_fit(title, open_role_count=FOUNDER_MAX_OPEN_ROLES + 1)
        assert result.accepted is False
        assert str(FOUNDER_MAX_OPEN_ROLES) in result.reason

    def test_unknown_company_size_rejects_a_founder_title(self):
        """Without a size signal the rule cannot be applied, and guessing in
        the permissive direction is what put a large company's CEO into the
        outbound queue."""
        result = evaluate_contact_fit("CEO", open_role_count=None)
        assert result.accepted is False
        assert "size unknown" in result.reason

    def test_unknown_size_does_not_block_a_cto(self):
        """The size gate is founder-specific; an exec tech title stands on
        its own."""
        assert evaluate_contact_fit("CTO", open_role_count=None).accepted is True

    def test_threshold_matches_the_spec(self):
        """<=50 people, proxied by <=10 open roles until a real headcount
        source exists."""
        assert FOUNDER_MAX_OPEN_ROLES == 10


class TestEdges:
    @pytest.mark.parametrize("title", [None, "", "   "])
    def test_no_title_is_not_a_buyer(self, title):
        result = evaluate_contact_fit(title, open_role_count=5)
        assert result.accepted is False
        assert result.reason == "no title"

    def test_an_unrecognised_title_is_rejected_not_assumed(self):
        result = evaluate_contact_fit("Chief Happiness Officer", open_role_count=5)
        assert result.accepted is False

    def test_every_decision_carries_a_reason(self):
        for title, roles in [("CTO", 5), ("VP of Data", 5), ("CEO", 99), (None, 5)]:
            assert evaluate_contact_fit(title, open_role_count=roles).reason

    def test_accepted_results_name_a_category(self):
        for title, roles in [("CTO", 5), ("Senior DevOps Engineer", 5), ("CEO", 3)]:
            result = evaluate_contact_fit(title, open_role_count=roles)
            assert result.accepted and result.category
