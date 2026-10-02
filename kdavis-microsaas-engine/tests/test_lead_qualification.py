"""
tests/test_lead_qualification.py

Unit coverage for agents/marketing/lead_qualification.py (Scraper v2).

The headline class here is TestFieldScopedMatching: Kelvin's 2026-10-01
instruction was that title matching happens on the TITLE FIELD ONLY, and
the specific failure he named is the name-contains-keyword case -- a
person NAMED "Talent" being thrown out as a recruiter. Every assertion in
that class is a bug that a blob-matching implementation would ship.
"""

from datetime import date

import pytest

from agents.marketing.lead_qualification import (
    EMAIL_GRADES,
    HEADCOUNT_BANDS,
    ROUTE_MANUAL_LINKEDIN,
    ROUTE_OUTBOUND_EMAIL,
    ROUTE_REJECT,
    SENIORITY_TIERS,
    ScoringConfig,
    classify_seniority,
    email_may_send,
    extract_stack,
    fit_score,
    funding_recency_months,
    grade_email,
    headcount_band,
    headcount_in_band,
    intent_score,
    is_negative_title,
    is_role_address,
    parse_location,
    parse_person_result,
    qualify,
    route_lead,
)


# ── 3.1 Structured parsing ───────────────────────────────────────────────

class TestStructuredParsing:
    def test_three_segment_linkedin_title(self):
        p = parse_person_result({"title": "Jane Doe - VP of Engineering - Acme Corp | LinkedIn"})
        assert p.name == "Jane Doe"
        assert p.title == "VP of Engineering"
        assert p.company == "Acme Corp"
        assert p.is_complete

    def test_en_dash_separator(self):
        p = parse_person_result({"title": "Marcus Webb – Head of Platform – Northwind Systems | LinkedIn"})
        assert (p.name, p.title, p.company) == ("Marcus Webb", "Head of Platform", "Northwind Systems")

    def test_two_segment_name_plus_company_is_not_read_as_a_title(self):
        """'Acme Corp' must land in company, never in title -- a company
        name in the title field becomes a fabricated role claim."""
        p = parse_person_result({"title": "Jane Doe - Acme Corp | LinkedIn"})
        assert p.name == "Jane Doe"
        assert p.company == "Acme Corp"
        assert p.title is None

    def test_two_segment_name_plus_title_is_not_read_as_a_company(self):
        p = parse_person_result({"title": "Jane Doe - Director of Infrastructure | LinkedIn"})
        assert p.name == "Jane Doe"
        assert p.title == "Director of Infrastructure"
        assert p.company is None

    def test_on_linkedin_shape_claims_no_title_or_company(self):
        """'X on LinkedIn: <post text>' carries a name and nothing else.
        Parsing the post text as a title is how snippet noise becomes a
        factual claim about someone's job."""
        p = parse_person_result({"title": "Priya Raman on LinkedIn: we're hiring a platform engineer"})
        assert p.name == "Priya Raman"
        assert p.title is None
        assert p.company is None

    def test_segment_order_reversed_still_resolves(self):
        p = parse_person_result({"title": "Jane Doe - Acme Corp - VP Engineering | LinkedIn"})
        assert p.title == "VP Engineering"
        assert p.company == "Acme Corp"

    def test_empty_title_yields_all_none(self):
        p = parse_person_result({"title": "", "snippet": ""})
        assert (p.name, p.title, p.company, p.location) == (None, None, None, None)
        assert not p.is_complete

    def test_company_not_mistaken_for_person_name(self):
        """A result whose first segment is a company must not populate
        `name` -- 'Northwind Technologies' is not a person."""
        p = parse_person_result({"title": "Northwind Technologies - Careers"})
        assert p.name is None

    @pytest.mark.parametrize("snippet,expected", [
        ("Location: Austin, Texas", "Austin, Texas"),
        ("Based in Greater Boston Area and leading platform work", "Greater Boston Area"),
        ("Denver, CO · 200 connections", "Denver, CO"),
        ("No place named here at all", None),
    ])
    def test_location_parsing(self, snippet, expected):
        assert parse_location(snippet) == expected

    def test_location_comes_from_snippet_not_title(self):
        p = parse_person_result({
            "title": "Jane Doe - VP of Engineering - Acme Corp | LinkedIn",
            "snippet": "Location: Seattle, WA",
        })
        assert p.location == "Seattle, WA"


