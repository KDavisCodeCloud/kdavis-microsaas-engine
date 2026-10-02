"""Tests for the founder-title employee-band lookup (2026-10-02).

The follower-vs-employee confusion is the whole reason this module exists,
so it gets the most tests: a parser that reads "15,183 followers" as a
headcount would reject every real company, and one that reads "1-10
followers" as a headcount would ACCEPT a 600-person company's CEO.
"""

import pytest

from agents.marketing.founder_band import (
    FOUNDER_MAX_HEADCOUNT,
    EmployeeBand,
    lookup_employee_band,
    parse_employee_band,
)


class FakeScraper:
    """Mimics scrapers.brave_search.BraveSearchScraper's _search contract."""

    api_key = "test-key"

    def __init__(self, items=None, raises=None):
        self.items = items or []
        self.raises = raises
        self.queries = []

    def _search(self, query):
        self.queries.append(query)
        if self.raises:
            raise self.raises
        return self.items


def _company_result(snippet, title="Learnosity | LinkedIn",
                    link="https://www.linkedin.com/company/learnosity/"):
    return {"link": link, "title": title, "snippet": snippet}


# --- band parsing -------------------------------------------------------

@pytest.mark.parametrize("text,lower,upper", [
    ("51-200 employees", 51, 200),
    ("11-50 employees", 11, 50),
    ("1-10 employees", 1, 10),
    ("201-500 employees", 201, 500),
    ("1,001-5,000 employees", 1001, 5000),
    ("2 to 10 employees", 2, 10),
    ("Company size 51–200 employees", 51, 200),   # en dash
    ("7 employees", 7, 7),
])
def test_parses_real_linkedin_bands(text, lower, upper):
    band = parse_employee_band(text)
    assert band is not None, f"failed to parse {text!r}"
    assert (band.lower, band.upper) == (lower, upper)


def test_open_ended_band_has_no_upper_bound():
    band = parse_employee_band("10,001+ employees")
    assert band is not None
    assert (band.lower, band.upper) == (10001, None)
    assert band.label == "10001+"
    # An open-ended band must never satisfy a small-company threshold.
    assert band.within(FOUNDER_MAX_HEADCOUNT) is False
    assert band.within(1_000_000) is False


@pytest.mark.parametrize("text", [
    "15,183 followers on LinkedIn",
    "1-10 followers",
    "Learnosity | 2,431 followers on LinkedIn.",
    "500+ followers",
])
def test_follower_counts_are_never_read_as_headcount(text):
    """THE critical case. A follower count must parse to None, not a band."""
    assert parse_employee_band(text) is None


def test_employee_band_is_found_even_when_followers_come_first():
    """The real-world snippet shape: followers lead, employees trail."""
    snippet = ("Learnosity | 15,183 followers on LinkedIn. Learnosity powers "
               "assessment for the world's best edtech. | 201-500 employees")
    band = parse_employee_band(snippet)
    assert band is not None
    assert (band.lower, band.upper) == (201, 500)


def test_a_small_follower_count_cannot_mask_a_large_employee_band():
    """If follower-stripping regressed, this snippet would parse as 1-10
    and wrongly ACCEPT -- the dangerous direction of the bug."""
    snippet = "BigCorp | 1-10 followers on LinkedIn | 5,001-10,000 employees"
    band = parse_employee_band(snippet)
    assert band is not None
    assert (band.lower, band.upper) == (5001, 10000)
    assert band.within(FOUNDER_MAX_HEADCOUNT) is False


@pytest.mark.parametrize("text", [None, "", "   ", "Learnosity | LinkedIn",
                                  "Education Technology, Dublin"])
def test_unparseable_text_returns_none(text):
    assert parse_employee_band(text) is None


# --- the <=50 decision --------------------------------------------------

@pytest.mark.parametrize("lower,upper,expected", [
    (1, 10, True),
    (11, 50, True),      # exactly at the ceiling
    (51, 200, False),    # one over
    (201, 500, False),
    (2, 10, True),
])
def test_within_uses_the_upper_bound_not_the_lower(lower, upper, expected):
    assert EmployeeBand(lower, upper, "x").within(FOUNDER_MAX_HEADCOUNT) is expected


# --- lookup orchestration ----------------------------------------------

def test_accepts_a_small_company_and_spends_one_query():
    scraper = FakeScraper([_company_result("Learnosity | 11-50 employees")])
    v = lookup_employee_band("Learnosity", scraper)
    assert v.accepted is True
    assert v.needs_review is False
    assert v.band.label == "11-50"
    assert v.queries_used == 1
    assert scraper.queries == ["site:linkedin.com/company Learnosity"]


def test_rejects_a_large_company_without_needing_review():
    scraper = FakeScraper([_company_result("Learnosity | 201-500 employees")])
    v = lookup_employee_band("Learnosity", scraper)
    assert (v.accepted, v.needs_review) == (False, False)
    assert v.band.label == "201-500"


def test_unparseable_becomes_needs_review_not_a_rejection():
    scraper = FakeScraper([_company_result("Learnosity | 15,183 followers on LinkedIn")])
    v = lookup_employee_band("Learnosity", scraper)
    assert v.accepted is False
    assert v.needs_review is True, "an unknown size must not read as a rejection"
    assert v.band is None
    assert v.queries_used == 1


def test_non_linkedin_results_are_ignored():
    """A company's own site or a directory listing must not set headcount."""
    scraper = FakeScraper([
        {"link": "https://learnosity.com/about",
         "snippet": "Learnosity | 11-50 employees", "title": "About Learnosity"},
    ])
    v = lookup_employee_band("Learnosity", scraper)
    assert v.needs_review is True
    assert v.band is None


def test_a_different_companys_linkedin_page_is_ignored():
    scraper = FakeScraper([
        _company_result("Acme Corp | 11-50 employees",
                        title="Acme Corp | LinkedIn",
                        link="https://www.linkedin.com/company/acme-corp/"),
    ])
    v = lookup_employee_band("Learnosity", scraper)
    assert v.needs_review is True, "matched a company we did not ask about"


def test_company_name_match_tolerates_suffixes_and_punctuation():
    scraper = FakeScraper([
        _company_result("Learnosity Ltd. | 11-50 employees",
                        title="Learnosity Ltd. | LinkedIn"),
    ])
    assert lookup_employee_band("Learnosity", scraper).accepted is True


def test_missing_brave_key_is_needs_review_and_spends_nothing():
    class NoKey:
        api_key = None
        def _search(self, q):  # pragma: no cover - must never be reached
            raise AssertionError("must not query without a key")
    v = lookup_employee_band("Learnosity", NoKey())
    assert (v.accepted, v.needs_review, v.queries_used) == (False, True, 0)


def test_a_brave_failure_is_needs_review_and_still_counts_the_query():
    scraper = FakeScraper(raises=RuntimeError("429 rate limited"))
    v = lookup_employee_band("Learnosity", scraper)
    assert (v.accepted, v.needs_review) == (False, True)
    assert v.queries_used == 1, "a failed call still consumed Brave quota"
    assert "429" in v.reason


def test_no_company_name_spends_nothing():
    scraper = FakeScraper([_company_result("11-50 employees")])
    v = lookup_employee_band("", scraper)
    assert (v.needs_review, v.queries_used) == (True, 0)
    assert scraper.queries == []


def test_stats_record_the_spend():
    stats = {}
    lookup_employee_band("Learnosity",
                         FakeScraper([_company_result("11-50 employees")]), stats=stats)
    assert stats["brave_founder_band_queries"] == 1
