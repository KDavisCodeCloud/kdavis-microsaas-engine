"""
tests/test_company_first_sourcing.py

Coverage for agents/marketing/company_first_sourcing.py (Scraper v2
primary path). No network, no DB: a fake scraper records every query, and
AtsBoardClient takes an injected http_get.

The two classes that encode Kelvin's explicit 2026-10-01 constraints:
  TestLinkedInIsNotATopLevelSource -- `site:linkedin.com/in` must appear
    ONLY in the per-company decision-maker step, never in discovery.
  TestQueryBudget -- a run must be holdable to ≤40 Brave queries.
"""

from datetime import date, datetime

import pytest

from agents.marketing.company_first_sourcing import (
    ATS_DISCOVERY_TEMPLATES,
    DECISION_MAKER_BUDGET_SHARE,
    CompanyCandidate,
    FunnelStats,
    ScoringConfig,
    build_candidates,
    discover_board_refs,
    find_company_first_signals,
    find_decision_maker,
    MSE_LEADS_WRITABLE_COLUMNS,
    load_cached_board_refs,
    parse_decision_maker_title,
    qualify_candidate,
    split_contact_name,
    to_mse_lead_row,
)
from scrapers.ats_boards import AtsBoardClient, AtsPosting, BoardRef


class FakeScraper:
    """Records every query. Returns canned items per query substring."""

    api_key = "test-key"

    def __init__(self, responses=None, default=None):
        self.responses = responses or {}
        self.default = default if default is not None else []
        self.queries = []

    def _search(self, query):
        self.queries.append(query)
        for needle, items in self.responses.items():
            if needle in query:
                return items
        return list(self.default)


NO_SLEEP = lambda _s: None


def ats_result(url, title="Platform Engineer", snippet=""):
    return {"link": url, "title": title, "snippet": snippet}


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self.upserts = []

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def upsert(self, row, **_k):
        self.upserts.append(row)
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class FakeDb:
    def __init__(self, rows=None):
        self.query = FakeQuery(rows or [])

    def table(self, _name):
        return self.query


def board_client(payload_by_token):
    def _get(url, **_kw):
        token = url.rstrip("/").split("/")[-1].split("?")[0]
        for key, payload in payload_by_token.items():
            if key in url:
                return type("R", (), {"status_code": 200, "json": lambda self, p=payload: p})()
        return type("R", (), {"status_code": 404, "json": lambda self: {}})()

    return AtsBoardClient(http_get=_get, sleep=lambda _s: None)


GREENHOUSE_PAYLOAD = {
    "meta": {"company_name": "Northwind Systems"},
    "jobs": [
        {"title": "Senior Platform Engineer", "absolute_url": "https://northwind.example/careers/1",
         "location": "Austin, TX", "updated_at": date.today().isoformat(),
         "content": "We run Azure, Terraform and Kubernetes. A team of 60 employees."},
        {"title": "Platform Engineer II", "absolute_url": "https://northwind.example/careers/2",
         "location": "Austin, TX", "updated_at": date.today().isoformat(),
         "content": "Azure and Terraform."},
        {"title": "Office Manager", "absolute_url": "https://northwind.example/careers/3",
         "updated_at": date.today().isoformat(), "content": "Front desk."},
    ],
}


# ── Step 1: discovery ────────────────────────────────────────────────────

class TestDiscovery:
    def test_discovers_tokens_from_ats_urls(self):
        scraper = FakeScraper(default=[
            ats_result("https://boards.greenhouse.io/northwind/jobs/1"),
            ats_result("https://jobs.lever.co/vectorlabs/abc"),
        ])
        stats = FunnelStats()
        refs = discover_board_refs(scraper, ["platform engineer"], max_queries=10, stats=stats, sleep=NO_SLEEP)
        assert {r.cache_key for r in refs} == {"greenhouse:northwind", "lever:vectorlabs"}
        assert stats.board_tokens_discovered == 2

    def test_non_ats_results_yield_no_tokens(self):
        """An aggregator result cannot become a board token -- there is no
        fallback that could invent one. This is the structural fix for the
        2026-09-29 ZipRecruiter leads."""
        scraper = FakeScraper(default=[
            ats_result("https://www.ziprecruiter.com/Jobs/Platform-Engineer"),
            ats_result("https://linkedin.com/jobs/platform-engineer-jobs"),
            ats_result("https://indeed.com/q-platform-engineer-jobs.html"),
        ])
        stats = FunnelStats()
        assert discover_board_refs(scraper, ["platform engineer"], max_queries=10, stats=stats, sleep=NO_SLEEP) == []
        assert stats.ats_urls_seen == 0

    def test_discovery_respects_its_query_cap(self):
        scraper = FakeScraper(default=[])
        stats = FunnelStats()
        discover_board_refs(scraper, ["a", "b", "c", "d"], max_queries=7, stats=stats, sleep=NO_SLEEP)
        assert stats.queries_discovery == 7
        assert len(scraper.queries) == 7

    def test_every_discovery_template_is_ats_scoped(self):
        for t in ATS_DISCOVERY_TEMPLATES:
            assert t.startswith("site:")
            assert "linkedin" not in t.lower()