# ── 3.2 Seniority + negative titles, field-scoped ────────────────────────

class TestSeniorityClassifier:
    @pytest.mark.parametrize("title,tier", [
        ("CTO", "c_level"),
        ("Chief Technology Officer", "c_level"),
        ("Co-Founder & CEO", "c_level"),
        ("VP of Engineering", "vp"),
        ("SVP, Platform", "vp"),
        ("Head of Infrastructure", "head"),
        ("Director of Platform Engineering", "director"),
        ("Engineering Manager", "manager"),
        ("Tech Lead", "manager"),
        ("Senior Site Reliability Engineer", "ic"),
        ("Cloud Architect", "ic"),
        ("", "unknown"),
        ("Enthusiast", "unknown"),
    ])
    def test_tiers(self, title, tier):
        assert classify_seniority(title) == tier

    def test_none_is_unknown_not_a_crash(self):
        assert classify_seniority(None) == "unknown"

    def test_most_senior_tier_wins(self):
        """'VP, Engineering (Director level)' is a VP, not a director."""
        assert classify_seniority("VP, Engineering (Director level)") == "vp"

    def test_every_returned_tier_is_declared(self):
        for title in ("CTO", "VP Eng", "Head of Ops", "Director IT", "Eng Manager", "DevOps Engineer"):
            assert classify_seniority(title) in SENIORITY_TIERS


class TestFieldScopedMatching:
    """The cases Kelvin named. Each one is a lead a blob-matching
    implementation silently throws away or silently keeps."""

    def test_person_named_talent_is_not_a_recruiter(self):
        """THE case. 'Avery Talent' is a surname in the NAME field;
        is_negative_title only ever sees the title, so the lead survives."""
        p = parse_person_result({"title": "Avery Talent - VP of Engineering - Acme Corp | LinkedIn"})
        assert p.name == "Avery Talent"
        rejected, reason = is_negative_title(p.title)
        assert rejected is False, f"a person named Talent was rejected as {reason}"
        assert classify_seniority(p.title) == "vp"

    def test_head_of_internal_audit_is_not_an_intern(self):
        """'intern' is a substring of 'Internal'. Word boundaries, not
        substrings."""
        rejected, reason = is_negative_title("Head of Internal Audit")
        assert rejected is False, f"rejected as {reason}"

    @pytest.mark.parametrize("title", [
        "Director of International Operations",
        "Head of Internet Infrastructure",
        "VP, Internal Platform Engineering",
    ])
    def test_intern_substrings_survive(self, title):
        assert is_negative_title(title)[0] is False

    def test_aegis_is_not_an_account_executive(self):
        """'AE' must be \\b-anchored or 'AEgis' matches."""
        assert is_negative_title("AEgis Platform Lead")[0] is False

    def test_sdram_is_not_an_sdr(self):
        assert is_negative_title("SDRAM Firmware Architect")[0] is False

    def test_salesforce_platform_director_is_a_buyer_not_sales(self):
        """A Salesforce platform owner is exactly who buys infra work.
        The exemption list exists for this."""
        assert is_negative_title("Salesforce Platform Director")[0] is False

    def test_negative_title_never_inspects_the_name_field(self):
        """End-to-end via qualify(): a recruiter-sounding NAME with a
        buyer TITLE must route on the title."""
        out = qualify(
            {"title": "Dana Recruiter - CTO - Northwind Systems | LinkedIn"},
            domain="northwind.example",
            email_verification_status="verified",
            posting_age_days=3,
            open_role_count=3,
            jd_text="We run Azure and Terraform across 40 employees.",
        )
        assert out["name"] == "Dana Recruiter"
        assert out["seniority"] == "c_level"
        assert out["negative_reason"] is None
        assert out["route"] == ROUTE_OUTBOUND_EMAIL


class TestNegativeTitlesStillReject:
    """The other half: the list has to actually work."""

    @pytest.mark.parametrize("title,reason", [
        ("Technical Recruiter", "recruiting"),
        ("Talent Acquisition Partner", "recruiting"),
        ("Senior Sourcer", "recruiting"),
        ("Engineering Intern", "student"),
        ("Computer Science Student", "student"),
        ("Open to Work | Former DevOps Engineer", "open_to_work"),
        ("Account Executive", "sales"),
        ("SDR", "sales"),
        ("Enterprise Sales Director", "sales"),
    ])
    def test_rejected_with_named_reason(self, title, reason):
        rejected, got = is_negative_title(title)
        assert rejected is True
        assert got == reason

    def test_none_title_is_not_rejected(self):
        """An ATS posting names no hiring manager. No title is 'unknown',
        not 'disqualified' -- otherwise company-first sourcing rejects
        every lead it finds."""
        assert is_negative_title(None) == (False, None)


