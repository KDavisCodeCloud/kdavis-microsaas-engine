"""
tests/test_ats_boards.py

Unit coverage for scrapers/ats_boards.py (Scraper v2 company-first
sourcing). No network: http_get is injected and sleep is stubbed, so the
real normalisation logic runs against recorded-shape payloads.

The point of this module is Brave-quota economics -- one Brave query
discovers a board token, then the ATS's own JSON API returns that
company's entire open-role list for free. The tests that matter most are
therefore the ones proving a wrong token can never be silently derived
(fetching another company's jobs) and that one broken board never aborts
a run.
"""

from datetime import date, datetime, timezone

import pytest

from scrapers.ats_boards import (
    ATS_HOST_MAP,
    MAX_POSTINGS_PER_BOARD,
    PROVIDERS,
    AtsBoardClient,
    AtsPosting,
    BoardRef,
    board_ref_from_url,
    company_domain_from_postings,
    company_name_from_token,
    matching_postings,
)


class FakeResponse:
    def __init__(self, payload=None, status_code=200, raise_json=False):
        self._payload = payload
        self.status_code = status_code
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._payload


def fake_get(payload=None, status_code=200, raise_json=False, raise_request=False):
    calls = []

    def _get(url, **kwargs):
        calls.append((url, kwargs))
        if raise_request:
            raise OSError("connection reset")
        return FakeResponse(payload, status_code, raise_json)

    _get.calls = calls
    return _get


def client(get):
    return AtsBoardClient(http_get=get, sleep=lambda _s: None)


# ── Board token discovery ────────────────────────────────────────────────

class TestBoardTokenDiscovery:
    @pytest.mark.parametrize("url,provider,token", [
        ("https://boards.greenhouse.io/northwind/jobs/4512", "greenhouse", "northwind"),
        ("https://job-boards.greenhouse.io/acmecorp/jobs/99", "greenhouse", "acmecorp"),
        ("https://jobs.lever.co/vectorlabs/8f3a-1122", "lever", "vectorlabs"),
        ("https://jobs.ashbyhq.com/quanta/abcd-1234", "ashby", "quanta"),
        ("https://apply.workable.com/helios/j/A1B2C3/", "workable", "helios"),
        ("https://www.boards.greenhouse.io/northwind/jobs/1", "greenhouse", "northwind"),
    ])
    def test_extracts_provider_and_token(self, url, provider, token):
        ref = board_ref_from_url(url)
        assert ref == BoardRef(provider=provider, token=token)
        assert ref.provider in PROVIDERS

    @pytest.mark.parametrize("url", [
        "https://linkedin.com/jobs/platform-engineer",
        "https://acme.com/careers/platform-engineer",
        "https://boards.greenhouse.io/",           # no token segment
        "https://boards.greenhouse.io/jobs/123",   # reserved first segment
        "https://jobs.lever.co/search",            # reserved
        "",
    ])
    def test_returns_none_rather_than_guessing(self, url):
        """A wrong token silently fetches ANOTHER company's jobs, so this
        is deliberately strict: no token is always better than a guess."""
        assert board_ref_from_url(url) is None

    def test_cache_key_is_provider_scoped(self):
        """Two providers can legitimately use the same token string."""
        a = BoardRef("greenhouse", "acme").cache_key
        b = BoardRef("lever", "acme").cache_key
        assert a != b

    @pytest.mark.parametrize("token,name", [
        ("acme-corp", "Acme Corp"),
        ("northwind_systems", "Northwind Systems"),
        ("helios", "Helios"),
    ])
    def test_company_name_from_token(self, token, name):
        assert company_name_from_token(token) == name

    def test_every_mapped_host_declares_a_known_provider(self):
        for provider, idx in ATS_HOST_MAP.values():
            assert provider in PROVIDERS
            assert idx >= 0


# ── Per-provider normalisation ───────────────────────────────────────────