class TestTokenCache:
    def test_cached_tokens_cost_zero_queries(self):
        db = FakeDb([{"provider": "greenhouse", "board_token": "cached", "consecutive_failures": 0}])
        refs = load_cached_board_refs(db, "p1")
        assert [r.cache_key for r in refs] == ["greenhouse:cached"]

    def test_repeatedly_failing_boards_are_not_polled(self):
        db = FakeDb([
            {"provider": "greenhouse", "board_token": "good", "consecutive_failures": 0},
            {"provider": "greenhouse", "board_token": "gone", "consecutive_failures": 5},
        ])
        assert [r.token for r in load_cached_board_refs(db, "p1")] == ["good"]

    def test_missing_cache_table_degrades_instead_of_raising(self):
        class Boom:
            def table(self, _n):
                raise RuntimeError("relation does not exist")

        assert load_cached_board_refs(Boom(), "p1") == []


# ── Step 4: decision-maker, and ONLY there ───────────────────────────────

class TestLinkedInIsNotATopLevelSource:
    """Kelvin, 2026-10-01: 'site:linkedin.com/in is NOT a top-level
    source. It runs only in the per-company decision-maker step.'"""

    def test_no_discovery_template_targets_linkedin(self):
        assert not any("linkedin" in t.lower() for t in ATS_DISCOVERY_TEMPLATES)

    def test_discovery_never_issues_a_linkedin_query(self):
        scraper = FakeScraper(default=[])
        discover_board_refs(scraper, ["platform engineer"], max_queries=20, stats=FunnelStats(), sleep=NO_SLEEP)
        assert not any("linkedin" in q.lower() for q in scraper.queries)

    def test_linkedin_queries_are_per_company_and_scoped_to_profiles(self):
        scraper = FakeScraper(default=[])
        find_decision_maker(scraper, "Northwind Systems", stats=FunnelStats(), sleep=NO_SLEEP)
        assert len(scraper.queries) == 1
        q = scraper.queries[0]
        assert q.startswith("site:linkedin.com/in ")
        assert '"Northwind Systems"' in q

    def test_full_run_issues_linkedin_queries_only_after_discovery(self):
        scraper = FakeScraper(responses={
            "site:boards.greenhouse.io": [ats_result("https://boards.greenhouse.io/northwind/jobs/1")],
            "site:linkedin.com/in": [{
                "link": "https://linkedin.com/in/janedoe",
                "title": "Jane Doe - Northwind Systems | LinkedIn",
                "snippet": "Jane Doe. VP of Engineering at Northwind Systems. Austin, TX",
            }],
        })
        find_company_first_signals(
            "p1", ["platform engineer"], scraper=scraper,
            ats_client=board_client({"northwind": GREENHOUSE_PAYLOAD}), max_queries=20,
            sleep=NO_SLEEP,
        )
        linkedin_idx = [i for i, q in enumerate(scraper.queries) if "linkedin.com/in" in q]
        ats_idx = [i for i, q in enumerate(scraper.queries) if "site:boards.greenhouse.io" in q]
        assert linkedin_idx and ats_idx
        assert min(linkedin_idx) > min(ats_idx), "discovery must precede any profile lookup"


class TestDecisionMakerTitleFromSnippet:
    @pytest.mark.parametrize("snippet,title", [
        ("Jane Doe. VP of Engineering at Northwind Systems. Austin, TX", "VP of Engineering"),
        ("Marcus Webb - Head of Platform - Acme", "Head of Platform"),
        ("Experience: Acme Corp · VP, Infrastructure", "VP, Infrastructure"),
        ("Chief Technology Officer at Helios", "Chief Technology Officer"),
    ])
    def test_parses_real_titles(self, snippet, title):
        assert parse_decision_maker_title(snippet) == title

    @pytest.mark.parametrize("snippet", [
        "",
        "500+ connections. View Jane's profile on LinkedIn.",
        "Passionate about cloud. Dog lover.",
    ])
    def test_returns_none_rather_than_a_placeholder(self, snippet):
        """MKT-O2 hands this field to the LLM as 'real signal context
        (never invent anything beyond this)'. A guess becomes a false
        claim in outbound copy -- that bug already shipped once."""
        assert parse_decision_maker_title(snippet) is None

    def test_recruiter_contact_is_skipped_not_returned(self):
        scraper = FakeScraper(default=[{
            "link": "https://linkedin.com/in/x",
            "title": "Sam Smith - Acme | LinkedIn",
            "snippet": "Sam Smith. Director of Talent Acquisition at Acme Corp.",
        }])
        out = find_decision_maker(scraper, "Acme Corp", stats=FunnelStats(), sleep=NO_SLEEP)
        assert out["title"] is None, "a recruiter is not the buyer"


# ── Steps 2+3 ────────────────────────────────────────────────────────────