# ── 3.5 Technographics ───────────────────────────────────────────────────

class TestTechnographics:
    def test_extracts_canonical_names(self):
        jd = "You'll work with AWS, k8s, and Terraform. Bonus: GitHub Actions."
        assert extract_stack(jd) == ["AWS", "GitHub Actions", "Kubernetes", "Terraform"]

    def test_k8s_and_kubernetes_collapse_to_one_tag(self):
        assert extract_stack("kubernetes and k8s and EKS") == ["Kubernetes"]

    def test_empty_text_is_empty_list_not_none(self):
        assert extract_stack(None) == []
        assert extract_stack("") == []

    def test_sorted_for_a_stable_stored_value(self):
        assert extract_stack("Terraform Azure AWS") == extract_stack("AWS Azure Terraform")


# ── 3.6 Firmographics ────────────────────────────────────────────────────

class TestFirmographics:
    @pytest.mark.parametrize("text,band", [
        ("A team of 45 engineers", "20-49"),
        ("20-50 employees", "50-99"),
        ("We are 150 employees strong", "100-300"),
        ("11 employees", "1-19"),
        ("5000 employees", "1000+"),
        ("No headcount stated", None),
    ])
    def test_band_extraction(self, text, band):
        assert headcount_band(text) == band

    def test_every_band_is_declared(self):
        for text in ("8 employees", "30 employees", "80 employees", "250 employees", "600 employees", "9000 employees"):
            assert headcount_band(text) in HEADCOUNT_BANDS

    def test_unknown_headcount_is_three_valued_not_false(self):
        """None must mean 'unknown', never 'out of band' -- most real SMB
        prospects never state headcount anywhere a search result sees."""
        assert headcount_in_band(None, 20, 300) is None
        assert headcount_in_band("20-49", 20, 300) is True
        assert headcount_in_band("1000+", 20, 300) is False

    def test_funding_needs_both_an_event_and_a_date(self):
        assert funding_recency_months("raised a Series A in March 2026", today=date(2026, 9, 30)) == 6
        # event with no date -> None, never a guess
        assert funding_recency_months("raised a Series A recently", today=date(2026, 9, 30)) is None
        # date with no event -> None
        assert funding_recency_months("founded in March 2020", today=date(2026, 9, 30)) is None

    def test_future_dated_funding_is_rejected_not_negative(self):
        assert funding_recency_months("closed a seed round in December 2027", today=date(2026, 9, 30)) is None


# ── 3.8 Email confidence grades ──────────────────────────────────────────

