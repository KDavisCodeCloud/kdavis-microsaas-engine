"""Lead targeting extensions (Kelvin, 2026-10-18): pain phrases, the partner
lane, VC operating partners, Hacker News.

No network: every HTTP call goes through an injected fake, every Brave query
through a fake scraper that counts them.
"""

import json
from datetime import date

import pytest

from agents.marketing import mkt_o2_cold_dm_writer as o2
from agents.marketing.company_classification import CONSULTING_EXCLUSION_POLICY, classify_company
from agents.marketing.company_first_sourcing import (
    VC_FUNDED_INTENT_BOOST,
    CompanyCandidate,
    PortfolioIndex,
    Stage1Result,
    qualify_candidate,
)
from agents.marketing.contact_fit import evaluate_contact_fit, evaluate_partner_contact_fit
from agents.marketing.hn_scout import (
    FREELANCER,
    WHO_IS_HIRING,
    HnPost,
    company_from_post,
    domain_from_post,
    find_hn_leads,
    keep_post,
)
from agents.marketing.outreach_copy import PAIN_RULE, pain_phrase_missing
from agents.marketing.pain_phrases import PainConfig, boost_intent, match_pain_phrases
from agents.marketing.partner_sourcing import (
    PARTNER_EXCLUSION_POLICY,
    PARTNERS_PRODUCT_ID,
    find_partner_leads,
    firm_host,
)
from agents.marketing.vc_partners import parse_portfolio_page, parse_team_page
from scrapers.ats_boards import AtsPosting, BoardRef

import time as _time
NOW_TS = int(_time.time())

SEED = ["ARM to Bicep", "Bicep migration", "Terraform migration", "tech debt",
        "pipeline modernization", "GitHub Actions migration", "Azure DevOps to GitHub",
        "Key Vault", "secrets management", "IaC standardization", "cloud cost reduction",
        "SOC 2 readiness", "landing zone"]


