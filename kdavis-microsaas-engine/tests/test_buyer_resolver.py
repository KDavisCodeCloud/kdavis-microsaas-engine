"""Lead-source expansion, Phase 5: the buyer resolver. No network, no SMTP."""

import pytest

from agents.marketing import buyer_resolver as br

LEAD = {"id": "L1", "product_id": "p", "company": "Northwind Labs", "domain": "northwindlabs.io",
        "status": "company_qualified", "contact_status": "pending", "buyer_candidates": None}


class R:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload, text

    def json(self):
        return self._p


def gh(routes):
    calls = []

    def get(url, **_k):
        calls.append(url)
        path = url.replace(br.GITHUB_API, "")
        return R(200, routes[path]) if path in routes else R(404)
    get.calls = calls
    return get


def no_pages(url, **_k):
    return R(404)


def grader(results):
    seen = []

    def g(first, last, domain, known_email=None, db=None):
        seen.append((first, last, known_email))
        return results.pop(0)
    g.seen = seen
    return g


VALID = {"email": "jane.doe@northwindlabs.io", "email_status": "verified", "email_grade": "valid"}
RISKY = {"email": "jane.doe@northwindlabs.io", "email_status": "verified", "email_grade": "risky"}


def test_verified_buyer_from_form_d_creates_the_contact():
    lead = {**LEAD, "buyer_candidates": [
        {"name": "Jane Doe", "title": "CTO and Co-Founder", "source": "form_d", "confidence": "high",
         "title_verdict": "allow", "evidence": "SEC Form D", "evidence_url": "u"}]}
    g = grader([VALID])
    up = br.resolve_lead(lead, db=None, http_get=no_pages, github_get=gh({}), grader=g)
    assert up["buyer_resolution"]["outcome"] == "auto_resolved"
    assert up["first_name"] == "Jane" and up["last_name"] == "Doe" and up["email_grade"] == "valid"
    assert up["status"] == "pending_dm" and up["lead_route"] == "outbound_email" and up["contact_status"] == "found"
    assert up["buyer_suggestion"]["source"] == "form_d"


def test_unverified_email_only_prefills():
    lead = {**LEAD, "buyer_candidates": [
        {"name": "Jane Doe", "title": "CTO", "source": "form_d", "confidence": "high", "title_verdict": "allow"}]}
    up = br.resolve_lead(lead, db=None, http_get=no_pages, github_get=gh({}), grader=grader([RISKY]))
    assert up["buyer_resolution"]["outcome"] == "prefilled"
    assert "first_name" not in up and "status" not in up, "a catch-all address never creates the contact"
    assert up["buyer_suggestion"]["email_grade"] == "risky"


def test_ambiguous_title_is_never_emailed_and_prefills():
    lead = {**LEAD, "buyer_candidates": [
        {"name": "Bob Roe", "title": "Executive Officer (Form D)", "source": "form_d", "confidence": "low",
         "title_verdict": "ambiguous"}]}
    g = grader([])
    up = br.resolve_lead(lead, db=None, http_get=no_pages, github_get=gh({}), grader=g)
    assert g.seen == [] and up["buyer_resolution"]["outcome"] == "prefilled"


def test_nothing_found_stays_manual():
    up = br.resolve_lead(dict(LEAD), db=None, http_get=no_pages, github_get=gh({}), grader=grader([]))
    assert up["buyer_resolution"]["outcome"] == "manual" and "buyer_suggestion" not in up
    assert set(up["buyer_resolution"]["sources_tried"]) == {"form_d", "company_page", "github", "hn_poster",
                                                            "search_snippet"}


def test_github_org_must_be_on_the_leads_domain_and_bio_must_name_the_company():
    routes = {
        "/orgs/northwindlabs": {"login": "northwindlabs", "blog": "https://www.northwindlabs.io"},
        "/orgs/northwindlabs/public_members?per_page=100": [{"login": "jd"}, {"login": "ex"}, {"login": "ic"}],
        "/users/jd": {"login": "jd", "name": "Jane Doe", "bio": "Co-founder & CTO @northwindlabs",
                      "html_url": "https://github.com/jd"},
        "/users/ex": {"login": "ex", "name": "Ex Person", "bio": "CTO at OtherCo", "company": "OtherCo"},
        "/users/ic": {"login": "ic", "name": "Ian Coder", "bio": "Platform engineer", "company": "Northwind Labs"},
    }
    out = br.from_github(LEAD, gh(routes), token="t")
    assert [c["name"] for c in out] == ["Jane Doe"]
    assert out[0]["source"] == "github" and out[0]["title_verdict"] == "allow"


def test_github_org_on_another_domain_is_rejected():
    routes = {"/orgs/northwindlabs": {"login": "northwindlabs", "blog": "https://northwind-games.com"}}
    assert br.from_github(LEAD, gh(routes), token="t") == []


def test_hn_poster_email_is_verified_directly():
    lead = {**LEAD, "buyer_candidates": [
        {"name": "Jane Doe", "title": "CTO", "source": "hn_poster", "confidence": "medium",
         "title_verdict": "allow", "email": "jane@northwindlabs.io"}]}
    g = grader([{**VALID, "email": "jane@northwindlabs.io"}])
    up = br.resolve_lead(lead, db=None, http_get=no_pages, github_get=gh({}), grader=g)
    assert g.seen == [("Jane", "Doe", "jane@northwindlabs.io")]
    assert up["email"] == "jane@northwindlabs.io" and up["buyer_resolution"]["outcome"] == "auto_resolved"


def test_ranking_prefers_allowed_titles_then_confidence_then_source_order():
    cands = [
        {"name": "A One", "title": "Executive Officer (Form D)", "source": "form_d", "confidence": "low",
         "title_verdict": "ambiguous"},
        {"name": "B Two", "title": "CTO", "source": "github", "confidence": "medium", "title_verdict": "allow"},
        {"name": "C Three", "title": "VP Engineering", "source": "company_page", "confidence": "medium",
         "title_verdict": "allow"},
        {"name": None, "title": "founder", "source": "hn_poster", "confidence": "low", "title_verdict": "allow"},
    ]
    assert [c["name"] for c in br.rank_candidates(cands)] == ["C Three", "B Two", "A One", None]


def test_snippet_resolver_is_off_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_SEARCH_SNIPPET_RESOLVER", raising=False)

    class Boom:
        api_key = "k"

        def _search(self, *_a, **_k):
            raise AssertionError("must not search when the flag is off")
    assert br.from_search_snippets(LEAD, Boom(), db=None) == []


def test_at_most_two_email_attempts_per_lead():
    cands = [{"name": f"P{i} Q", "title": "CTO", "source": "github", "confidence": "medium",
              "title_verdict": "allow"} for i in range(4)]
    lead = {**LEAD, "buyer_candidates": None}
    g = grader([RISKY, RISKY, RISKY])
    import agents.marketing.buyer_resolver as mod
    orig = mod.from_github
    mod.from_github = lambda *_a, **_k: cands
    try:
        br.resolve_lead(lead, db=None, http_get=no_pages, github_get=gh({}), grader=g)
    finally:
        mod.from_github = orig
    assert len(g.seen) == 2
