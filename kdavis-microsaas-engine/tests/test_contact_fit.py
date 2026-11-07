"""
tests/test_contact_fit.py

Buyer-only contact rules (Kelvin, 2026-11-07). Replaces the 2026-10-01
suite, whose central assertion -- that "Senior DevOps Engineer", "Systems
Engineer" and "Infrastructure Architect" are buyers -- is exactly why the
outbound system reached workers instead of budget owners.

The rule itself lives in agents.marketing.buyer_gate; contact_fit delegates.
"""

import pytest

from agents.marketing.buyer_gate import ALLOW, AMBIGUOUS, DENY, classify_buyer_title
from agents.marketing.contact_fit import evaluate_contact_fit

ALLOWED = [
    "CTO",
    "Chief Technology Officer",
    "Co-Founder & CTO",
    "VP Engineering",
    "VP of Engineering",
    "Vice President of Engineering",
    "SVP, Infrastructure",
    "VP Infrastructure",
    "VP, Platform Engineering",
    "Vice President of Platform",
    "Head of Engineering",
    "Head of Platform",
    "Head of Infrastructure",
    "Head of DevOps",
    "Head of SRE",
    "Director of Engineering",
    "Director Of Engineering",
    "Engineering Director",
    "Senior Director, Platform Engineering",
    "Director of Platform Engineering",
    "Director of Infrastructure",
    "Director of Site Reliability Engineering",
    "Founder",
    "Co-founder",
    "Cofounder",
    "Chief Architect",
]

DENIED = [
    "Senior DevOps Engineer",
    "Platform Engineer",
    "Staff Software Engineer",
    "Principal Engineer",
    "Site Reliability Engineer",
    "SRE",
    "SRE Manager",
    "Cloud Architect",
    "Infrastructure Architect",
    "Solutions Architect",
    "Software Developer",
    "Systems Administrator",
    "Data Analyst",
    "Security Specialist",
    "DevOps Consultant",
    "Software Engineering Intern",
    "Technical Recruiter",
    "Talent Acquisition Partner",
    "Head of People",
    "HR Business Partner",
    "VP of Sales",
    "VP Marketing",
    "Director, Design",
    "Chief Operating Officer",
    "Open to work | ex-CTO",
    "Former VP Engineering",
    "Founding Engineer",
]

AMBIGUOUS_TITLES = [
    "Chief Information Officer",
    "CIO",
    "CISO",
    "VP of Data",
    "EVP",
    "VP",
    "Engineering Manager",
    "Director-General",
    "Founder & Principal Engineer",
    "",
    None,
]


@pytest.mark.parametrize("title", ALLOWED)
def test_buyer_titles_are_allowed(title):
    v = classify_buyer_title(title)
    assert v.verdict == ALLOW, v.reason
    assert evaluate_contact_fit(title).accepted


@pytest.mark.parametrize("title", DENIED)
def test_ic_and_non_buyer_titles_are_denied(title):
    v = classify_buyer_title(title)
    assert v.verdict == DENY, v.reason
    assert not evaluate_contact_fit(title).accepted


@pytest.mark.parametrize("title", AMBIGUOUS_TITLES)
def test_ambiguous_titles_are_never_promoted(title):
    v = classify_buyer_title(title)
    assert v.verdict == AMBIGUOUS, v.reason
    fit = evaluate_contact_fit(title)
    assert not fit.accepted
    assert fit.reason.startswith("ambiguous")


def test_case_count_meets_the_brief():
    assert len(ALLOWED) + len(DENIED) + len(AMBIGUOUS_TITLES) >= 30


class TestCeoSize:
    def test_ceo_allowed_only_with_a_stated_small_headcount(self):
        assert classify_buyer_title("CEO", headcount=30).verdict == ALLOW
        assert classify_buyer_title("CEO", headcount=50).verdict == DENY
        assert classify_buyer_title("CEO", headcount=120).verdict == DENY

    def test_few_open_roles_does_not_prove_a_small_company(self):
        # Learnosity (~150 staff) had 2 open roles.
        assert classify_buyer_title("CEO", open_role_count=2).verdict == AMBIGUOUS

    def test_many_open_roles_denies_a_ceo(self):
        assert classify_buyer_title("CEO", open_role_count=36).verdict == DENY

    def test_chief_executive_officer_is_the_same_rule(self):
        assert classify_buyer_title("Chief Executive Officer", headcount=20).verdict == ALLOW


class TestTitleHygiene:
    def test_html_in_a_scraped_title_is_stripped(self):
        v = classify_buyer_title("VP Engineering and Product, <strong>Tacita Morway</strong>")
        assert "<" not in v.reason

    def test_classifier_reads_the_title_never_a_name(self):
        # A person NAMED like a role is irrelevant: only the title is passed.
        assert classify_buyer_title("Director of Engineering").verdict == ALLOW

    def test_accepted_results_name_a_category(self):
        for title in ALLOWED:
            assert evaluate_contact_fit(title).category