class TestGreenhouse:
    PAYLOAD = {
        "meta": {"company_name": "Northwind Systems"},
        "jobs": [
            {"title": "Senior Platform Engineer", "absolute_url": "https://boards.greenhouse.io/northwind/jobs/1",
             "location": {"name": "Austin, TX"}, "updated_at": "2026-09-20T10:00:00Z",
             "content": "<p>You will own our <b>Terraform</b> and Kubernetes estate.</p>"},
            {"title": "Account Executive", "absolute_url": "https://boards.greenhouse.io/northwind/jobs/2",
             "location": {"name": "Remote"}, "updated_at": "2026-09-01T10:00:00Z", "content": "Sell things."},
        ],
    }

    def test_normalises_all_fields(self):
        c = client(fake_get(self.PAYLOAD))
        posts = c.fetch_board(BoardRef("greenhouse", "northwind"))
        assert len(posts) == 2
        p = posts[0]
        assert p.company == "Northwind Systems"
        assert p.title == "Senior Platform Engineer"
        assert p.location == "Austin, TX"
        assert p.posted_at == date(2026, 9, 20)
        assert "Terraform" in p.description_text
        assert "<b>" not in p.description_text, "HTML must be stripped for keyword extraction"

    def test_uses_documented_public_endpoint(self):
        get = fake_get(self.PAYLOAD)
        client(get).fetch_board(BoardRef("greenhouse", "northwind"))
        url = get.calls[0][0]
        assert url == "https://boards-api.greenhouse.io/v1/boards/northwind/jobs?content=true"

    def test_stats_record_the_fetch(self):
        c = client(fake_get(self.PAYLOAD))
        c.fetch_board(BoardRef("greenhouse", "northwind"))
        assert c.stats.boards_fetched == 1
        assert c.stats.postings_returned == 2
        assert c.stats.boards_failed == 0


class TestLever:
    PAYLOAD = [
        {"text": "Staff DevOps Engineer", "hostedUrl": "https://jobs.lever.co/vectorlabs/abc",
         "categories": {"location": "Denver, CO"}, "createdAt": 1758326400000,
         "descriptionPlain": "AWS, Terraform, EKS."},
    ]

    def test_normalises_list_payload_and_ms_epoch(self):
        c = client(fake_get(self.PAYLOAD))
        posts = c.fetch_board(BoardRef("lever", "vectorlabs"))
        assert len(posts) == 1
        p = posts[0]
        assert p.title == "Staff DevOps Engineer"
        assert p.location == "Denver, CO"
        assert p.posted_at == datetime.fromtimestamp(1758326400, tz=timezone.utc).date()

    def test_company_falls_back_to_token_when_api_omits_it(self):
        """Lever's feed carries no company name. Deriving it from the
        token is the documented fallback -- but it must be a real cased
        name, never the raw slug."""
        posts = client(fake_get(self.PAYLOAD)).fetch_board(BoardRef("lever", "vector-labs"))
        assert posts[0].company == "Vector Labs"


class TestAshby:
    PAYLOAD = {"jobs": [
        {"title": "Cloud Infrastructure Lead", "jobUrl": "https://jobs.ashbyhq.com/quanta/x",
         "location": "Remote - US", "publishedAt": "2026-09-25", "descriptionPlain": "Azure and Bicep."},
    ]}

    def test_normalises_iso_date_only(self):
        posts = client(fake_get(self.PAYLOAD)).fetch_board(BoardRef("ashby", "quanta"))
        assert posts[0].posted_at == date(2026, 9, 25)
        assert posts[0].title == "Cloud Infrastructure Lead"


class TestWorkable:
    PAYLOAD = {"name": "Helios Energy", "jobs": [
        {"title": "SRE Manager", "url": "https://helios.example/careers/sre",
         "location": "Boston, MA", "published_on": "2026-09-18", "description": "Kubernetes, Prometheus."},
    ]}

    def test_normalises_and_prefers_api_company_name(self):
        posts = client(fake_get(self.PAYLOAD)).fetch_board(BoardRef("workable", "helios"))
        assert posts[0].company == "Helios Energy", "the API's real name beats the token-derived guess"


class TestDateHandling:
    def test_undated_posting_is_none_never_today(self):
        """An undated posting must not read as fresh -- that would hand
        intent_score a signal the data never supported."""
        payload = {"meta": {"company_name": "X"}, "jobs": [{"title": "Platform Engineer", "absolute_url": "u"}]}
        posts = client(fake_get(payload)).fetch_board(BoardRef("greenhouse", "x"))
        assert posts[0].posted_at is None
        assert posts[0].age_days is None

    def test_garbage_date_is_none_not_a_crash(self):
        payload = {"meta": {}, "jobs": [{"title": "Platform Engineer", "updated_at": "not-a-date"}]}
        posts = client(fake_get(payload)).fetch_board(BoardRef("greenhouse", "x"))
        assert posts[0].posted_at is None


# ── Failure isolation ────────────────────────────────────────────────────