class Resp:
    def __init__(self, status=200, text="", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def http(routes: dict):
    """Fake httpx.get: first route whose key is a substring of the URL."""
    def _get(url, **_kw):
        for key, value in routes.items():
            if key in url:
                return value if isinstance(value, Resp) else Resp(200, value)
        return Resp(404, "")
    return _get


# ── 1. Pain phrases ──────────────────────────────────────────────────────

class TestPainPhrases:
    def test_tolerates_hyphens_and_spacing_but_not_paraphrase(self):
        text = ("You will lead our ARM-to-Bicep work. We are working toward SOC2 readiness "
                "and need someone who has paid down technical debt.")
        m = match_pain_phrases(text, SEED)
        assert m.phrases == ["ARM to Bicep", "SOC 2 readiness"]
        # "technical debt" is not "tech debt": literal matching, by design --
        # the snippet is quoted to the prospect, so it must be their words.
        assert "tech debt" not in m.phrases

    def test_snippet_is_the_sentence_verbatim(self):
        text = "About us. Own the Azure landing zone and Key Vault rollout. Benefits follow."
        m = match_pain_phrases(text, SEED)
        snippets = {e["phrase"]: e["snippet"] for e in m.evidence}
        assert snippets["landing zone"] == "Own the Azure landing zone and Key Vault rollout."

    def test_word_edges_are_respected(self):
        assert not match_pain_phrases("keyvaults and techdebt.io", ["Key Vault", "tech debt"])

    def test_boost_is_capped(self):
        cfg = PainConfig(phrases=tuple(SEED), per_match=0.10, max_boost=0.20)
        m = match_pain_phrases("tech debt, Key Vault, landing zone", SEED)
        new, reason = boost_intent(0.5, m, cfg)
        assert new == 0.7 and "+0.20" in reason

    def test_no_match_no_change(self):
        assert boost_intent(0.42, match_pain_phrases("nothing here", SEED), PainConfig(tuple(SEED))) == (0.42, None)

    def test_config_comes_from_the_icp_row(self):
        cfg = PainConfig.from_icp({"pain_phrases": ["landing zone", "", 7],
                                   "pain_phrase_boost": {"per_match": 0.05, "max": 0.1}})
        assert cfg.phrases == ("landing zone",) and cfg.per_match == 0.05 and cfg.max_boost == 0.1

    def test_empty_icp_means_no_phrases(self):
        assert PainConfig.from_icp(None).phrases == ()


def _candidate(desc: str, company="Northwind Systems"):
    p = AtsPosting(provider="greenhouse", board_token="northwind", company=company,
                   title="Senior Platform Engineer", url="https://boards.greenhouse.io/northwind/jobs/1",
                   posted_at=date.today(), description_text=desc)
    return CompanyCandidate(ref=BoardRef("greenhouse", "northwind"), company=company, domain=None,
                            matching=[p], all_postings=[p])


class TestQualifyWithSignals:
    def test_pain_phrases_raise_intent_and_are_stored(self):
        base = qualify_candidate(_candidate("Azure and Terraform."), stage1=Stage1Result(passed=True))
        boosted = qualify_candidate(
            _candidate("Azure and Terraform. Lead the Terraform migration and landing zone build."),
            stage1=Stage1Result(passed=True), pain=PainConfig(tuple(SEED)))
        assert boosted["pain_phrases"] == ["Terraform migration", "landing zone"]
        assert boosted["intent_score"] == pytest.approx(min(1.0, base["intent_score"] + 0.20))
        assert any("pain phrases in JD" in r for r in boosted["score_reasons"])
        assert boosted["pain_evidence"][0]["snippet"].startswith("Lead the Terraform migration")

    def test_funded_by_adds_the_vc_boost(self):
        base = qualify_candidate(_candidate("Azure."), stage1=Stage1Result(passed=True))
        funded = qualify_candidate(_candidate("Azure."), stage1=Stage1Result(passed=True),
                                   funded_by=["Craft Ventures"])
        assert funded["funded_by"] == ["Craft Ventures"]
        assert funded["intent_score"] == pytest.approx(min(1.0, base["intent_score"] + VC_FUNDED_INTENT_BOOST))


class TestPortfolioIndex:
    def test_domain_and_distinctive_name_both_match(self):
        idx = PortfolioIndex()
        idx.add("Craft Ventures", "Supabase", "www.supabase.com")
        idx.add("Wing Venture Capital", "Supabase Inc", None)
        assert idx.funds_for("Something Else", "supabase.com") == ["Craft Ventures", "Wing Venture Capital"][:1]
        assert idx.funds_for("Supabase", None) == ["Craft Ventures", "Wing Venture Capital"]

    def test_short_names_never_match_by_name(self):
        idx = PortfolioIndex()
        idx.add("Fund", "Box", None)
        assert idx.funds_for("Box", None) == []


# ── 2. Partner lane ──────────────────────────────────────────────────────

class FakeScraper:
    api_key = "test"

    def __init__(self, results_by_query):
        self.results_by_query = results_by_query
        self.queries = []

    def _search(self, query):
        self.queries.append(query)
        for key, items in self.results_by_query.items():
            if key in query:
                return items
        return []


COMPLIANCE_HOME = """<html><head><meta property="og:site_name" content="Ledgerline Assurance">
<meta name="description" content="SOC 2 audits and readiness for startups."></head>
<body><h1>SOC 2 audit and readiness assessments for SaaS companies</h1>
<p>Our clients rely on us for attestations.</p></body></html>"""
COMPLIANCE_ABOUT = "<html><body><h2>Leadership</h2><div>Dana Whitfield</div><div>Managing Partner</div></body></html>"
STAFFING_HOME = """<html><head><title>TalentBridge Staffing</title></head><body>
<p>We are an IT staffing agency. Our clients are hiring SOC 2 audit specialists.</p></body></html>"""
BLOG_HOME = "<html><head><title>Random Blog</title></head><body><p>Ten tips for travel.</p></body></html>"


class TestPartnerLane:
    def _run(self, routes, results, **kw):
        scraper = FakeScraper(results)
        rows, stats = find_partner_leads(
            [{"segment": "compliance_firm", "query": "\"SOC 2 audit\" firm"}],
            scraper=scraper, http_get=http(routes), sleep=lambda _s: None, **kw)
        return rows, stats, scraper

    def test_a_firm_with_evidence_and_a_named_partner_is_written(self):
        rows, stats, scraper = self._run(
            {"ledgerline.example/robots.txt": Resp(404), "ledgerline.example/about": COMPLIANCE_ABOUT,
             "ledgerline.example/": COMPLIANCE_HOME},
            {"SOC 2": [{"link": "https://www.ledgerline.example/services", "title": "x", "snippet": ""},
                       {"link": "https://clutch.co/profile/ledgerline", "title": "x", "snippet": ""}]})
        assert stats.dropped_not_a_firm_host == 1  # clutch is never read
        [row] = rows
        assert row["product_id"] == PARTNERS_PRODUCT_ID and row["source"] == "partner_signal"
        assert row["company"] == "Ledgerline Assurance" and row["partner_type"] == "compliance_firm"
        assert (row["first_name"], row["last_name"], row["title"]) == ("Dana", "Whitfield", "Managing Partner")
        assert row["status"] == "pending_dm" and row["lead_route"] == "manual_linkedin"
        assert len(scraper.queries) == 1  # contact came from the site: no Brave lookup

    def test_staffing_is_the_only_exclusion(self):
        rows, stats, _ = self._run(
            {"talentbridge.example/robots.txt": Resp(404), "talentbridge.example/": STAFFING_HOME},
            {"SOC 2": [{"link": "https://talentbridge.example/", "title": "", "snippet": ""}]})
        assert rows == [] and stats.dropped == {"excluded_staffing": 1}

    def test_a_site_that_does_not_do_the_work_is_dropped(self):
        rows, stats, _ = self._run(
            {"blog.example/robots.txt": Resp(404), "blog.example/": BLOG_HOME},
            {"SOC 2": [{"link": "https://blog.example/top-10", "title": "", "snippet": ""}]})
        assert rows == [] and stats.dropped == {"site_does_not_do_the_work": 1}

    def test_robots_disallow_is_honoured(self):
        rows, stats, _ = self._run(
            {"ledgerline.example/robots.txt": Resp(200, "User-agent: *\nDisallow: /"),
             "ledgerline.example/": COMPLIANCE_HOME},
            {"SOC 2": [{"link": "https://ledgerline.example/", "title": "", "snippet": ""}]})
        assert rows == [] and stats.dropped == {"robots_disallowed": 1}

    def test_brave_founder_lookup_only_when_the_site_names_nobody(self):
        rows, stats, scraper = self._run(
            {"ledgerline.example/robots.txt": Resp(404), "ledgerline.example/": COMPLIANCE_HOME},
            {"site:linkedin.com/in": [{"link": "https://www.linkedin.com/in/dana-w",
                                       "title": "Dana Whitfield - Founder - Ledgerline Assurance | LinkedIn",
                                       "snippet": "Founder at Ledgerline Assurance"}],
             "SOC 2": [{"link": "https://ledgerline.example/", "title": "", "snippet": ""}]})
        [row] = rows
        assert row["linkedin_url"] == "https://www.linkedin.com/in/dana-w" and row["title"] == "Founder"
        assert stats.queries == 1 and stats.queries_contact == 1

    def test_hard_query_cap(self):
        scraper = FakeScraper({})
        templates = [{"segment": "ai_agency", "query": f"q{i}"} for i in range(10)]
        find_partner_leads(templates, scraper=scraper, http_get=http({}), max_queries=4, sleep=lambda _s: None)
        assert len(scraper.queries) == 4

    def test_consultancies_pass_only_in_this_lane(self):
        """The same firm: excluded as a prospect, kept as a partner."""
        c = classify_company("Ledgerline Advisory", "We help clients with SOC 2 audits. A boutique consulting firm.")
        assert CONSULTING_EXCLUSION_POLICY.decide(c)[0] is True
        assert PARTNER_EXCLUSION_POLICY.decide(c)[0] is False

    @pytest.mark.parametrize("url,ok", [
        ("https://clutch.co/x", False), ("https://www.upwork.com/x", False),
        ("https://www.linkedin.com/company/x", False), ("https://acme-security.io/vciso", True),
    ])
    def test_directories_are_never_firms(self, url, ok):
        assert bool(firm_host(url)) is ok


class TestPartnerContactFit:
    def test_firm_leaders_accepted_where_buyer_rules_reject(self):
        assert evaluate_contact_fit("Managing Partner", open_role_count=None).accepted is False
        assert evaluate_partner_contact_fit("Managing Partner", "compliance_firm").accepted is True
        assert evaluate_partner_contact_fit("Founder & CEO", "ai_agency").accepted is True

    def test_vc_platform_roles(self):
        assert evaluate_partner_contact_fit("Operating Partner, Talent", "vc_operating_partner").accepted
        assert evaluate_partner_contact_fit("Head of Platform", "vc_operating_partner").accepted
        assert not evaluate_partner_contact_fit("General Partner", "vc_operating_partner").accepted
        assert not evaluate_partner_contact_fit("Sales Manager", "ai_agency").accepted


# ── 3. VC operating partners ─────────────────────────────────────────────

TEAM_HTML = """<div><h3>Sunil Dhaliwal</h3><p>General Partner</p></div>
<div><h3>Natasha Katoni</h3><p>Operating Partner, Talent</p></div>
<div><h3>Mark LaRosa</h3><p>Operating Partner, GTM and Compute</p></div>
<div><h3>Jessica Hoffman</h3><p>Partner, Head of Platform Team</p></div>
<div><p>Sunil Chokshi is a partner at Wing and Head of Talent.</p></div>
<div><p>Operating Partner</p></div>"""


class TestVcParsing:
    def test_team_page_pairs_names_with_adjacent_titles_only(self):
        contacts = parse_team_page(TEAM_HTML)
        names = [c["name"] for c in contacts]
        # Platform ranks first, then talent; GTM is off-function; a bio
        # sentence and a title with no name beside it produce nothing.
        assert names == ["Jessica Hoffman", "Natasha Katoni"]

    def test_portfolio_page_reads_outbound_company_links(self):
        page = """<a href="https://supabase.com">Supabase</a> <a href="https://www.glean.com/">Visit site</a>
        <a href="https://twitter.com/fund">Twitter</a> <a href="https://craftventures.com/team">Team</a>"""
        assert parse_portfolio_page(page, "craftventures.com") == [
            {"company": "Supabase", "domain": "supabase.com"},
            {"company": "Glean", "domain": "glean.com"},
        ]


# ── 4. Hacker News ───────────────────────────────────────────────────────

def _post(text, kind=WHO_IS_HIRING):
    return HnPost(item_id=1, thread_kind=kind, thread_title="Ask HN: Who is hiring? (October 2026)",
                  by="someone", posted_at=None, text=text)


class TestHnFilters:
    def test_infra_term_required(self):
        assert keep_post(_post("Acme | Senior SRE | Remote"))[0]
        assert keep_post(_post("Acme | Platform Engineer | Contract | Azure, Terraform"))[2] == [
            "azure", "platform engineer", "terraform"]

    def test_contract_alone_is_not_enough(self):
        assert keep_post(_post("Sendery | iOS Engineer | Tokyo | Contract")) == (False, "no_infra_term", [])

    def test_bare_platform_is_not_platform_engineering(self):
        assert keep_post(_post("Acme | Backend Engineer | our platform serves millions"))[0] is False

    def test_freelancer_thread_keeps_buyers_only(self):
        seeking_work = _post("SEEKING WORK | EU | Senior DevOps engineer", FREELANCER)
        seeking_freelancer = _post("SEEKING FREELANCER | Acme | Remote | Terraform help needed", FREELANCER)
        assert keep_post(seeking_work) == (False, "freelancer_thread_seeking_work", [])
        assert keep_post(seeking_freelancer)[0] is True

    def test_company_and_domain_from_the_header(self):
        p = _post("Northwind Systems | SRE | Remote (US) | https://northwind.io/careers\nWe run Terraform.")
        assert company_from_post(p) == "Northwind Systems"
        assert domain_from_post(p, "Northwind Systems") == "northwind.io"
        assert company_from_post(_post("Founding Engineer (Security / AI) — Stealth Project")) is None
        assert company_from_post(_post("Stealth Startup | DevOps | Remote")) is None

    def test_ats_and_social_links_are_not_the_company_domain(self):
        p = _post("Acme | SRE | https://jobs.lever.co/acme/123 https://www.linkedin.com/company/acme")
        assert domain_from_post(p, "Acme") is None


def test_find_hn_leads_end_to_end():
    api = "hacker-news.firebaseio.com/v0"
    routes = {
        f"{api}/user/whoishiring.json": Resp(200, payload={"submitted": [100]}),
        f"{api}/user/jon_north.json": Resp(200, payload={"submitted": [200]}),
        f"{api}/item/100.json": Resp(200, payload={"id": 100, "title": "Ask HN: Who is hiring? (October 2026)",
                                                   "time": NOW_TS, "kids": [101, 102, 103, 104]}),
        f"{api}/item/200.json": Resp(200, payload={"id": 200, "title": "Ask HN: Freelancer? Seeking freelancer? (October 2026)",
                                                   "time": NOW_TS, "kids": [201]}),
        f"{api}/item/101.json": Resp(200, payload={"id": 101, "by": "cto_jane", "time": 1790866927,
                                                   "text": "Northwind | Senior SRE | Remote | https://northwind.io<p>Our ARM-to-Bicep migration needs help. Contract ok."}),
        f"{api}/item/102.json": Resp(200, payload={"id": 102, "text": "Sendery | iOS Engineer | Contract"}),
        f"{api}/item/103.json": Resp(200, payload={"id": 103, "deleted": True}),
        f"{api}/item/104.json": Resp(200, payload={"id": 104, "text": "TalentBridge | DevOps | We are an IT staffing agency placing DevOps engineers"}),
        f"{api}/item/201.json": Resp(200, payload={"id": 201, "text": "SEEKING WORK | DevOps contractor"}),
    }
    rows, stats = find_hn_leads("p-consulting", http_get=http(routes), existing_companies=set())
    assert stats.threads_found == 2 and stats.posts_read == 4
    assert stats.dropped == {"no_infra_term": 1, "deleted_or_dead": 1, "freelancer_thread_seeking_work": 1}
    assert stats.excluded == {"staffing": 1}
    [row] = rows
    assert row["source"] == "hn_thread" and row["company"] == "Northwind" and row["domain"] == "northwind.io"
    assert row["lead_route"] == "manual_linkedin" and row["status"] == "company_qualified"
    assert row["job_posting_url"] == "https://news.ycombinator.com/item?id=101"
    assert "ARM-to-Bicep migration" in row["signal_text"]
    assert any("engagement: contract" in r for r in row["score_reasons"])


def test_hn_dedupes_against_existing_prospects():
    api = "hacker-news.firebaseio.com/v0"
    routes = {
        f"{api}/user/whoishiring.json": Resp(200, payload={"submitted": [100]}),
        f"{api}/user/jon_north.json": Resp(200, payload={"submitted": []}),
        f"{api}/item/100.json": Resp(200, payload={"id": 100, "title": "Ask HN: Who is hiring? (October 2026)", "time": NOW_TS, "kids": [101]}),
        f"{api}/item/101.json": Resp(200, payload={"id": 101, "text": "Northwind | SRE | Remote"}),
    }
    rows, stats = find_hn_leads("p", http_get=http(routes), existing_companies={"northwind"})
    assert rows == [] and stats.duplicates == 1


# ── MKT-O2: pain phrases reach touch 1; partners get a partnership note ──

def _capture(monkeypatch, replies):
    seen = []

    def fake(system, user, anthropic_client=None, max_tokens=1024):
        seen.append({"system": system, "user": user})
        return json.dumps(replies[min(len(seen) - 1, len(replies) - 1)])

    monkeypatch.setattr(o2, "_analyze", fake)
    return seen


def test_pain_phrases_are_in_the_prompt_and_context(monkeypatch):
    seen = _capture(monkeypatch, [{"touch_1": "Hi Ana, the Terraform migration your posting mentions -- curious?",
                                   "touch_2": "Happy to help.", "touch_3": "Last note."}])
    lead = {"first_name": "Ana", "company": "Northwind", "title": "CTO", "lead_route": "manual_linkedin",
            "pain_phrases": ["Terraform migration"],
            "pain_evidence": [{"phrase": "Terraform migration", "snippet": "Lead the Terraform migration."}]}
    out = o2._write_route_aware_sequence(lead, "9b6c8f36-985f-4d7a-a416-c40da89e23af")
    assert PAIN_RULE in seen[0]["system"]
    assert "Lead the Terraform migration." in seen[0]["user"]
    assert len(seen) == 1 and "Terraform migration" in out["touch_1"]


def test_a_touch_1_that_drops_the_pain_is_regenerated_once(monkeypatch):
    seen = _capture(monkeypatch, [
        {"touch_1": "Hi Ana, saw you are hiring.", "touch_2": "x", "touch_3": "y"},
        {"touch_1": "Hi Ana, the landing zone work in your posting -- worth a chat?", "touch_2": "x", "touch_3": "y"},
    ])
    lead = {"first_name": "Ana", "company": "N", "title": "CTO", "lead_route": "manual_linkedin",
            "pain_phrases": ["landing zone"], "pain_evidence": []}
    out = o2._write_route_aware_sequence(lead, "p")
    assert len(seen) == 2 and "landing zone" in out["touch_1"]
    assert "MUST contain" in seen[1]["user"]


def test_a_lead_without_pain_gets_the_unchanged_prompt(monkeypatch):
    seen = _capture(monkeypatch, [{"touch_1": "Hi Ana", "touch_2": "x", "touch_3": "y"}])
    o2._write_route_aware_sequence({"first_name": "Ana", "lead_route": "manual_linkedin"}, "p")
    assert PAIN_RULE not in seen[0]["system"] and "pain_evidence" not in seen[0]["user"]


def test_partner_leads_get_a_partnership_note_without_a_sales_close(monkeypatch):
    seen = _capture(monkeypatch, [{"touch_1": "Hi Dana, your SOC 2 practice --", "touch_2": "Open to referring?",
                                   "touch_3": "Last one."}])
    lead = {"first_name": "Dana", "company": "Ledgerline", "title": "Managing Partner",
            "lead_route": "outbound_email", "partner_type": "compliance_firm",
            "signal_text": "SOC 2 audits and readiness for startups."}
    out = o2._write_route_aware_sequence(lead, PARTNERS_PRODUCT_ID)
    assert "PARTNERSHIP" in seen[0]["system"] and "implementation partner" in seen[0]["system"]
    assert "SOC 2 audits and readiness" in seen[0]["user"]
    # Always the manual LinkedIn channel, and no appended demo/offer URL.
    assert out["drafted_for_route"] == "manual_linkedin" and "http" not in out["touch_2"]


def test_partner_contacts_are_draftable_buyers_rules_are_not_used():
    ok, reason = o2.contact_is_draftable({"contact_status": "found", "first_name": "Dana",
                                          "title": "Managing Partner", "partner_type": "compliance_firm"})
    assert ok, reason
    ok, _ = o2.contact_is_draftable({"contact_status": "found", "first_name": "Dana",
                                     "title": "Managing Partner"})
    assert not ok  # the same title as a PROSPECT contact is still rejected


def test_pain_phrase_missing():
    assert pain_phrase_missing("nothing", ["Key Vault"]) is True
    assert pain_phrase_missing("your key-vault rollout", ["Key Vault"]) is False
    assert pain_phrase_missing("anything", []) is False


# ── Lanes and counters ───────────────────────────────────────────────────

def test_lanes_split_partners_from_prospects():
    from api.routers.outreach_lanes import in_lane
    assert in_lane(PARTNERS_PRODUCT_ID, "partners") and not in_lane(PARTNERS_PRODUCT_ID, "prospects")
    assert in_lane("consulting", "prospects") and not in_lane("consulting", "partners")
    assert in_lane(PARTNERS_PRODUCT_ID, None) and in_lane("consulting", None)


def test_digest_counters_exclude_the_partner_lane():
    from core.outreach_counters import count_outreach
    leads = [{"product_id": "c", "contact_status": "pending"},
             {"product_id": PARTNERS_PRODUCT_ID, "contact_status": "pending"}]
    seqs = [{"product_id": PARTNERS_PRODUCT_ID, "status": "pending_hitl"}]
    c = count_outreach(leads, seqs, [], exclude_product_ids=(PARTNERS_PRODUCT_ID,))
    assert c["awaiting_buyer"] == 1 and c["awaiting_approval"] == 0
    p = count_outreach(leads, seqs, [], product_id=PARTNERS_PRODUCT_ID)
    assert p["awaiting_buyer"] == 1 and p["awaiting_approval"] == 1


# ── Loop Map ─────────────────────────────────────────────────────────────

def test_partner_sent_desk():
    from datetime import datetime, timezone
    from core.loop_map_rules import evaluate
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    pid = PARTNERS_PRODUCT_ID
    S = {"seqs": [{"product_id": pid, "status": "approved_manual"}]}
    assert evaluate("partner.sent", S, pid, now).status == "waiting_on_you"
    S = {"seqs": [{"product_id": pid, "status": "approved_manual", "touch_1_sent_at": "2026-10-17T10:00:00+00:00"}]}
    assert evaluate("partner.sent", S, pid, now).status == "working"
    assert evaluate("partner.sent", {"seqs": []}, pid, now).status == "not_proven"


def test_hn_runs_do_not_stand_in_for_the_weekly_sweep():
    from datetime import datetime, timezone
    from core.loop_map_rules import evaluate
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    runs = [
        {"product_id": "c", "status": "complete", "started_at": "2026-10-01T00:00:00+00:00",
         "completed_at": "2026-10-01T01:00:00+00:00", "leads_found": 6, "sources_used": ["job_posting_signal"]},
        {"product_id": "c", "status": "complete", "started_at": "2026-10-18T00:00:00+00:00",
         "completed_at": "2026-10-18T00:05:00+00:00", "leads_found": 9, "sources_used": ["hn_thread"]},
    ]
    st = evaluate("outbound.lead_run", {"runs": runs, "brave_budget": {}}, "c", now)
    assert st.status == "blocked" and "16 days" in st.evidence


def test_buyer_research_is_reachable_with_the_marketing_key(monkeypatch):
    """Found 2026-10-18: production answered 401 'Not enough segments' -- the
    tenant middleware never exempted this prefix, so the lane never loaded."""
    from fastapi.testclient import TestClient
    import api.routers.buyer_research as br
    from api.main import app
    from tests.conftest import FakeSupabase
    monkeypatch.setattr(br, "get_supabase", lambda: FakeSupabase({"mse_leads": []}))
    r = TestClient(app).get("/marketing/buyer-research?lane=partners",
                            headers={"Authorization": "Bearer test-marketing-api-key"})
    assert r.status_code == 200, r.text
    assert r.json() == {"tasks": [], "count": 0}


# ── Regressions from the first production run (2026-10-18) ───────────────

def test_hn_picks_the_newest_thread_across_posters_not_the_first_found():
    """whoishiring still lists its October 2025 freelancer thread; jon_north
    posts the current one. The first live run read the 2025 thread."""
    from agents.marketing.hn_scout import find_threads
    api = "hacker-news.firebaseio.com/v0"
    year = 365 * 86400
    routes = {
        f"{api}/user/whoishiring.json": Resp(200, payload={"submitted": [100, 90]}),
        f"{api}/user/jon_north.json": Resp(200, payload={"submitted": [200]}),
        f"{api}/item/100.json": Resp(200, payload={"id": 100, "title": "Ask HN: Who is hiring? (October 2026)", "time": NOW_TS}),
        f"{api}/item/90.json": Resp(200, payload={"id": 90, "title": "Ask HN: Freelancer? Seeking freelancer? (October 2025)", "time": NOW_TS - year}),
        f"{api}/item/200.json": Resp(200, payload={"id": 200, "title": "Ask HN: Freelancer? Seeking freelancer? (October 2026)", "time": NOW_TS - 3600}),
    }
    assert [t["id"] for t in find_threads(http(routes))] == [100, 200]


def test_a_stale_thread_is_never_read_as_current():
    from agents.marketing.hn_scout import find_threads
    api = "hacker-news.firebaseio.com/v0"
    routes = {
        f"{api}/user/whoishiring.json": Resp(200, payload={"submitted": [90]}),
        f"{api}/user/jon_north.json": Resp(200, payload={"submitted": []}),
        f"{api}/item/90.json": Resp(200, payload={"id": 90, "title": "Ask HN: Freelancer? Seeking freelancer? (October 2025)", "time": NOW_TS - 365 * 86400}),
    }
    assert find_threads(http(routes)) == []


@pytest.mark.parametrize("name,ok", [("Case Studies", False), ("Army Veteran", False),
                                     ("Steve Case", True), ("Dana Whitfield", True)])
def test_page_furniture_is_not_a_person(name, ok):
    from agents.marketing.contact_discovery import _plausible_name
    assert _plausible_name(name) is ok


def test_a_heading_about_a_title_is_not_a_title():
    from agents.marketing.contact_discovery import adjacent_name_title_pairs
    from agents.marketing.contact_fit import PARTNER_FIRM_TITLE_RE
    segs = ["Case Studies", "From the President", "Rob Black", "Founder"]
    assert adjacent_name_title_pairs(segs, PARTNER_FIRM_TITLE_RE) == [("Rob Black", "Founder")]


COURSE_HOME = """<html><head><meta property="og:site_name" content="Launch My AI Automation Agency"></head><body>
<p>Start an AI automation agency in 30 days. We build your site and playbook.</p><p>Our clients succeed.</p></body></html>"""
VENDOR_HOME = """<html><head><title>Cynomi</title></head><body>
<p>MSSPs leverage Cynomi’s Security Growth platform to deliver vCISO services. Our clients scale.</p></body></html>"""
LEGAL_HOME = """<html><head><title>Digital Reference</title></head><body>
<p>Artificial Intelligence Legal Professional Services. Our clients trust us.</p></body></html>"""


@pytest.mark.parametrize("page,segment,reason", [
    (COURSE_HOME, "ai_agency", "course_or_educator"),
    (VENDOR_HOME, "fractional_ciso", "software_vendor"),
    (LEGAL_HOME, "ai_agency", "not_an_agency"),
])
def test_live_false_positives_are_dropped(page, segment, reason):
    scraper = FakeScraper({"q": [{"link": "https://firm.example/", "title": "", "snippet": ""}]})
    rows, stats = find_partner_leads([{"segment": segment, "query": "q"}], scraper=scraper,
                                     http_get=http({"firm.example/robots.txt": Resp(404), "firm.example/": page}),
                                     sleep=lambda _s: None)
    assert rows == [] and stats.dropped == {reason: 1}


def test_firm_name_drops_the_tagline():
    from agents.marketing.partner_sourcing import firm_name
    page = '<meta property="og:site_name" content="Nate Herk · AI automation made easy">'
    assert firm_name(page, "nateherk.com") == "Nate Herk"


class _RecDb:
    """Records writes; serves canned selects per table."""

    def __init__(self, tables):
        self.tables, self.writes, self.fail_next_upsert = tables, [], 0

    def table(self, name):
        db = self

        class Q:
            def __init__(self):
                self.op = None

            def select(self, *a, **k): return self
            def eq(self, *a, **k): return self
            def limit(self, *a, **k): return self
            def order(self, *a, **k): return self

            def upsert(self, payload, on_conflict=None):
                self.op = ("upsert", payload); return self

            def update(self, payload):
                self.op = ("update", payload); return self

            def insert(self, payload):
                self.op = ("insert", payload); return self

            def execute(self):
                import httpx
                if self.op and self.op[0] == "upsert" and db.fail_next_upsert:
                    db.fail_next_upsert -= 1
                    raise httpx.RemoteProtocolError("Server disconnected")
                if self.op:
                    db.writes.append((name,) + self.op)
                    return type("R", (), {"data": [self.op[1]]})()
                return type("R", (), {"data": db.tables.get(name, [])})()
        return Q()


def test_vc_scout_batches_portfolio_writes_keeps_probed_boards_and_retries(monkeypatch):
    from agents.marketing import vc_partners
    funds = [{"id": "f1", "name": "Craft Ventures", "domain": "craftventures.com",
              "team_url": None, "portfolio_url": "https://craftventures.com/portfolio", "active": True}]
    page = "".join(f'<a href="https://co{i}.com">Co{i}</a>' for i in range(250))
    db = _RecDb({
        "mse_vc_funds": funds,
        "mse_vc_portfolio_companies": [{"fund_id": "f1", "domain": "co0.com", "board_status": "none_found"}],
    })
    db.fail_next_upsert = 1  # the first batch hits a dropped connection
    monkeypatch.setattr(vc_partners, "_allowed", lambda *a, **k: True)
    rows, stats = vc_partners.run_vc_scout(
        db, http_get=http({"craftventures.com/portfolio": page}), max_probes=0, sleep=lambda _s: None)
    upserts = [w for w in db.writes if w[0] == "mse_vc_portfolio_companies" and w[1] == "upsert"]
    assert all(isinstance(u[2], list) and len(u[2]) <= vc_partners.UPSERT_BATCH for u in upserts)
    written = [r for u in upserts for r in u[2]]
    assert len(written) == 250 and stats.portfolio_companies == 250
    # co0 was probed before: its board_status is not reset to not_probed.
    assert "board_status" not in next(r for r in written if r["domain"] == "co0.com")


def test_the_lane_is_applied_before_the_limit():
    from api.routers.outreach_lanes import scope_to_lane
    calls = []

    class Q:
        def eq(self, k, v): calls.append(("eq", k, v)); return self
        def neq(self, k, v): calls.append(("neq", k, v)); return self

    scope_to_lane(Q(), "partners"); scope_to_lane(Q(), "prospects"); scope_to_lane(Q(), None)
    assert calls == [("eq", "product_id", PARTNERS_PRODUCT_ID), ("neq", "product_id", PARTNERS_PRODUCT_ID)]
