"""
tests/test_domain_resolution.py

Mandatory company-domain resolution (Kelvin's decision 5, 2026-10-01).

WHY THIS IS THE HIGHEST-STAKES MODULE IN THE V2 BUILD. A domain here becomes
an email address downstream (core/email_finder builds pattern candidates
from it), so a WRONG domain does not merely mis-tag a row -- it produces real
outbound mail to a real stranger at an unrelated company. The tests that
matter most are therefore the ones proving a guess is refused:
step (c) constructs "token + .com" and must reject it unless DNS resolves
AND the homepage identifies that company.

No network: http_get and dns_resolves are injected throughout.
"""

import pytest

from agents.marketing.domain_resolution import (
    MAX_BRAVE_DOMAIN_QUERIES,
    DomainResolution,
    from_brave,
    from_constructed,
    from_jd_text,
    from_postings,
    resolve_domain,
)


class _Posting:
    def __init__(self, url):
        self.url = url


def homepage(title_text, status=200):
    def _get(url, **_kw):
        return type("R", (), {"status_code": status,
                              "text": f"<html><head><title>{title_text}</title></head></html>"})()
    return _get


def no_homepage(*_a, **_k):
    raise OSError("connection refused")


yes_dns = lambda _h: True
no_dns = lambda _h: False


class FakeScraper:
    api_key = "k"

    def __init__(self, items):
        self.items = items
        self.queries = []

    def _search(self, query):
        self.queries.append(query)
        return self.items


# ── (a) JD text ──────────────────────────────────────────────────────────

class TestFromJdText:
    def test_finds_a_company_url(self):
        jd = "Learn more about us at https://northwind.example/about before applying."
        assert from_jd_text(jd, "Northwind Systems") == ("northwind.example", "jd_url:northwind.example")

    def test_finds_a_bare_domain(self):
        assert from_jd_text("Visit northwind.io for details.", "Northwind")[0] == "northwind.io"

    def test_ignores_the_ats_and_social_hosts(self):
        jd = ("Apply at https://boards.greenhouse.io/northwind/jobs/1 or follow us on "
              "https://linkedin.com/company/northwind and https://github.com/northwind")
        assert from_jd_text(jd, "Northwind") is None

    def test_unrelated_third_party_link_is_not_claimed_as_the_company_site(self):
        """A JD routinely links to a benefits provider, a conference or a blog.
        Taking the first URL in the text would attach those to the lead as
        its website."""
        jd = "Our benefits are administered by https://somebenefitsvendor.example/plans."
        assert from_jd_text(jd, "Northwind Systems") is None

    def test_name_resembling_host_is_preferred_over_an_earlier_unrelated_one(self):
        jd = ("Benefits via https://benefitsco.example . Company site: "
              "https://northwind.example .")
        assert from_jd_text(jd, "Northwind Systems")[0] == "northwind.example"

    def test_empty_input(self):
        assert from_jd_text(None, "Acme") is None
        assert from_jd_text("", "Acme") is None


# ── (b) ATS posting links ────────────────────────────────────────────────

class TestFromPostings:
    def test_finds_a_company_hosted_posting_url(self):
        postings = [_Posting("https://boards.greenhouse.io/x/jobs/1"),
                    _Posting("https://helios.example/careers/sre")]
        assert from_postings(postings) == ("helios.example", "posting_url:helios.example")

    def test_ats_only_urls_yield_nothing(self):
        assert from_postings([_Posting("https://job-boards.greenhouse.io/x/jobs/1")]) is None

    def test_tolerates_missing_urls(self):
        assert from_postings([_Posting(None), _Posting("")]) is None


# ── (c) constructed + verified -- the dangerous one ──────────────────────

class TestFromConstructed:
    def test_accepts_only_when_dns_and_homepage_both_confirm(self):
        got = from_constructed("northwind", "Northwind Systems",
                               http_get=homepage("Northwind Systems | Home"), dns_resolves=yes_dns)
        assert got is not None
        assert got[0] == "northwind.com"
        assert "constructed+verified" in got[1]

    def test_refuses_when_dns_does_not_resolve(self):
        assert from_constructed("northwind", "Northwind Systems",
                                http_get=homepage("Northwind Systems"), dns_resolves=no_dns) is None

    def test_refuses_a_parked_or_squatted_domain(self):
        """DNS resolves, but the homepage is somebody else. This is the exact
        case that makes "token + .com" a guess rather than a resolution --
        accepting it would generate outbound mail to an unrelated company."""
        assert from_constructed("northwind", "Northwind Systems",
                                http_get=homepage("Buy this domain!"), dns_resolves=yes_dns) is None

    def test_refuses_an_unrelated_company_with_the_same_short_name(self):
        assert from_constructed("clutch", "Clutch Technologies",
                                http_get=homepage("Clutch Sports Bar &amp; Grill"),
                                dns_resolves=yes_dns) is None

    def test_tries_each_tld_in_order(self):
        seen = []

        def get(url, **_kw):
            seen.append(url)
            ok = "northwind.ai" in url
            return type("R", (), {"status_code": 200,
                                  "text": "<title>Northwind Systems</title>" if ok else "<title>nope</title>"})()

        got = from_constructed("northwind", "Northwind Systems", http_get=get, dns_resolves=yes_dns)
        assert got[0] == "northwind.ai"
        assert any(".com" in u for u in seen), "cheaper TLDs must be tried first"

    def test_http_failure_is_not_a_resolution(self):
        assert from_constructed("northwind", "Northwind", http_get=no_homepage, dns_resolves=yes_dns) is None

    def test_non_200_is_not_a_resolution(self):
        assert from_constructed("northwind", "Northwind",
                                http_get=homepage("Northwind", status=503), dns_resolves=yes_dns) is None

    def test_no_token_or_no_company_yields_nothing(self):
        assert from_constructed(None, "Acme", http_get=homepage("Acme"), dns_resolves=yes_dns) is None
        assert from_constructed("acme", None, http_get=homepage("Acme"), dns_resolves=yes_dns) is None


