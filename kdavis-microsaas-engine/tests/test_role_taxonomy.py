"""
tests/test_role_taxonomy.py

Role taxonomy (decision 2) and the open-role size proxy (decision 4),
2026-10-01.

Every "must match" title below is a REAL title sampled from the ATS boards
in the cache, and every "must not match" is a real title that was correctly
rejected. Measured title-match rate against 1,894 real postings: 7.39%, up
from 1.0-1.7% with the old five-literal-phrase list (target was 5-8%).
"""

import pytest

from agents.marketing.role_taxonomy import (
    DEFAULT_INCLUDE,
    DEFAULT_INTENT_BOOST,
    DEFAULT_NEGATIVE,
    SIZE_PROXY_MAX_OPEN_ROLES,
    SIZE_PROXY_MIN_OPEN_ROLES,
    UNDATED_INTENT_MULTIPLIER,
    RoleTaxonomy,
    intent_multiplier_for,
    matching_postings,
    size_proxy_in_band,
)


class _Posting:
    """Minimal stand-in for AtsPosting -- matching_postings only reads
    .title and .age_days."""

    def __init__(self, title, age_days=1):
        self.title = title
        self.age_days = age_days


@pytest.fixture
def taxonomy():
    return RoleTaxonomy.from_config(None)


class TestIncludedRoleFamilies:
    """Real titles the old five-phrase list missed. Each was sampled from
    live ATS data on 2026-10-01, not invented."""

    @pytest.mark.parametrize("title,family", [
        ("Senior DevOps Engineer", "devops"),
        ("Senior DevSecOps Engineer", "devsecops"),
        ("Staff Site Reliability Engineer II", "sre"),
        ("Software Engineer - Reliability (US Citizen)", "sre"),
        ("Platform Engineer II", "platform"),
        ("Head of Platform", "platform"),
        ("Director - Data Platforms & Architecture", "platform"),
        ("Senior Data Architect - Databricks Platform", "platform"),
        ("Founding Infrastructure Engineer", "infrastructure"),
        ("VP, Infrastructure", "infrastructure"),
        ("Cloud Senior Engineer", "cloud"),
        ("Cloud Software Architect", "cloud"),
        ("Principal Cloud Architect", "cloud"),
        ("Senior Systems Engineer", "systems"),
        ("Junior Systems Administrator", "systems"),
        ("Network Architect", "network"),
        ("Senior Cloud Security Engineer", "cloud_security"),
        ("Security Infrastructure Engineer", "cloud_security"),
        ("Build & Release Engineer", "build_release"),
        ("Kubernetes Platform Engineer", "kubernetes"),
    ])
    def test_matches_expected_family(self, taxonomy, title, family):
        result = taxonomy.match(title)
        assert result.is_match, f"{title!r} should be a relevant role"
        assert family in result.families, f"{title!r} -> {result.families}, expected {family}"

    def test_intervening_words_do_not_defeat_the_match(self, taxonomy):
        """"Cloud Senior Engineer" and "Cloud Software Architect" both put a
        word between the domain and the role noun, which the first version
        of this list could not handle."""
        for title in ("Cloud Senior Engineer", "Cloud Software Architect",
                      "Principal Cloud Software Architect"):
            assert taxonomy.match(title).is_match, title

    def test_buyer_titles_match(self, taxonomy):
        """The first draft of the role-noun list had only IC nouns, so "Head
        of Platform" -- a primary buyer title -- did not match at all."""
        for title in ("Head of Platform", "Head of Infrastructure",
                      "VP of Platform Engineering", "Director of Platform Engineering"):
            assert taxonomy.match(title).is_match, title