class TestEmailGrades:
    @pytest.mark.parametrize("status,grade", [
        ("verified", "valid"),
        ("catch_all", "risky"),
        ("invalid", "invalid"),
        ("unverified", "unknown"),
        (None, "unknown"),
        ("nonsense", "unknown"),
    ])
    def test_status_maps_to_grade(self, status, grade):
        assert grade_email(status) == grade
        assert grade_email(status) in EMAIL_GRADES

    def test_verified_on_catch_all_domain_is_downgraded_to_risky(self):
        """A catch-all accepts every RCPT TO, so a 250 proves the domain
        answers -- not that the mailbox exists. Shipping these as valid is
        how a bounce rate climbs quietly."""
        assert grade_email("verified", domain_is_catch_all=True) == "risky"

    def test_role_address_is_capped_at_risky(self):
        assert grade_email("verified", role_address=True) == "risky"

    @pytest.mark.parametrize("email,is_role", [
        ("info@acme.com", True),
        ("careers@acme.com", True),
        ("jane.doe@acme.com", False),
        (None, False),
        ("not-an-email", False),
    ])
    def test_role_address_detection(self, email, is_role):
        assert is_role_address(email) is is_role

    def test_only_valid_sends_unconditionally(self):
        """email_may_send is the UNCONDITIONAL gate. "risky" is excluded here
        on purpose -- it sends only via MKT-O5's capped branch, which runs
        before this check."""
        assert email_may_send("valid") is True
        for grade in ("risky", "invalid", "unknown"):
            assert email_may_send(grade) is False, f"{grade} must not send unconditionally"

    def test_valid_and_risky_may_be_routed_outbound(self):
        """Decision 3b (2026-10-01): catch-all addresses may take the
        outbound route, with volume capped at send time."""
        from agents.marketing.lead_qualification import email_may_route_outbound
        assert email_may_route_outbound("valid") is True
        assert email_may_route_outbound("risky") is True
        for grade in ("invalid", "unknown", None, ""):
            assert email_may_route_outbound(grade) is False, f"{grade!r} must not be routed outbound"

    def test_risky_routes_outbound_and_says_it_is_capped(self):
        """Regression: route_lead used to send every risky lead to
        manual_linkedin, and MKT-O5 checks the stored lead_route BEFORE its
        risky branch -- so the warmup cap was unreachable for exactly the
        leads it was written for."""
        from agents.marketing.lead_qualification import (
            ROUTE_OUTBOUND_EMAIL, Score, route_lead,
        )
        route, reason = route_lead(
            email_grade="risky", has_domain=True,
            fit=Score(0.85, []), intent=Score(0.80, []),
        )
        assert route == ROUTE_OUTBOUND_EMAIL
        assert "cap" in reason.lower(), (
            f"the route must record that risky volume is bounded; got {reason!r}")

    def test_invalid_and_unknown_still_route_manual(self):
        from agents.marketing.lead_qualification import (
            ROUTE_MANUAL_LINKEDIN, Score, route_lead,
        )
        for grade in ("invalid", "unknown"):
            route, _ = route_lead(
                email_grade=grade, has_domain=True,
                fit=Score(0.85, []), intent=Score(0.80, []),
            )
            assert route == ROUTE_MANUAL_LINKEDIN, f"{grade} must stay off the email track"


# ── 3.9 Scoring ──────────────────────────────────────────────────────────

class TestScoring:
    def test_scores_stay_in_range(self):
        best = fit_score(seniority="c_level", headcount_band_value="20-49",
                         stack=["Azure", "AWS", "Terraform"], has_domain=True)
        worst = fit_score(seniority="unknown", headcount_band_value="1000+", stack=[], has_domain=False)
        assert 0.0 <= worst.value <= best.value <= 1.0

    def test_seniority_dominates_fit(self):
        c = fit_score(seniority="c_level", has_domain=True)
        ic = fit_score(seniority="ic", has_domain=True)
        assert c.value > ic.value

    def test_unknown_headcount_scores_between_in_band_and_out_of_band(self):
        in_band = fit_score(seniority="vp", headcount_band_value="20-49").value
        unknown = fit_score(seniority="vp", headcount_band_value=None).value
        out_band = fit_score(seniority="vp", headcount_band_value="1000+").value
        assert out_band < unknown < in_band

    def test_fresh_posting_outscores_stale(self):
        fresh = intent_score(posting_age_days=2, open_role_count=1)
        stale = intent_score(posting_age_days=90, open_role_count=1)
        assert fresh.value > stale.value

    def test_unknown_posting_age_earns_no_intent_credit(self):
        """Unlike firmographics, 'we can't tell when they posted' genuinely
        is the absence of an intent signal."""
        unknown = intent_score(posting_age_days=None, open_role_count=1)
        stale = intent_score(posting_age_days=90, open_role_count=1)
        assert unknown.value == stale.value

    def test_more_open_roles_raises_intent(self):
        assert intent_score(open_role_count=4).value > intent_score(open_role_count=1).value

    def test_every_score_carries_reasons(self):
        """A rejected lead must always be explainable."""
        assert fit_score(seniority="ic").reasons
        assert intent_score(posting_age_days=5).reasons

    def test_thresholds_are_configurable_not_hardcoded(self):
        cfg = ScoringConfig(fit_threshold=0.99)
        fit = fit_score(seniority="vp", headcount_band_value="20-49", has_domain=True, config=cfg)
        route, reason = route_lead(email_grade="valid", has_domain=True, fit=fit,
                                  intent=intent_score(posting_age_days=1, open_role_count=3), config=cfg)
        assert route == ROUTE_REJECT
        assert "0.99" in reason


