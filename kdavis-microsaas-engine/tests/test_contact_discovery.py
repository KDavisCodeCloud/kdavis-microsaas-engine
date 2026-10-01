"""
tests/test_contact_discovery.py

Free-first contact discovery (Kelvin's decision 5a, 2026-10-01).

WHY IT EXISTS: the 2026-10-01 re-runs found ZERO contacts across both
products while spending Brave queries trying. Domains now resolve 100% of the
time, so contacts are the bottleneck. These sources cost nothing (the JD text
is already in memory) or one GET against an already-verified domain.

The assertions that matter most are the refusals: half a contact (a name with
no stated title) must never be returned, because MKT-O2 hands these fields to
the LLM as asserted fact.
"""

import pytest

from agents.marketing.contact_discovery import (
    TEAM_PAGE_PATHS,
    ContactDiscoveryStats,
    DiscoveredContact,
    discover_contact_free,
    from_company_pages,
    from_jd_text,
)


def page(body, status=200):
    def _get(url, **_kw):
        if url.endswith("/robots.txt"):
            return type("R", (), {"status_code": 200, "text": "User-agent: *\nAllow: /"})()
        return type("R", (), {"status_code": status, "text": body})()
    return _get


def robots(disallow_all=True):
    rules = "User-agent: *\nDisallow: /" if disallow_all else "User-agent: *\nAllow: /"

    def _get(url, **_kw):
        if url.endswith("/robots.txt"):
            return type("R", (), {"status_code": 200, "text": rules})()
        return type("R", (), {"status_code": 200, "text": "<p>Jane Doe - CTO</p>"})()
    return _get


class TestFromJdText:
    """The company wrote this, so there is no inference involved -- the most
    reliable source available, and it costs nothing at all."""

    @pytest.mark.parametrize("jd,name,title", [
        ("You will report to Jane Doe, VP of Engineering, and own Azure.",
         "Jane Doe", "VP of Engineering"),
        ("This role reports to: Marcus Webb, CTO", "Marcus Webb", "CTO"),
        ("You'll report to Priya Raman, Head of Platform.", "Priya Raman", "Head of Platform"),
        ("Reporting directly to Ana Ruiz, Director of Engineering.",
         "Ana Ruiz", "Director of Engineering"),
    ])
    def test_named_hiring_manager_is_found(self, jd, name, title):
        contact = from_jd_text(jd)
        assert contact is not None and contact.is_usable
        assert contact.name == name
        assert contact.title == title
        assert contact.evidence, "evidence must be recorded for auditing"

    @pytest.mark.parametrize("jd", [
        "Our team is great. Apply now.",
        "Reporting to the hiring manager.",
        "You will report to the engineering organisation.",
        "",
        None,
    ])
    def test_nothing_usable_returns_none(self, jd):
        assert from_jd_text(jd) is None

    def test_a_name_with_no_stated_title_is_refused(self):
        """Half a contact is worse than none: MKT-O2 would assert a role the
        JD never stated."""
        assert from_jd_text("You will report to Jane Doe on this team.") is None

    def test_a_recruiter_named_in_the_jd_is_not_the_buyer(self):
        assert from_jd_text("Questions? Contact Dana Smith, Technical Recruiter.") is None


class TestFromCompanyPages:
    def test_finds_a_leader_on_a_team_page(self):
        contact = from_company_pages(
            "northwind.example",
            http_get=page("<h2>Leadership</h2><p>Jane Doe - CTO</p>"))
        assert contact is not None and contact.is_usable
        assert contact.name == "Jane Doe"
        assert contact.title == "CTO"
        assert contact.source.startswith("company_page:")

    def test_title_before_name_also_parses(self):
        contact = from_company_pages(
            "x.example", http_get=page("<p>CTO Marcus Webb</p>"))
        assert contact is not None
        assert contact.name == "Marcus Webb"
        assert contact.title == "CTO"

    def test_page_furniture_is_not_mistaken_for_a_person(self):
        """"Our Team", "Contact Us" and "Privacy Policy" all match the
        capitalised-words shape."""
        contact = from_company_pages(
            "x.example",
            http_get=page("<nav>Our Team | Contact Us | Privacy Policy</nav>"))
        assert contact is None

    def test_robots_txt_disallow_is_honoured(self):
        """Unlike the ATS JSON APIs these are crawled pages, so permission is
        asked. The page here WOULD yield a contact if fetched."""
        stats = ContactDiscoveryStats()
        contact = from_company_pages("x.example", http_get=robots(True), stats=stats)
        assert contact is None
        assert stats.pages_blocked_by_robots > 0
        assert stats.pages_fetched == 0

    def test_robots_txt_allow_permits_the_fetch(self):
        stats = ContactDiscoveryStats()
        contact = from_company_pages("x.example", http_get=robots(False), stats=stats)
        assert contact is not None
        assert stats.pages_fetched > 0

    def test_page_budget_is_bounded(self):
        """A company with none of these pages must not cost eight requests."""
        calls = []

        def counting(url, **kw):
            calls.append(url)
            if url.endswith("/robots.txt"):
                return type("R", (), {"status_code": 404, "text": ""})()
            return type("R", (), {"status_code": 404, "text": ""})()

        from_company_pages("x.example", http_get=counting, max_pages=2)
        non_robots = [u for u in calls if not u.endswith("/robots.txt")]
        assert len(non_robots) <= 2

    def test_http_failure_is_counted_not_raised(self):
        def boom(url, **kw):
            if url.endswith("/robots.txt"):
                return type("R", (), {"status_code": 404, "text": ""})()
            raise OSError("connection reset")

        stats = ContactDiscoveryStats()
        assert from_company_pages("x.example", http_get=boom, stats=stats) is None
        assert stats.failures.get("request_error", 0) > 0

    def test_no_domain_means_no_requests(self):
        calls = []
        from_company_pages(None, http_get=lambda u, **k: calls.append(u))
        assert calls == []

    def test_paths_are_ordered_most_likely_first(self):
        assert TEAM_PAGE_PATHS[0] == "/team"
        assert "/leadership" in TEAM_PAGE_PATHS
        assert "/about" in TEAM_PAGE_PATHS