class TestNegativeRoles:
    @pytest.mark.parametrize("title,reason", [
        ("Sr. Sales Engineer - Rubrik Agent Cloud", "sales"),
        ("Account Executive", "sales"),
        ("Enterprise Account Executive, Platform", "sales"),
        ("SDR - Cloud Infrastructure", "sales"),
        ("Global Solutions Architect - Cloud", "solutions_engineer"),
        ("Senior Solution Architect - Cloud, Data & Infrastructure", "solutions_engineer"),
        ("Technical Support Engineer", "support"),
        ("Customer Success Manager", "customer_success"),
        ("Lead Consultant - Data Platforms & Architecture", "consultant"),
        ("Forward Deployed Engineer", "consultant"),
    ])
    def test_rejected_with_a_named_reason(self, taxonomy, title, reason):
        result = taxonomy.match(title)
        assert result.is_match is False
        assert reason in result.negatives

    def test_negative_wins_over_an_include_family(self, taxonomy):
        """"Platform Solutions Engineer" is a solutions engineer. Matching
        the include list first would have made it a platform hire."""
        result = taxonomy.match("Platform Solutions Engineer")
        assert "platform" in result.families
        assert result.is_match is False

    def test_salesforce_is_exempt_from_the_sales_negative(self, taxonomy):
        """A Salesforce platform owner is a real buyer, and "Salesforce"
        contains "sales"."""
        result = taxonomy.match("Salesforce Platform Director")
        assert result.negatives == []
        assert result.is_match is True

    @pytest.mark.parametrize("title", [
        "Head of RevOps",
        "Join Our Talent Network",
        "Application Systems Analyst II",
        "Senior Product Marketing Manager",
    ])
    def test_non_infrastructure_titles_simply_do_not_match(self, taxonomy, title):
        assert taxonomy.match(title).is_match is False


class TestIntentBoost:
    @pytest.mark.parametrize("title,boost", [
        ("Founding Infrastructure Engineer", "founding"),
        ("First DevOps Hire", "first"),
        ("Lead Platform Engineer", "lead"),
        ("Head of Infrastructure", "head_of"),
    ])
    def test_boost_detected(self, taxonomy, title, boost):
        assert boost in taxonomy.match(title).boosts

    def test_boosts_do_not_by_themselves_make_a_match(self, taxonomy):
        result = taxonomy.match("Founding Account Executive")
        assert "founding" in result.boosts
        assert result.is_match is False


class TestConfigurability:
    def test_config_overrides_a_single_section_and_keeps_the_rest(self):
        tax = RoleTaxonomy.from_config({"include": {"only_sre": r"\bSRE\b"}})
        assert tax.match("Senior SRE").is_match
        assert tax.match("Platform Engineer").is_match is False
        # negative + intent lists fall back to the defaults rather than
        # silently becoming empty
        assert tax.match("Sales Engineer").negatives
        assert tax.match("Head of SRE").boosts

    def test_a_plain_list_of_terms_is_accepted(self):
        """A config author who does not write regex should still get safe,
        word-boundary matching."""
        tax = RoleTaxonomy.from_config({"include": ["DevOps", "SRE"]})
        assert tax.match("Senior DevOps Engineer").is_match
        assert tax.match("DevOpsology Researcher").is_match is False

    def test_an_invalid_regex_degrades_instead_of_crashing(self):
        """A bad pattern in config must not take a scout run down. It is
        escaped to a literal, which may then match nothing -- that is an
        acceptable degradation; raising at import time is not."""
        tax = RoleTaxonomy.from_config({"include": {"broken": "([unclosed"}})
        assert tax.match("Platform Engineer").is_match is False  # no crash
        assert "broken" in tax.include

    def test_none_config_uses_every_default(self):
        tax = RoleTaxonomy.from_config(None)
        assert set(tax.include) == set(DEFAULT_INCLUDE)
        assert set(tax.negative) == set(DEFAULT_NEGATIVE)
        assert set(tax.intent_boost) == set(DEFAULT_INTENT_BOOST)