# ── Routing ──────────────────────────────────────────────────────────────

class TestRouting:
    def _good(self, cfg=None):
        return (
            fit_score(seniority="vp", headcount_band_value="20-49",
                      stack=["Azure", "Terraform"], has_domain=True, config=cfg),
            intent_score(posting_age_days=3, open_role_count=3, stack=["Azure", "Terraform"], config=cfg),
        )

    def test_qualified_lead_routes_to_email(self):
        fit, intent = self._good()
        assert route_lead(email_grade="valid", has_domain=True, fit=fit, intent=intent)[0] == ROUTE_OUTBOUND_EMAIL

    def test_domainless_lead_routes_to_manual_linkedin_never_email(self):
        """Kelvin, 2026-10-01: 'Domain-less leads route to the manual
        LinkedIn track only.' Not dropped, and never emailed."""
        fit, intent = self._good()
        route, reason = route_lead(email_grade="valid", has_domain=False, fit=fit, intent=intent)
        assert route == ROUTE_MANUAL_LINKEDIN
        assert reason == "no domain"

    @pytest.mark.parametrize("grade", ["invalid", "unknown"])
    def test_ungradeable_email_never_routes_to_email(self, grade):
        """"risky" is deliberately NOT in this list any more (decision 3b,
        2026-10-01): a catch-all takes the outbound route and is rationed at
        send time instead. invalid and unknown stay off the email track
        entirely."""
        fit, intent = self._good()
        assert route_lead(email_grade=grade, has_domain=True, fit=fit, intent=intent)[0] == ROUTE_MANUAL_LINKEDIN

    def test_a_domainless_risky_lead_is_still_manual(self):
        """The no-domain rule outranks the risky allowance -- there is no
        address to send to."""
        fit, intent = self._good()
        assert route_lead(email_grade="risky", has_domain=False,
                          fit=fit, intent=intent)[0] == ROUTE_MANUAL_LINKEDIN

    def test_low_fit_rejects_before_email_grade_matters(self):
        low = fit_score(seniority="unknown", has_domain=False)
        _, intent = self._good()
        assert route_lead(email_grade="valid", has_domain=True, fit=low, intent=intent)[0] == ROUTE_REJECT


# ── End-to-end qualify() ─────────────────────────────────────────────────

class TestQualifyPipeline:
    def test_full_pipeline_on_a_good_lead(self):
        out = qualify(
            {"title": "Jane Doe - VP of Engineering - Northwind Systems | LinkedIn",
             "snippet": "Location: Austin, Texas"},
            jd_text="We run Azure, Terraform and Kubernetes. A team of 60 employees. We raised a Series A in March 2026.",
            posting_age_days=4,
            open_role_count=3,
            domain="northwind.example",
            email="jane.doe@northwind.example",
            email_verification_status="verified",
            today=date(2026, 9, 30),
        )
        assert out["name"] == "Jane Doe"
        assert out["title"] == "VP of Engineering"
        assert out["company"] == "Northwind Systems"
        assert out["location"] == "Austin, Texas"
        assert out["seniority"] == "vp"
        assert out["stack_tags"] == ["Azure", "Kubernetes", "Terraform"]
        assert out["headcount_band"] == "50-99"
        assert out["funding_months"] == 6
        assert out["email_grade"] == "valid"
        assert out["route"] == ROUTE_OUTBOUND_EMAIL
        assert out["score_reasons"]

    def test_negative_title_rejects_with_an_attributable_reason(self):
        out = qualify(
            {"title": "Sam Smith - Technical Recruiter - Acme Corp | LinkedIn"},
            domain="acme.example",
            email_verification_status="verified",
        )
        assert out["route"] == ROUTE_REJECT
        assert out["route_reason"] == "negative_title:recruiting"
        assert out["negative_reason"] == "recruiting"

    def test_no_structured_data_never_produces_a_fabricated_field(self):
        """The exact shape of the 60 leads disqualified as
        'no_structured_data': nothing parseable in, nothing invented out."""
        out = qualify({"title": "", "snippet": ""})
        assert out["name"] is None
        assert out["title"] is None
        assert out["company"] is None
        assert out["seniority"] == "unknown"
        assert out["headcount_band"] is None
        assert out["funding_months"] is None
        assert out["email_grade"] == "unknown"
        assert out["route"] == ROUTE_REJECT