class TestCandidateBuilding:
    def _boards(self):
        client = board_client({"northwind": GREENHOUSE_PAYLOAD})
        ref = BoardRef("greenhouse", "northwind")
        return {ref.cache_key: client.fetch_board(ref)}, {ref.cache_key: ref}

    def test_only_title_matching_roles_count(self):
        boards, refs = self._boards()
        stats = FunnelStats()
        cands = build_candidates(boards, refs, ["platform engineer"], max_age_days=30, stats=stats)
        assert len(cands) == 1
        assert cands[0].company == "Northwind Systems"
        assert len(cands[0].matching) == 2, "two platform roles match; Office Manager does not"
        assert stats.postings_returned == 3
        assert stats.postings_title_matched == 2

    def test_company_with_no_matching_role_is_attributed_not_lost(self):
        boards, refs = self._boards()
        stats = FunnelStats()
        assert build_candidates(boards, refs, ["quantum chemist"], max_age_days=30, stats=stats) == []
        assert stats.dropped_no_matching_role == 1

    def test_domain_comes_from_the_posting_not_the_ats_host(self):
        boards, refs = self._boards()
        cands = build_candidates(boards, refs, ["platform engineer"], max_age_days=30, stats=FunnelStats())
        assert cands[0].domain == "northwind.example"

    def test_open_role_count_feeds_intent(self):
        boards, refs = self._boards()
        cands = build_candidates(boards, refs, ["platform engineer"], max_age_days=30, stats=FunnelStats())
        low = qualify_candidate(CompanyCandidate(
            ref=cands[0].ref, company=cands[0].company, domain=cands[0].domain,
            matching=cands[0].matching[:1], all_postings=cands[0].all_postings,
        ), contact={"title": "VP of Engineering"})
        high = qualify_candidate(cands[0], contact={"title": "VP of Engineering"})
        assert high["intent_score"] > low["intent_score"]


class TestQualifyCandidate:
    def _candidate(self, **kw):
        base = dict(
            ref=BoardRef("greenhouse", "northwind"), company="Northwind Systems",
            domain="northwind.example",
            matching=[AtsPosting("greenhouse", "northwind", title="Senior Platform Engineer",
                                 url="https://northwind.example/careers/1", location="Austin, TX",
                                 posted_at=date.today(),
                                 description_text="Azure, Terraform, Kubernetes. A team of 60 employees.")],
            all_postings=[],
        )
        base.update(kw)
        return CompanyCandidate(**base)

    def test_never_invents_an_email(self):
        """The 19 rows with fabricated addresses in the 2026-10-01
        cleanup came from guessing. v2 structurally cannot: grade is
        'unknown' until something is actually verified, and 'unknown'
        never sends."""
        out = qualify_candidate(self._candidate(), contact={"title": "CTO"})
        assert "email" not in out
        assert out["email_grade"] == "unknown"

    def test_unknown_email_routes_to_manual_linkedin_never_email(self):
        out = qualify_candidate(self._candidate(), contact={"title": "CTO"})
        assert out["lead_route"] == "manual_linkedin"

    def test_domainless_candidate_routes_to_manual_linkedin(self):
        out = qualify_candidate(self._candidate(domain=None), contact={"title": "CTO"})
        assert out["lead_route"] == "manual_linkedin"

    def test_technographics_and_firmographics_extracted_from_jd(self):
        out = qualify_candidate(self._candidate(), contact={"title": "CTO"})
        assert out["stack_tags"] == ["Azure", "Kubernetes", "Terraform"]
        assert out["headcount_band"] == "50-99"

    def test_job_posting_title_is_the_real_role_never_the_contact_title(self):
        """The 2026-10-01 factual-accuracy bug: the ICP's contact-title
        placeholder was being stored as what the company was hiring."""
        out = qualify_candidate(self._candidate(), contact={"title": "CTO"})
        assert out["job_posting_title"] == "Senior Platform Engineer"
        assert out["title"] == "CTO"

    def test_recruiter_contact_rejects_the_candidate_with_a_reason(self):
        out = qualify_candidate(self._candidate(), contact={"title": "Technical Recruiter"})
        assert out["lead_route"] == "reject"
        assert out["route_reason"] == "negative_title:recruiting"

    def test_no_contact_still_scores_and_routes(self):
        """An ATS posting names no hiring manager. That must not reject
        the company -- company-first sourcing would find nothing."""
        out = qualify_candidate(self._candidate(), contact=None)
        assert out["seniority"] == "unknown"
        assert out["lead_route"] in ("manual_linkedin", "reject")
        assert out["route_reason"]


# ── Budget ───────────────────────────────────────────────────────────────