class TestMatchingPostings:
    def test_returns_per_reason_drop_counts(self, taxonomy):
        postings = [
            _Posting("Senior Platform Engineer"),
            _Posting("Account Executive"),
            _Posting("Office Manager"),
            _Posting("Technical Support Engineer"),
        ]
        matched, drops = matching_postings(postings, taxonomy)
        assert [p.title for p in matched] == ["Senior Platform Engineer"]
        assert drops["negative_sales"] == 1
        assert drops["negative_support"] == 1
        assert drops["no_role_family"] == 1

    def test_undated_posting_is_kept_and_counted(self, taxonomy):
        """Decision 3 (2026-10-01): "keep them; apply a x0.7 intent
        multiplier instead of discarding". Previously an undated posting was
        dropped by any freshness filter -- that discarded 83 and 84 postings
        in the first two live runs, about half of every taxonomy match."""
        postings = [_Posting("Platform Engineer", age_days=None)]
        matched, drops = matching_postings(postings, taxonomy, max_age_days=30)
        assert len(matched) == 1
        assert drops["undated_kept"] == 1
        assert "stale_dated" not in drops

    def test_a_genuinely_old_dated_posting_is_still_dropped(self):
        """"We cannot tell when this was posted" and "this was posted four
        months ago" are different facts and were being conflated."""
        tax = RoleTaxonomy.from_config(None)
        matched, drops = matching_postings(
            [_Posting("Platform Engineer", age_days=400)], tax, max_age_days=30)
        assert matched == []
        assert drops["stale_dated"] == 1

    def test_no_freshness_filter_keeps_everything_relevant(self, taxonomy):
        postings = [_Posting("Platform Engineer", age_days=None),
                    _Posting("DevOps Engineer", age_days=400)]
        matched, _drops = matching_postings(postings, taxonomy, max_age_days=None)
        assert len(matched) == 2

    def test_title_only_never_the_description(self, taxonomy):
        """Matching the description turns "we use Terraform to manage our
        sales CRM" into a platform-engineering signal."""
        posting = _Posting("Account Executive")
        posting.description_text = "You will use Terraform and Kubernetes dashboards daily."
        assert matching_postings([posting], taxonomy)[0] == []


class TestSizeProxy:
    """Decision 4: headcount stays unknown from ATS text, so the board's
    total open-role count is the company-size proxy. in-ICP = 3-40."""

    @pytest.mark.parametrize("count,expected", [
        (3, True), (6, True), (40, True),
        (0, False), (1, False), (2, False),
        (41, False), (77, False), (120, False),
    ])
    def test_band_edges(self, count, expected):
        assert size_proxy_in_band(count)[0] is expected

    def test_rubrik_and_capco_fall_out(self):
        """Both ran 120 open roles in the first live runs."""
        for count in (120, 77):
            in_band, reason = size_proxy_in_band(count)
            assert in_band is False
            assert "enterprise/staffing" in reason

    def test_unknown_count_is_not_in_band(self):
        """Unlike headcount_band's three-valued logic: the open-role count is
        always known for a board we successfully fetched, so None means the
        fetch failed -- and a company we could not read is not one to
        qualify."""
        in_band, reason = size_proxy_in_band(None)
        assert in_band is False
        assert "unknown" in reason

    def test_reason_is_always_populated(self):
        for count in (None, 0, 5, 500):
            assert size_proxy_in_band(count)[1]

    def test_band_is_configurable(self):
        assert size_proxy_in_band(100, low=50, high=200)[0] is True

    def test_declared_defaults_match_the_spec(self):
        assert SIZE_PROXY_MIN_OPEN_ROLES == 3
        assert SIZE_PROXY_MAX_OPEN_ROLES == 40


class TestUndatedIntentMultiplier:
    """Decision 3: undated postings are kept at x0.7 intent rather than
    discarded. The penalty is per-COMPANY, not per-posting: one dated
    posting is enough to establish that the board is current."""

    def test_all_undated_gets_the_penalty(self):
        mult, reason = intent_multiplier_for([_Posting("Platform Engineer", age_days=None)])
        assert mult == UNDATED_INTENT_MULTIPLIER == 0.7
        assert "undated" in reason

    def test_one_dated_posting_restores_full_weight(self):
        mult, reason = intent_multiplier_for([
            _Posting("Platform Engineer", age_days=None),
            _Posting("DevOps Engineer", age_days=3),
        ])
        assert mult == 1.0
        assert "dated posting present" in reason

    def test_empty_list_is_penalised_not_crashed(self):
        mult, _ = intent_multiplier_for([])
        assert mult == UNDATED_INTENT_MULTIPLIER

    def test_the_multiplier_actually_lowers_the_intent_score(self):
        from agents.marketing.lead_qualification import intent_score

        full = intent_score(posting_age_days=None, open_role_count=3, undated_multiplier=1.0)
        penalised = intent_score(posting_age_days=None, open_role_count=3, undated_multiplier=0.7)
        assert penalised.value < full.value
        assert any("undated" in r for r in penalised.reasons)

    def test_an_undated_company_still_qualifies(self):
        """The point of the change: a weaker signal, not no signal."""
        from agents.marketing.lead_qualification import intent_score

        assert intent_score(posting_age_days=None, open_role_count=4,
                           stack=["Azure", "Terraform"], undated_multiplier=0.7).value > 0