class TestFailuresAreNonFatal:
    """One company's board being down must never abort a scout run -- but
    it must never be silent either."""

    def test_404_returns_empty_and_counts(self):
        c = client(fake_get(None, status_code=404))
        assert c.fetch_board(BoardRef("greenhouse", "gone")) == []
        assert c.stats.boards_failed == 1
        assert c.stats.http_errors["http_404"] == 1

    def test_network_error_returns_empty_and_counts(self):
        c = client(fake_get(raise_request=True))
        assert c.fetch_board(BoardRef("lever", "x")) == []
        assert c.stats.boards_failed == 1
        assert c.stats.http_errors["request_error"] == 1

    def test_non_json_body_returns_empty_and_counts(self):
        c = client(fake_get(raise_json=True))
        assert c.fetch_board(BoardRef("ashby", "x")) == []
        assert c.stats.http_errors["bad_json"] == 1

    def test_unknown_provider_is_refused_not_requested(self):
        get = fake_get({})
        c = client(get)
        assert c.fetch_board(BoardRef("bamboohr", "x")) == []
        assert get.calls == [], "an unknown provider must not produce an HTTP call"
        assert c.stats.boards_failed == 1

    def test_empty_board_is_distinguished_from_a_failure(self):
        """A company with zero open roles is a successful fetch with no
        postings -- conflating it with a 404 hides a real signal (they
        stopped hiring)."""
        c = client(fake_get({"meta": {}, "jobs": []}))
        assert c.fetch_board(BoardRef("greenhouse", "quiet")) == []
        assert c.stats.boards_fetched == 1
        assert c.stats.boards_empty == 1
        assert c.stats.boards_failed == 0

    def test_oversized_board_is_capped(self):
        """A board with 800 roles is an enterprise/staffing operation, not
        a 20-300 headcount prospect."""
        payload = {"meta": {}, "jobs": [{"title": f"Role {i}"} for i in range(MAX_POSTINGS_PER_BOARD + 50)]}
        posts = client(fake_get(payload)).fetch_board(BoardRef("greenhouse", "huge"))
        assert len(posts) == MAX_POSTINGS_PER_BOARD

    def test_stats_as_dict_is_serialisable_for_funnel_stats(self):
        c = client(fake_get({"meta": {}, "jobs": []}))
        c.fetch_board(BoardRef("greenhouse", "x"))
        d = c.stats.as_dict()
        assert set(d) == {"boards_fetched", "boards_failed", "boards_empty", "postings_returned", "http_errors"}


class TestFetchBoards:
    def test_dedupes_by_cache_key(self):
        get = fake_get({"meta": {}, "jobs": []})
        c = client(get)
        refs = [BoardRef("greenhouse", "a"), BoardRef("greenhouse", "a"), BoardRef("lever", "a")]
        out = c.fetch_boards(refs)
        assert set(out) == {"greenhouse:a", "lever:a"}
        assert len(get.calls) == 2, "a repeated board must not be re-fetched"


# ── Relevance filtering ──────────────────────────────────────────────────

class TestMatchingPostings:
    POSTS = [
        AtsPosting("greenhouse", "x", title="Senior Platform Engineer", posted_at=date.today()),
        AtsPosting("greenhouse", "x", title="Account Executive", posted_at=date.today()),
        AtsPosting("greenhouse", "x", title="Site Reliability Engineer", posted_at=None),
    ]

    def test_matches_on_title_only(self):
        """Description matching is how 'we use Terraform to manage our
        sales CRM' turns a sales role into a platform signal."""
        posts = [AtsPosting("greenhouse", "x", title="Account Executive",
                            description_text="You will use Terraform and Kubernetes dashboards.")]
        assert matching_postings(posts, ["platform engineer", "kubernetes"]) == []

    def test_word_boundary_anchored(self):
        posts = [AtsPosting("greenhouse", "x", title="SDRAM Firmware Engineer")]
        assert matching_postings(posts, ["SDR"]) == []

    def test_matches_relevant_titles(self):
        out = matching_postings(self.POSTS, ["platform engineer", "site reliability engineer"])
        assert [p.title for p in out] == ["Senior Platform Engineer", "Site Reliability Engineer"]

    def test_empty_keywords_match_nothing(self):
        assert matching_postings(self.POSTS, []) == []
        assert matching_postings(self.POSTS, ["", "  "]) == []

    def test_undated_posting_dropped_only_when_a_freshness_filter_is_set(self):
        """An unknown date cannot honestly satisfy a 'last 30 days'
        filter, but with no filter it is still a real posting."""
        kw = ["site reliability engineer"]
        assert len(matching_postings(self.POSTS, kw, max_age_days=None)) == 1
        assert matching_postings(self.POSTS, kw, max_age_days=30) == []


class TestCompanyDomain:
    def test_finds_company_domain_off_the_ats_host(self):
        posts = [AtsPosting("workable", "helios", url="https://helios.example/careers/sre")]
        assert company_domain_from_postings(posts) == "helios.example"

    def test_ats_only_urls_yield_no_domain(self):
        """No domain is the signal that routes a lead to the manual
        LinkedIn track -- it must not be faked from the ATS host."""
        posts = [AtsPosting("greenhouse", "x", url="https://boards.greenhouse.io/x/jobs/1")]
        assert company_domain_from_postings(posts) is None