class TestQueryBudget:
    def test_total_queries_never_exceed_max_queries(self):
        """The instruction was a run capped at ≤40 Brave queries. That cap
        has to hold across discovery AND every decision-maker lookup."""
        scraper = FakeScraper(responses={
            "site:boards.greenhouse.io": [ats_result(f"https://boards.greenhouse.io/co{i}/jobs/1") for i in range(10)],
            "site:linkedin.com/in": [],
        })
        payloads = {f"co{i}": GREENHOUSE_PAYLOAD for i in range(10)}
        _rows, stats = find_company_first_signals(
            "p1", ["platform engineer", "devops engineer", "sre", "cloud engineer"],
            scraper=scraper, ats_client=board_client(payloads), max_queries=40,
            sleep=NO_SLEEP,
        )
        total = stats.queries_discovery + stats.queries_decision_maker
        assert total <= 40, f"spent {total} Brave queries against a 40 cap"
        assert len(scraper.queries) == total

    def test_decision_maker_step_cannot_starve_discovery(self):
        scraper = FakeScraper(default=[])
        _rows, stats = find_company_first_signals(
            "p1", ["platform engineer"], scraper=scraper,
            ats_client=board_client({}), max_queries=20,
            sleep=NO_SLEEP,
        )
        expected_discovery = 20 - int(20 * DECISION_MAKER_BUDGET_SHARE)
        assert stats.queries_discovery <= expected_discovery

    def test_no_api_key_spends_nothing_and_returns_nothing(self):
        class NoKey(FakeScraper):
            api_key = None

        scraper = NoKey()
        rows, stats = find_company_first_signals("p1", ["x"], scraper=scraper, max_queries=40, sleep=NO_SLEEP)
        assert rows == []
        assert scraper.queries == []
        assert stats.queries_discovery == 0

    def test_ats_expansion_costs_zero_brave_queries(self):
        """The whole economic argument for v2: one query found one
        posting, and the ATS API returned the company's entire role list
        for free."""
        scraper = FakeScraper(responses={
            "site:boards.greenhouse.io": [ats_result("https://boards.greenhouse.io/northwind/jobs/1")],
            "site:linkedin.com/in": [],
        })
        _rows, stats = find_company_first_signals(
            "p1", ["platform engineer"], scraper=scraper,
            ats_client=board_client({"northwind": GREENHOUSE_PAYLOAD}), max_queries=40,
            sleep=NO_SLEEP,
        )
        assert stats.postings_returned == 3, "3 roles learned"
        assert stats.postings_title_matched == 2
        # Those 3 roles cost one discovery query, not three.
        assert stats.queries_discovery <= 5


# ── Funnel stats ─────────────────────────────────────────────────────────

class TestFunnelStats:
    def test_every_candidate_is_accounted_for(self):
        """written + every named drop must equal companies considered.
        A lead that vanishes with no stage to explain it is the exact bug
        this build exists to prevent."""
        scraper = FakeScraper(responses={
            "site:boards.greenhouse.io": [
                ats_result("https://boards.greenhouse.io/northwind/jobs/1"),
                ats_result("https://boards.greenhouse.io/quiet/jobs/1"),
            ],
            "site:linkedin.com/in": [],
        })
        payloads = {"northwind": GREENHOUSE_PAYLOAD, "quiet": {"meta": {}, "jobs": []}}
        rows, stats = find_company_first_signals(
            "p1", ["platform engineer"], scraper=scraper,
            ats_client=board_client(payloads), max_queries=40,
            sleep=NO_SLEEP,
        )
        d = stats.as_dict()
        accounted = (
            d["leads_qualified"] + d["dropped_no_matching_role"] + d["dropped_negative_title_total"]
            + d["dropped_low_fit"] + d["dropped_low_intent"] + d["dropped_duplicate_company"]
        )
        assert accounted == d["companies_considered"] == 2
        assert len(rows) == d["leads_qualified"]

    def test_stats_are_json_serialisable_for_funnel_stats_column(self):
        import json
        _rows, stats = find_company_first_signals(
            "p1", ["x"], scraper=FakeScraper(default=[]), ats_client=board_client({}), max_queries=10,
            sleep=NO_SLEEP,
        )
        json.dumps(stats.as_dict())

    def test_existing_domains_are_deduped_and_counted(self):
        scraper = FakeScraper(responses={
            "site:boards.greenhouse.io": [ats_result("https://boards.greenhouse.io/northwind/jobs/1")],
            "site:linkedin.com/in": [],
        })
        rows, stats = find_company_first_signals(
            "p1", ["platform engineer"], scraper=scraper,
            ats_client=board_client({"northwind": GREENHOUSE_PAYLOAD}), max_queries=40,
            existing_domains={"northwind.example"},
            sleep=NO_SLEEP,
        )
        assert rows == []
        assert stats.dropped_duplicate_company == 1


# ── Row projection ───────────────────────────────────────────────────────