# ── (d) Brave fallback ───────────────────────────────────────────────────

class TestFromBrave:
    def test_accepts_a_name_matching_result(self):
        scraper = FakeScraper([{"link": "https://northwind.example/"}])
        assert from_brave("Northwind Systems", scraper)[0] == "northwind.example"
        assert scraper.queries == ['"Northwind Systems" official website']

    def test_rejects_a_directory_or_competitor_result(self):
        """Brave will happily return a listing site or a rival for an
        ambiguous name."""
        scraper = FakeScraper([{"link": "https://crunchbase.com/org/northwind"},
                               {"link": "https://someoneelse.example/"}])
        assert from_brave("Northwind Systems", scraper) is None

    def test_counts_its_query(self):
        stats = {}
        from_brave("Acme", FakeScraper([]), stats=stats)
        assert stats["brave_domain_queries"] == 1

    def test_no_api_key_spends_nothing(self):
        class NoKey(FakeScraper):
            api_key = None

        scraper = NoKey([{"link": "https://acme.example/"}])
        assert from_brave("Acme", scraper) is None
        assert scraper.queries == []


# ── Orchestration ────────────────────────────────────────────────────────

class TestResolveDomainChain:
    def _kw(self, **over):
        kw = dict(http_get=homepage("Northwind Systems"), dns_resolves=yes_dns)
        kw.update(over)
        return kw

    def test_known_domain_short_circuits_everything(self):
        scraper = FakeScraper([])
        result = resolve_domain("Northwind Systems", known_domain="northwind.example",
                               scraper=scraper, brave_budget_remaining=5, **self._kw())
        assert result.domain == "northwind.example"
        assert result.method == "already_known"
        assert scraper.queries == [], "no Brave query for a domain we already have"

    def test_jd_text_wins_over_later_steps(self):
        result = resolve_domain("Northwind Systems", token="northwind",
                               jd_text="See https://northwind.example/careers", **self._kw())
        assert result.method == "jd_text"

    def test_falls_through_to_posting_url(self):
        result = resolve_domain("Helios Energy", token="helios", jd_text="no links here",
                               postings=[_Posting("https://helios.example/c/1")],
                               **self._kw(http_get=homepage("Helios Energy")))
        assert result.method == "posting_url"
        assert result.domain == "helios.example"

    def test_falls_through_to_constructed(self):
        result = resolve_domain("Northwind Systems", token="northwind", jd_text="", **self._kw())
        assert result.method == "constructed_verified"
        assert result.domain == "northwind.com"

    def test_falls_through_to_brave_only_when_budget_remains(self):
        scraper = FakeScraper([{"link": "https://northwind.example/"}])
        kw = self._kw(http_get=homepage("unrelated"), dns_resolves=no_dns)
        result = resolve_domain("Northwind Systems", token="northwind", jd_text="",
                               scraper=scraper, brave_budget_remaining=1, **kw)
        assert result.method == "brave"
        assert result.brave_queries_used == 1

    def test_no_brave_budget_means_no_brave_query(self):
        scraper = FakeScraper([{"link": "https://northwind.example/"}])
        kw = self._kw(http_get=homepage("unrelated"), dns_resolves=no_dns)
        result = resolve_domain("Northwind Systems", token="northwind", jd_text="",
                               scraper=scraper, brave_budget_remaining=0, **kw)
        assert result.resolved is False
        assert scraper.queries == []

    def test_unresolvable_returns_none_never_a_guess(self):
        """A lead with no domain routes to the manual LinkedIn track. It must
        never carry a plausible-looking fabricated one."""
        kw = self._kw(http_get=homepage("somebody else entirely"), dns_resolves=yes_dns)
        result = resolve_domain("Northwind Systems", token="northwind", jd_text="", **kw)
        assert result.domain is None
        assert result.resolved is False
        assert result.attempts == ["jd_text", "posting_url", "constructed"]

    def test_attempts_are_recorded_for_auditing(self):
        result = resolve_domain("Northwind Systems", token="northwind", jd_text="", **self._kw())
        assert "jd_text" in result.attempts
        assert "constructed" in result.attempts

    def test_method_is_always_set_when_resolved(self):
        result = resolve_domain("Northwind Systems", token="northwind", jd_text="", **self._kw())
        assert result.resolved
        assert result.method in ("jd_text", "posting_url", "constructed_verified", "brave", "already_known")

    def test_budget_constant_matches_the_spec(self):
        assert MAX_BRAVE_DOMAIN_QUERIES == 10