class TestDiscoverContactFree:
    """Order matters: the JD costs nothing, so it is always tried before any
    HTTP request."""

    def test_jd_wins_and_makes_no_request(self):
        calls = []

        def counting(url, **kw):
            calls.append(url)
            return type("R", (), {"status_code": 200, "text": ""})()

        stats = ContactDiscoveryStats()
        contact = discover_contact_free(
            domain="x.example",
            jd_text="You will report to Jane Doe, VP of Engineering.",
            http_get=counting, stats=stats,
        )
        assert contact.name == "Jane Doe"
        assert calls == [], "a JD hit must cost zero HTTP requests"
        assert stats.jd_hits == 1

    def test_falls_through_to_company_pages(self):
        stats = ContactDiscoveryStats()
        contact = discover_contact_free(
            domain="x.example", jd_text="No manager named here.",
            http_get=page("<p>Marcus Webb, Head of Infrastructure</p>"), stats=stats,
        )
        assert contact is not None
        assert contact.source.startswith("company_page:")
        assert stats.page_hits == 1

    def test_returns_none_so_the_caller_can_decide_about_brave(self):
        stats = ContactDiscoveryStats()
        assert discover_contact_free(
            domain="x.example", jd_text="nothing here",
            http_get=page("<p>nothing useful</p>"), stats=stats,
        ) is None

    def test_stats_are_serialisable_for_funnel_stats(self):
        import json

        stats = ContactDiscoveryStats()
        discover_contact_free(domain=None, jd_text=None, stats=stats)
        json.dumps(stats.as_dict())


class TestDiscoveredContact:
    def test_both_halves_required(self):
        assert DiscoveredContact(name="Jane Doe", title="CTO").is_usable is True
        assert DiscoveredContact(name="Jane Doe").is_usable is False
        assert DiscoveredContact(title="CTO").is_usable is False
        assert DiscoveredContact().is_usable is False


class TestCaseSensitivityOfNames:
    """THE bug from the 2026-10-01 consulting run.

    The combined name+title patterns were compiled with a global
    re.IGNORECASE, which makes `[A-Z][a-z]+` match ANY case -- so prose
    matched the name shape. The run stored contacts literally called
    "you will", "and leadership", "or senior", "of MeatEater", "with co" and
    "Tech Leads", every one of which would have gone into outbound copy as
    the recipient's name.

    The fix wraps only the TITLE in a scoped inline flag `(?i:...)` so the
    name stays case-sensitive. These are the exact strings that got through.
    """

    @pytest.mark.parametrize("text", [
        "you will - CTO",
        "and leadership, CEO",
        "or senior, CEO",
        "of MeatEater, founder",
        "with co, founder",
        "Tech Leads - CTO",
        "of SofterWare, CEO",
    ])
    def test_prose_fragments_are_not_names(self, text):
        from agents.marketing.contact_discovery import _contact_from_text

        assert _contact_from_text(text, "t") is None, f"{text!r} is not a person"

    @pytest.mark.parametrize("text", [
        "Strutt Co - CTO",
        "Acme Labs - CTO",
        "Northwind Systems, CEO",
    ])
    def test_company_names_are_not_people(self, text):
        """Reuses lead_qualification._COMPANY_SUFFIX_RE rather than growing a
        second list that could disagree with it."""
        from agents.marketing.contact_discovery import _contact_from_text

        assert _contact_from_text(text, "t") is None

    @pytest.mark.parametrize("text,name", [
        ("Jane Doe - CTO", "Jane Doe"),
        ("Marcus Webb, VP of Engineering", "Marcus Webb"),
        ("CTO Priya Raman", "Priya Raman"),
        ("Maria del Carmen, Head of Platform", "Maria del Carmen"),
    ])
    def test_real_names_still_parse(self, text, name):
        from agents.marketing.contact_discovery import _contact_from_text

        result = _contact_from_text(text, "t")
        assert result is not None, f"{text!r} should still parse"
        assert result.name == name

    def test_titles_remain_case_insensitive(self):
        """Only the NAME became case-sensitive; a lowercase title must still
        match, because real pages write "cto" and "Head of engineering"."""
        from agents.marketing.contact_discovery import _contact_from_text

        assert _contact_from_text("Jane Doe - cto", "t") is not None
        assert _contact_from_text("Marcus Webb, head of engineering", "t") is not None

    def test_jd_prose_around_the_name_stays_case_insensitive(self):
        """"You will report to" / "you will report to" must both work, while
        the captured name stays case-sensitive."""
        assert from_jd_text("You will report to Jane Doe, VP of Engineering.") is not None
        assert from_jd_text("you will report to Jane Doe, VP of Engineering.") is not None
        assert from_jd_text("You will report to the team, VP of Engineering.") is None