class TestToMseLeadRow:
    def test_drops_internal_keys_and_nones(self):
        qualified = qualify_candidate(CompanyCandidate(
            ref=BoardRef("greenhouse", "northwind"), company="Northwind Systems", domain=None,
            matching=[AtsPosting("greenhouse", "northwind", title="Platform Engineer", posted_at=date.today())],
            all_postings=[],
        ), contact={"title": "CTO", "name": "Jane Doe", "profile_url": "https://linkedin.com/in/janedoe"})
        qualified["product_id"] = "p1"
        row = to_mse_lead_row(qualified, source="job_posting_signal")

        for internal in ("route_reason", "negative_reason", "contact_name",
                         "contact_profile_url", "ats_provider", "ats_board_token"):
            assert internal not in row
        assert "domain" not in row, "a None must be omitted, not written as a literal"
        assert row["first_name"] == "Jane"
        assert row["last_name"] == "Doe"
        assert row["source"] == "job_posting_signal"
        assert row["lead_route"] == "manual_linkedin"

    def test_contact_name_is_split_into_the_two_real_columns(self):
        """mse_leads has first_name/last_name -- there is no `name` column.
        The first live v2 run died on exactly this with PGRST204."""
        assert split_contact_name("Jane Doe") == ("Jane", "Doe")
        assert split_contact_name("Maria del Carmen Ruiz") == ("Maria", "del Carmen Ruiz")
        assert split_contact_name(None) == (None, None)
        assert split_contact_name("   ") == (None, None)

    def test_single_token_name_does_not_invent_a_surname(self):
        assert split_contact_name("Cher") == ("Cher", None)

    def test_decision_maker_profile_url_is_persisted(self):
        """It was being discarded, which left the manual-LinkedIn track
        with no way to reach the person it had routed there."""
        qualified = qualify_candidate(CompanyCandidate(
            ref=BoardRef("greenhouse", "x"), company="X", domain=None,
            matching=[AtsPosting("greenhouse", "x", title="Platform Engineer", posted_at=date.today())],
            all_postings=[],
        ), contact={"title": "CTO", "name": "A B", "profile_url": "https://linkedin.com/in/ab"})
        qualified["product_id"] = "p1"
        assert to_mse_lead_row(qualified, source="s")["linkedin_url"] == "https://linkedin.com/in/ab"

    def test_every_emitted_key_is_a_real_mse_leads_column(self):
        """Asserted against MSE_LEADS_WRITABLE_COLUMNS, which was taken
        from microsaas-prod's information_schema on 2026-10-01.

        The predecessor of this test compared against a hand-written
        allowlist that contained "name" -- so it passed while production
        rejected the insert with PGRST204. An allowlist invented in the
        test file can only ever confirm the implementation's own
        assumptions; this one has to match the database."""
        qualified = qualify_candidate(CompanyCandidate(
            ref=BoardRef("greenhouse", "x"), company="X", domain="x.example",
            matching=[AtsPosting("greenhouse", "x", title="Platform Engineer", posted_at=date.today(),
                                 description_text="Azure Terraform 60 employees")],
            all_postings=[],
        ), contact={"title": "CTO", "name": "A B", "profile_url": "https://linkedin.com/in/ab"})
        qualified["product_id"] = "p1"
        row = to_mse_lead_row(qualified, source="s")
        assert set(row) <= MSE_LEADS_WRITABLE_COLUMNS
        assert "name" not in row, "mse_leads has first_name/last_name, not name"

    def test_unknown_keys_are_refused_rather_than_sent_to_postgrest(self, caplog):
        """A key that is not a real column must be dropped here, with a
        warning -- not 400 the whole batch insert in production."""
        import agents.marketing.company_first_sourcing as cfs

        original = cfs.MSE_LEADS_WRITABLE_COLUMNS
        try:
            cfs.MSE_LEADS_WRITABLE_COLUMNS = original - {"company"}
            qualified = {"product_id": "p1", "company": "X", "fit_score": 0.5}
            with caplog.at_level("WARNING"):
                row = cfs.to_mse_lead_row(qualified, source="s")
            assert "company" not in row
            assert "refusing to write unknown mse_leads column" in caplog.text
        finally:
            cfs.MSE_LEADS_WRITABLE_COLUMNS = original


class TestBravePacing:
    """BraveSearchScraper.scrape() paces itself; _search() -- which v2
    calls directly -- does not. Without pacing here a 40-query run fires
    40 requests back-to-back and earns 429s on a metered plan."""

    def test_every_query_after_the_first_is_paced(self):
        delays = []
        scraper = FakeScraper(default=[])
        discover_board_refs(scraper, ["a", "b", "c"], max_queries=5,
                            stats=FunnelStats(), sleep=delays.append)
        assert len(scraper.queries) == 5
        assert len(delays) == 4, "one pause between each pair of queries, none before the first"
        assert all(1.2 <= d <= 2.5 for d in delays)

    def test_decision_maker_lookup_is_paced_too(self):
        delays = []
        find_decision_maker(FakeScraper(default=[]), "Acme", stats=FunnelStats(), sleep=delays.append)
        assert len(delays) == 1


