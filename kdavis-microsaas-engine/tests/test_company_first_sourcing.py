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
    load_cached_board_refs,
    parse_decision_maker_title,
    qualify_candidate,
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
        ), contact={"title": "CTO", "name": "Jane Doe"})
        qualified["product_id"] = "p1"
        row = to_mse_lead_row(qualified, source="job_posting_signal")

        for internal in ("route_reason", "negative_reason", "contact_name", "ats_provider", "ats_board_token"):
            assert internal not in row
        assert "domain" not in row, "a None must be omitted, not written as a literal"
        assert row["name"] == "Jane Doe"
        assert row["source"] == "job_posting_signal"
        assert row["lead_route"] == "manual_linkedin"

    def test_only_declared_columns_are_emitted(self):
        """Guards against a key drifting in that migration 057 never
        added -- PostgREST would 400 the whole insert."""
        allowed = {
            "product_id", "source", "company", "domain", "title", "name", "location",
            "job_posting_url", "job_posting_title", "job_posting_date", "open_role_count",
            "stack_tags", "headcount_band", "funding_months", "email_grade", "fit_score",
            "intent_score", "score_reasons", "lead_route", "seniority", "confidence_score",
        }
        qualified = qualify_candidate(CompanyCandidate(
            ref=BoardRef("greenhouse", "x"), company="X", domain="x.example",
            matching=[AtsPosting("greenhouse", "x", title="Platform Engineer", posted_at=date.today(),
                                 description_text="Azure Terraform 60 employees")],
            all_postings=[],
        ), contact={"title": "CTO", "name": "A B"})
        qualified["product_id"] = "p1"
        assert set(to_mse_lead_row(qualified, source="s")) <= allowed


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
