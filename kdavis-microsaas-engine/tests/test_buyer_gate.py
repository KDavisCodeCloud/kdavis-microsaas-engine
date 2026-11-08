"""
tests/test_buyer_gate.py

Company exclusions and the verified-email rule (Kelvin, 2026-11-07).
"""

import pytest

from agents.marketing.buyer_gate import (
    LANE_FIND_THE_BUYER,
    LANE_REJECT,
    LANE_SEND_QUEUE,
    company_block_reason,
    company_size_block_reason,
    email_may_enter_send_queue,
    evaluate_lead,
    is_aggregator_domain,
    registrable_domain,
)


@pytest.mark.parametrize("domain", [
    "linkedin.com", "www.linkedin.com", "indeed.com", "ziprecruiter.com", "glassdoor.com",
    "monster.com", "simplyhired.com", "dice.com", "index.dev", "builtin.com", "builtinnyc.com",
    "wellfound.com", "otta.com", "boards.greenhouse.io", "job-boards.greenhouse.io",
    "jobs.lever.co", "jobs.ashbyhq.com", "apply.workable.com", "camberhealth.notion.site",
    "weworkremotely.com", "remoteok.com",
])
def test_job_boards_and_ats_hosts_are_never_the_company(domain):
    assert is_aggregator_domain(domain)
    assert company_block_reason("Anything", domain).startswith("aggregator_or_ats_domain")


@pytest.mark.parametrize("company,domain,expect", [
    ("CorVel", "corvel.com", "excluded_employer:corvel"),
    ("Honeywell Aerospace", "aerospace.honeywell.com", "excluded_employer:honeywell"),
    ("The Boeing Company", "boeing.com", "excluded_employer:boeing"),
    ("Security Risk Advisors", "securityriskadvisors.com", "competitor"),
    ("Coderio", "coderio.com", "competitor"),
    ("Acme Staffing", "acmestaff.com", "competitor_name"),
    ("Nimbus Cloud Consulting", "nimbuscc.io", "competitor_name"),
    ("BlueRock Managed Services", "bluerock.io", "competitor_name"),
    ("Stafford Gray", "staffordgray.com", "competitor"),
    ("Rearc", "rearc.io", "competitor"),
    ("Onhires", "onhires.com", "competitor"),
])
def test_competitors_and_excluded_employers_are_rejected(company, domain, expect):
    assert company_block_reason(company, domain).startswith(expect)


@pytest.mark.parametrize("company,domain", [
    ("Roboflow", "roboflow.com"),
    ("Customer.io", "customer.io"),
    ("Lumafield", "lumafield.com"),
    ("PDT Partners", "pdtpartners.com"),
])
def test_real_hiring_companies_pass(company, domain):
    assert company_block_reason(company, domain) is None


def test_a_company_needs_its_own_domain():
    assert company_block_reason("Northwind", None) == "no resolved company domain"


def test_registrable_domain():
    assert registrable_domain("https://jobs.lever.co/acme") == "lever.co"
    assert registrable_domain("mail.acme.co.uk") == "acme.co.uk"
    assert registrable_domain("jane@sub.acme.com") == "acme.com"


def test_size_band_rejects_only_on_evidence():
    assert company_size_block_reason(headcount_band="301-1000", open_roles=None, min_size=20, max_size=200)
    assert company_size_block_reason(headcount_band="1-19", open_roles=None, min_size=20, max_size=200)
    assert company_size_block_reason(headcount_band=None, open_roles=40, min_size=20, max_size=200)
    assert company_size_block_reason(headcount_band=None, open_roles=None, min_size=20, max_size=200) is None
    assert company_size_block_reason(headcount_band="50-99", open_roles=6, min_size=20, max_size=200) is None


class TestVerifiedEmailRule:
    @pytest.mark.parametrize("grade", ["risky", "invalid", "unknown", None, ""])
    def test_only_valid_enters_the_send_queue(self, grade):
        assert not email_may_enter_send_queue(grade)

    def test_valid_enters(self):
        assert email_may_enter_send_queue("valid")

    def _lead(self, **kw):
        lead = {"company": "Roboflow", "domain": "roboflow.com", "title": "CTO",
                "email": "kevin@roboflow.com", "email_grade": "valid", "size_proxy_open_roles": 6}
        lead.update(kw)
        return lead

    def test_buyer_with_verified_email_reaches_the_send_queue(self):
        assert evaluate_lead(self._lead(), max_size=500).lane == LANE_SEND_QUEUE

    @pytest.mark.parametrize("grade", ["risky", "unknown", "invalid", None])
    def test_unverified_email_goes_to_find_the_buyer_never_the_queue(self, grade):
        assert evaluate_lead(self._lead(email_grade=grade), max_size=500).lane == LANE_FIND_THE_BUYER

    def test_ic_contact_goes_to_find_the_buyer(self):
        assert evaluate_lead(self._lead(title="Senior Platform Engineer"), max_size=500).lane == LANE_FIND_THE_BUYER

    def test_email_on_another_domain_is_not_trusted(self):
        assert evaluate_lead(self._lead(email="kevin@gmail.com"), max_size=500).lane == LANE_FIND_THE_BUYER

    def test_aggregator_company_is_rejected_outright(self):
        assert evaluate_lead(self._lead(company="linkedin.com", domain="linkedin.com")).lane == LANE_REJECT


class TestSendPathEnforcesTheRule:
    def test_sender_blocks_every_non_valid_grade(self):
        import agents.marketing.mkt_o5_sequence_sender as o5
        for grade in ("risky", "invalid", "unknown"):
            assert o5._email_send_block_reason(
                {"email": "a@b.com", "lead_route": "outbound_email", "email_grade": grade},
                risky_budget_left=99) == f"email_grade={grade}"

    def test_routing_admits_only_valid(self):
        from agents.marketing.lead_qualification import email_may_route_outbound
        assert email_may_route_outbound("valid")
        assert not email_may_route_outbound("risky")


@pytest.mark.parametrize("domain", ["tubitv.it.com", "acme.us.com", "www.foo.uk.com", "bar.gb.net"])
def test_resold_second_level_domains_are_not_a_company_domain(domain):
    assert company_block_reason("Tubitv", domain).startswith("unverifiable_resold_domain")


@pytest.mark.parametrize("domain", ["tubitv.com", "acme.co.uk", "deloitte.it", "united.com"])
def test_ordinary_domains_are_not_mistaken_for_resold_ones(domain):
    r = company_block_reason("Northwind", domain)
    assert r is None or not r.startswith("unverifiable_resold_domain")