class TestBoardCap:
    """ATS expansion is free in Brave quota but not in wall clock: one
    HTTP request (20s timeout) + a 1-2.5s pause per board. An uncapped run
    that discovered 200 boards would run over an hour and get killed
    mid-flight, leaving mse_lead_finder_runs stuck at "running" with no
    funnel_stats -- the un-attributable failure this build removes."""

    def _scraper(self, n):
        return FakeScraper(responses={
            "site:boards.greenhouse.io": [
                ats_result(f"https://boards.greenhouse.io/co{i}/jobs/1") for i in range(n)
            ],
            "site:linkedin.com/in": [],
        })

    def test_boards_polled_never_exceeds_the_cap(self):
        payloads = {f"co{i}": GREENHOUSE_PAYLOAD for i in range(30)}
        _rows, stats = find_company_first_signals(
            "p1", ["platform engineer"], scraper=self._scraper(30),
            ats_client=board_client(payloads), max_queries=40, max_boards=5,
            sleep=NO_SLEEP,
        )
        assert stats.boards_polled == 5
        assert stats.boards_skipped_over_cap == 25

    def test_skipped_boards_are_counted_not_silently_dropped(self):
        _rows, stats = find_company_first_signals(
            "p1", ["platform engineer"], scraper=self._scraper(10),
            ats_client=board_client({}), max_queries=40, max_boards=3,
            sleep=NO_SLEEP,
        )
        assert stats.boards_skipped_over_cap == 7
        assert "boards_skipped_over_cap" in stats.as_dict()

    def test_under_the_cap_nothing_is_skipped(self):
        payloads = {f"co{i}": GREENHOUSE_PAYLOAD for i in range(3)}
        _rows, stats = find_company_first_signals(
            "p1", ["platform engineer"], scraper=self._scraper(3),
            ats_client=board_client(payloads), max_queries=40, max_boards=60,
            sleep=NO_SLEEP,
        )
        assert stats.boards_skipped_over_cap == 0
        assert stats.boards_polled == 3


class TestBoardTokenCacheWrite:
    def test_timestamps_are_real_iso_not_the_string_now(self):
        """PostgREST sends values as JSON, so "now()" arrives as a literal
        6-character string and Postgres rejects it as invalid timestamptz.
        upsert_board_tokens' own try/except would have swallowed that into
        a warning and the cache would silently never populate -- defeating
        the whole reason the table exists."""
        from agents.marketing.company_first_sourcing import upsert_board_tokens

        db = FakeDb([])
        ref = BoardRef("greenhouse", "northwind")
        posting = AtsPosting("greenhouse", "northwind", company="Northwind Systems", title="Platform Engineer")
        written = upsert_board_tokens(db, {ref.cache_key: (ref, [posting], "northwind.example")}, "p1")

        assert written == 1
        row = db.query.upserts[0]
        for field_name in ("last_fetched_at", "updated_at"):
            value = row[field_name]
            assert value != "now()"
            assert datetime.fromisoformat(value), f"{field_name} must be a parseable ISO timestamp"
        assert row["last_status"] == "ok"
        assert row["company_domain"] == "northwind.example"

    def test_empty_board_is_recorded_as_empty_with_a_failure_tick(self):
        from agents.marketing.company_first_sourcing import upsert_board_tokens

        db = FakeDb([])
        ref = BoardRef("lever", "quiet")
        upsert_board_tokens(db, {ref.cache_key: (ref, [], None)}, "p1")
        row = db.query.upserts[0]
        assert row["last_status"] == "empty"
        assert row["open_role_count"] == 0
        assert row["company"] == "Quiet", "company falls back to the cased token, never the raw slug"


class TestDeadBoardEviction:
    """The first live v2 run surfaced 7+ ATS boards returning 404 (angi,
    dbtlabsinc, o1labs, worldlabs, skylotechnologies, zerofox,
    talentwerx.io) -- Brave's index carries ATS URLs for companies that
    have since moved or closed their boards. Verified the endpoints are
    correct: stripe and discord return 200 on the same API.

    Two bugs this covers, both of which made eviction impossible:
      - a 404 and "no openings this week" both recorded as 'empty'
      - consecutive_failures was SET to 1, never incremented, so the
        `>= 3` eviction could never fire and a dead board was re-polled
        (HTTP request + politeness pause) every run, forever."""

    def _upsert(self, status, prior=None):
        from agents.marketing.company_first_sourcing import upsert_board_tokens

        db = FakeDb([])
        ref = BoardRef("greenhouse", "gone")
        upsert_board_tokens(
            db, {ref.cache_key: (ref, [], None)}, "p1",
            board_status={ref.cache_key: status},
            prior_failures=prior or {},
        )
        return db.query.upserts[0]

    def test_failed_board_is_recorded_as_failed_not_empty(self):
        assert self._upsert("failed")["last_status"] == "failed"

    def test_empty_board_is_still_recorded_as_empty(self):
        """A company with no current openings is a legitimate result and
        must keep its place in the cache."""
        row = self._upsert("empty")
        assert row["last_status"] == "empty"
        assert row["consecutive_failures"] == 0

    def test_consecutive_failures_increments_rather_than_resetting(self):
        key = "greenhouse:gone"
        assert self._upsert("failed", {key: 0})["consecutive_failures"] == 1
        assert self._upsert("failed", {key: 1})["consecutive_failures"] == 2
        assert self._upsert("failed", {key: 2})["consecutive_failures"] == 3

    def test_a_successful_fetch_resets_the_failure_count(self):
        from agents.marketing.company_first_sourcing import upsert_board_tokens

        db = FakeDb([])
        ref = BoardRef("greenhouse", "back")
        posting = AtsPosting("greenhouse", "back", company="Back Online", title="Platform Engineer")
        upsert_board_tokens(
            db, {ref.cache_key: (ref, [posting], None)}, "p1",
            board_status={ref.cache_key: "ok"}, prior_failures={ref.cache_key: 2},
        )
        assert db.query.upserts[0]["consecutive_failures"] == 0

    def test_failed_fetch_does_not_overwrite_a_known_role_count_with_zero(self):
        """A failed request learned nothing about the role count."""
        assert "open_role_count" not in self._upsert("failed")

    def test_board_over_the_failure_limit_is_not_polled_again(self):
        db = FakeDb([
            {"provider": "greenhouse", "board_token": "dead", "consecutive_failures": 3},
            {"provider": "greenhouse", "board_token": "alive", "consecutive_failures": 1},
        ])
        prior = {}
        refs = load_cached_board_refs(db, "p1", prior_failures=prior)
        assert [r.token for r in refs] == ["alive"]
        assert prior["greenhouse:dead"] == 3, (
            "a skipped board's count must still be remembered, so re-discovering "
            "it this run keeps accumulating instead of resetting to 1"
        )

    def test_client_records_a_distinct_status_per_outcome(self):
        ok = board_client({"northwind": GREENHOUSE_PAYLOAD})
        ok.fetch_board(BoardRef("greenhouse", "northwind"))
        assert ok.board_status["greenhouse:northwind"] == "ok"

        empty = board_client({"quiet": {"meta": {}, "jobs": []}})
        empty.fetch_board(BoardRef("greenhouse", "quiet"))
        assert empty.board_status["greenhouse:quiet"] == "empty"

        failed = board_client({})
        failed.fetch_board(BoardRef("greenhouse", "gone"))
        assert failed.board_status["greenhouse:gone"] == "failed"


