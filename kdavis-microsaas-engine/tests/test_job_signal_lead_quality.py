"""
tests/test_job_signal_lead_quality.py

Regression coverage for the 2026-09-29 bad-lead batch: both job-signal
branches wrote leads whose "company" was a job BOARD (linkedin.com,
ziprecruiter.com, index.dev), which then produced real HITL drafts
addressed to ZipRecruiter. Root cause was _company_name_from_result
falling back to the result's DOMAIN whenever it couldn't parse a company
out of the title -- and an aggregator category page never parses.

Three gates, asserted here against the exact four URLs that actually
shipped bad leads, plus the ATS-hosted shapes that must keep working.
"""

import agents.marketing.mkt_lead_finder as lf
from agents.marketing.mkt_lead_finder import (
    ATS_SITE_QUERY_TEMPLATES,
    _ats_templates_for,
    _company_from_ats_url,
    _company_from_posting,
    _is_aggregator,
    find_cloud_decoded_job_signals,
    find_job_posting_signals,
)


class _FakeBraveSearchScraper:
    def __init__(self, items_by_query=None, query_count=0, **kwargs):
        self.api_key = "fake-key"
        self.query_count = query_count
        self._items_by_query = items_by_query or {}
        self.queries = []

    def _search(self, query):
        self.queries.append(query)
        self.query_count += 1
        return self._items_by_query.get(query, [])


def _item(link, title="Acme Corp - Careers", snippet="Posted 1 day ago"):
    return {"link": link, "title": title, "snippet": snippet}


ICP = {
    "job_titles": ["CTO"],
    "locations": ["United States"],
    "search_templates": ['"{title}" hiring "cloud architect" {location}'],
    "max_company_size": 200,
}


# ── Gate 1: aggregator blocklist ────────────────────────────────────────

class TestAggregatorBlocklist:
    def test_the_four_urls_that_actually_shipped_bad_leads_are_all_blocked(self):
        """Verbatim from mse_leads on 2026-09-29 -- every one produced a
        HITL draft addressed to a job board."""
        shipped = [
            ("https://www.linkedin.com/jobs/chief-cloud-architect-jobs", "linkedin.com"),
            ("https://www.index.dev/job-description/head-of-platform", "index.dev"),
            ("https://www.ziprecruiter.com/Jobs/Data-Platform-Engineering-Director", "ziprecruiter.com"),
            ("https://www.linkedin.com/jobs/platform-development-jobs", "linkedin.com"),
        ]
        for link, domain in shipped:
            assert _is_aggregator(link, domain) is True, f"{link} must be blocked"

    def test_every_named_blocklist_domain_is_blocked(self):
        for domain in [
            "linkedin.com", "ziprecruiter.com", "indeed.com", "glassdoor.com",
            "monster.com", "simplyhired.com", "index.dev", "dice.com",
            "wellfound.com", "builtin.com",
        ]:
            assert _is_aggregator(f"https://{domain}/something", domain) is True, domain

    def test_builtin_city_subdomains_are_blocked(self):
        """builtin runs builtinnyc.com / builtinaustin.com etc."""
        assert _is_aggregator("https://www.builtinnyc.com/job/x/94138", "builtinnyc.com") is True

    def test_company_own_posting_is_not_blocked(self):
        assert _is_aggregator("https://example.com/jobs/1", "example.com") is False
        assert _is_aggregator("https://acme.com/careers/senior-sre", "acme.com") is False

    def test_ats_hosts_are_never_blocked(self):
        for link, domain in [
            ("https://boards.greenhouse.io/acme/jobs/123", "boards.greenhouse.io"),
            ("https://job-boards.greenhouse.io/acme/jobs/123", "job-boards.greenhouse.io"),
            ("https://jobs.lever.co/acme/uuid", "jobs.lever.co"),
            ("https://jobs.ashbyhq.com/acme/uuid", "jobs.ashbyhq.com"),
            ("https://apply.workable.com/acme/j/ABC123", "apply.workable.com"),
        ]:
            assert _is_aggregator(link, domain) is False, link

    def test_category_index_pages_are_blocked_but_numbered_postings_are_not(self):
        assert _is_aggregator("https://acme.com/jobs", "acme.com") is True
        assert _is_aggregator("https://acme.com/jobs/platform-engineer-jobs", "acme.com") is True
        # a real posting with an id must survive
        assert _is_aggregator("https://acme.com/jobs/4821", "acme.com") is False


# ── Gate 2: ATS-derived company names ───────────────────────────────────

class TestAtsCompanyExtraction:
    def test_company_comes_from_the_ats_url_slug(self):
        assert _company_from_ats_url("https://boards.greenhouse.io/acme-corp/jobs/1", "boards.greenhouse.io") == "Acme Corp"
        assert _company_from_ats_url("https://jobs.lever.co/stripe/uuid", "jobs.lever.co") == "Stripe"
        assert _company_from_ats_url("https://jobs.ashbyhq.com/linear/uuid", "jobs.ashbyhq.com") == "Linear"
        assert _company_from_ats_url("https://apply.workable.com/acme/j/ABC", "apply.workable.com") == "Acme"

    def test_non_ats_host_yields_nothing(self):
        assert _company_from_ats_url("https://example.com/jobs/1", "example.com") is None

    def test_ats_url_slug_wins_over_the_result_title(self):
        item = _item("x", title="Some Unrelated Listing Title")
        got = _company_from_posting(item, "https://jobs.lever.co/stripe/uuid", "jobs.lever.co")
        assert got == "Stripe"

    def test_ats_templates_are_generated_for_every_host_and_keyword(self):
        tmpl = _ats_templates_for(["SRE", "platform engineer"])
        assert len(tmpl) == 2 * len(ATS_SITE_QUERY_TEMPLATES)
        assert 'site:boards.greenhouse.io "SRE"' in tmpl
        assert 'site:jobs.lever.co "platform engineer"' in tmpl