class TestCompanyAssociationIsVerified:
    """Found live 2026-10-01: the same profile (linkedin.com/in/jerrykrikheli)
    came back as the decision-maker for TWO different companies, because
    `site:linkedin.com/in "Company"` also matches a profile that merely
    MENTIONS the company. Writing that lead asserts "<person> is <title>
    at <company>" as fact -- the same class of fabrication as the
    job_posting_title bug."""

    def _result(self, title, snippet):
        return [{"link": "https://linkedin.com/in/x", "title": title, "snippet": snippet}]

    def test_profile_not_naming_the_company_is_rejected(self):
        scraper = FakeScraper(default=self._result(
            "Jerry Krikheli - Globex | LinkedIn",
            "Jerry Krikheli. VP of Engineering at Globex. Austin, TX",
        ))
        out = find_decision_maker(scraper, "Northwind Systems", stats=FunnelStats(), sleep=NO_SLEEP)
        assert out["title"] is None
        assert out["name"] is None

    def test_profile_naming_the_company_is_accepted(self):
        scraper = FakeScraper(default=self._result(
            "Jane Doe - Northwind Systems | LinkedIn",
            "Jane Doe. VP of Engineering at Northwind Systems. Austin, TX",
        ))
        out = find_decision_maker(scraper, "Northwind Systems", stats=FunnelStats(), sleep=NO_SLEEP)
        assert out["title"] == "VP of Engineering"
        assert out["name"] == "Jane Doe"

    def test_punctuation_and_casing_differences_still_match(self):
        """An ATS token yields "Vector Labs" while the profile says
        "VectorLabs" -- a strict comparison would throw away a real lead."""
        scraper = FakeScraper(default=self._result(
            "Sam Lee - VectorLabs | LinkedIn", "Sam Lee. CTO at VectorLabs.",
        ))
        out = find_decision_maker(scraper, "Vector Labs", stats=FunnelStats(), sleep=NO_SLEEP)
        assert out["title"] == "CTO"

    def test_legal_suffix_on_our_side_still_matches(self):
        scraper = FakeScraper(default=self._result(
            "Sam Lee - Acme Technologies | LinkedIn", "Sam Lee. CTO at Acme Technologies.",
        ))
        out = find_decision_maker(scraper, "Acme Technologies Inc", stats=FunnelStats(), sleep=NO_SLEEP)
        assert out["title"] == "CTO"

    def test_a_company_name_of_only_stopwords_matches_nothing(self):
        """"Inc" must not match every profile on LinkedIn."""
        from agents.marketing.company_first_sourcing import _mentions_company

        assert _mentions_company("anything at all", "Inc") is False
        assert _mentions_company("anything at all", "") is False


class TestDuplicateContactHandling:
    """mse_leads.linkedin_url is globally UNIQUE (partial index), and 41
    rows already carried one before v2 ran. A batch insert is
    all-or-nothing, so one collision discarded every good lead with it."""

    def _run(self, existing):
        scraper = FakeScraper(responses={
            "site:boards.greenhouse.io": [ats_result("https://boards.greenhouse.io/northwind/jobs/1")],
            "site:linkedin.com/in": [{
                "link": "https://linkedin.com/in/janedoe",
                "title": "Jane Doe - Northwind Systems | LinkedIn",
                "snippet": "Jane Doe. VP of Engineering at Northwind Systems.",
            }],
        })
        return find_company_first_signals(
            "p1", ["platform engineer"], scraper=scraper,
            ats_client=board_client({"northwind": GREENHOUSE_PAYLOAD}), max_queries=40,
            existing_linkedin_urls=existing, sleep=NO_SLEEP,
        )

    def test_already_used_profile_drops_the_attribution_not_the_lead(self):
        """The company signal is still real -- it just has no usable
        contact. Dropping the whole lead would throw away a genuine
        hiring signal over a contact-field collision."""
        rows, stats = self._run({"https://linkedin.com/in/janedoe"})
        assert len(rows) == 1, "the company lead survives"
        assert stats.dropped_duplicate_contact == 1
        row = to_mse_lead_row(rows[0], source="s")
        assert "linkedin_url" not in row
        assert "first_name" not in row
        assert row["company"] == "Northwind Systems"

    def test_unused_profile_is_kept(self):
        rows, stats = self._run(set())
        assert stats.dropped_duplicate_contact == 0
        assert to_mse_lead_row(rows[0], source="s")["linkedin_url"] == "https://linkedin.com/in/janedoe"


class TestQueuedBoardTokens:
    def test_unpolled_discoveries_are_cached_for_next_run(self):
        """The first live run discovered 186 boards from 26 Brave queries
        against a 60-board cap. Without caching the other 126 they would be
        re-discovered next week at full Brave cost."""
        from agents.marketing.company_first_sourcing import queue_board_tokens

        db = FakeDb([])
        refs = {f"greenhouse:co{i}": BoardRef("greenhouse", f"co{i}") for i in range(3)}
        assert queue_board_tokens(db, refs, "p1") == 3
        row = db.query.upserts[0]
        assert row["discovered_from"] == "brave_ats_discovery"
        assert "last_status" not in row, "never fetched is a distinct state from ok/empty/failed"
        assert "last_fetched_at" not in row


class TestCallerOwnedStats:
    """Brave bills a query when it is issued, so a run that dies partway
    has really spent that quota. The caller owns the FunnelStats so the
    failure path can still report the spend -- two failed v2 runs on
    2026-10-01 spent ~60 queries that _this_months_brave_query_count never
    saw, which is the figure ground-rule 5 requires be accurate."""

    def test_caller_supplied_stats_object_is_the_one_populated(self):
        mine = FunnelStats()
        scraper = FakeScraper(responses={
            "site:boards.greenhouse.io": [ats_result("https://boards.greenhouse.io/northwind/jobs/1")],
            "site:linkedin.com/in": [],
        })
        _rows, returned = find_company_first_signals(
            "p1", ["platform engineer"], scraper=scraper,
            ats_client=board_client({"northwind": GREENHOUSE_PAYLOAD}), max_queries=40,
            stats=mine, sleep=NO_SLEEP,
        )
        assert returned is mine, "the caller must hold the same object, not a copy"
        assert mine.queries_discovery > 0

    def test_spend_is_visible_on_the_caller_object_after_a_mid_run_failure(self):
        """An exception inside expansion must still leave the queries
        already spent recorded on the caller's stats."""
        mine = FunnelStats()

        class Exploding:
            stats = type("S", (), {"as_dict": lambda self: {}})()
            board_status: dict = {}

            def fetch_boards(self, _refs):
                raise RuntimeError("ATS exploded")

        scraper = FakeScraper(responses={
            "site:boards.greenhouse.io": [ats_result("https://boards.greenhouse.io/northwind/jobs/1")],
        })
        with pytest.raises(RuntimeError):
            find_company_first_signals(
                "p1", ["platform engineer"], scraper=scraper,
                ats_client=Exploding(), max_queries=40, stats=mine, sleep=NO_SLEEP,
            )
        assert mine.queries_discovery > 0, "spend already incurred must survive the exception"


class TestBoardCacheIsSharedAcrossProducts:
    """mse_ats_board_tokens is UNIQUE on (provider, board_token) -- one row
    per company board, globally. Filtering reads by product_id would make a
    board discovered for consulting invisible to Cloud Decoded (re-paying
    full Brave cost to rediscover it), and the following upsert would flip
    the row's product_id so it became invisible to consulting instead. The
    two branches would take turns paying for the same tokens forever."""

    def test_cached_boards_are_returned_regardless_of_discovering_product(self):
        db = FakeDb([{"provider": "greenhouse", "board_token": "shared", "consecutive_failures": 0}])
        for product in ("consulting-product-id", "cloud-decoded-product-id", None):
            refs = load_cached_board_refs(db, product)
            assert [r.token for r in refs] == ["shared"], f"invisible to {product}"

    def test_read_does_not_filter_on_product_id(self):
        """Pinned structurally: the flaw was a single .eq("product_id", ...)
        that looked obviously correct in review."""
        import inspect

        import agents.marketing.company_first_sourcing as cfs

        body = inspect.getsource(cfs.load_cached_board_refs)
        code_lines = [
            ln for ln in body.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        code = "\n".join(code_lines)
        assert '.eq("product_id"' not in code, (
            "load_cached_board_refs must not scope the shared board cache to one product"
        )