# ── Gate 3: no company name -> no lead ──────────────────────────────────

class TestCompanyNameRequired:
    def test_unparseable_aggregator_title_yields_no_company(self):
        item = _item("x", title="$141k-$253k Data Platform Engineering Director Jobs")
        assert _company_from_posting(item, "https://example.com/x", "example.com") is None

    def test_never_falls_back_to_the_domain(self):
        """The exact bug: domain silently became the company name."""
        item = _item("x", title="No Separator Here")
        got = _company_from_posting(item, "https://ziprecruiter.com/x", "ziprecruiter.com")
        assert got != "ziprecruiter.com"
        assert got is None

    def test_leading_segment_convention_preserved(self):
        assert _company_from_posting(_item("x", title="Acme Corp - Careers"), "https://e.com/x", "e.com") == "Acme Corp"
        assert _company_from_posting(_item("x", title="Stripe is hiring a Platform Engineer"), "https://e.com/x", "e.com") == "Stripe"


# ── End-to-end through both branches ────────────────────────────────────

class TestConsultingBranchDropsAggregators:
    def test_aggregator_result_is_dropped_and_counted(self, monkeypatch):
        query = '"CTO" hiring "cloud architect" United States'
        item = _item("https://www.ziprecruiter.com/Jobs/Data-Platform-Engineering-Director",
                     title="$141k Data Platform Engineering Director Jobs")
        scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
        monkeypatch.setattr(lf, "BraveSearchScraper", lambda **kw: scraper)

        stats = {}
        signals = find_job_posting_signals("prod-1", ICP, _stats=stats)

        assert signals == []
        assert stats["dropped_aggregator"] == 1

    def test_ats_posting_is_kept_with_real_company(self, monkeypatch):
        ats_query = 'site:boards.greenhouse.io "cloud architect"'
        item = _item("https://boards.greenhouse.io/acme-corp/jobs/99", title="Cloud Architect")
        scraper = _FakeBraveSearchScraper(items_by_query={ats_query: [item]})
        monkeypatch.setattr(lf, "BraveSearchScraper", lambda **kw: scraper)

        signals = find_job_posting_signals("prod-1", ICP)

        assert len(signals) == 1
        assert signals[0]["company"] == "Acme Corp"
        assert signals[0]["domain"] == "boards.greenhouse.io"

    def test_ats_queries_run_before_open_web_queries(self, monkeypatch):
        scraper = _FakeBraveSearchScraper(items_by_query={})
        monkeypatch.setattr(lf, "BraveSearchScraper", lambda **kw: scraper)
        find_job_posting_signals("prod-1", ICP)
        assert scraper.queries[0].startswith("site:"), scraper.queries[0]


class TestCloudDecodedBranchDropsAggregators:
    def test_aggregator_result_is_dropped_and_counted(self, monkeypatch):
        query = '"SRE" hiring United States'
        item = _item("https://www.linkedin.com/jobs/platform-development-jobs", title="Platform Development Jobs")
        scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
        monkeypatch.setattr(lf, "BraveSearchScraper", lambda **kw: scraper)
        monkeypatch.setattr(lf, "_fetch_job_posting_text", lambda url, http_get=None: None)

        stats = {}
        signals = find_cloud_decoded_job_signals(_stats=stats, fetch_jd_text=False)

        assert signals == []
        assert stats["dropped_aggregator"] == 1

    def test_ats_posting_is_kept_with_real_company(self, monkeypatch):
        ats_query = 'site:jobs.lever.co "SRE"'
        item = _item("https://jobs.lever.co/stripe/uuid-1", title="Site Reliability Engineer")
        scraper = _FakeBraveSearchScraper(items_by_query={ats_query: [item]})
        monkeypatch.setattr(lf, "BraveSearchScraper", lambda **kw: scraper)
        monkeypatch.setattr(lf, "_fetch_job_posting_text", lambda url, http_get=None: None)

        signals = find_cloud_decoded_job_signals(fetch_jd_text=False)

        assert len(signals) == 1
        assert signals[0]["company"] == "Stripe"

    def test_no_company_result_is_dropped_and_counted(self, monkeypatch):
        query = '"SRE" hiring United States'
        item = _item("https://example.com/careers/1", title="Site Reliability Engineer")
        scraper = _FakeBraveSearchScraper(items_by_query={query: [item]})
        monkeypatch.setattr(lf, "BraveSearchScraper", lambda **kw: scraper)
        monkeypatch.setattr(lf, "_fetch_job_posting_text", lambda url, http_get=None: None)

        stats = {}
        signals = find_cloud_decoded_job_signals(_stats=stats, fetch_jd_text=False)

        assert signals == []
        assert stats["dropped_no_company"] == 1
