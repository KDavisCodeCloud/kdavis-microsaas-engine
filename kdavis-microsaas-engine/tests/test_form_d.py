"""Lead-source expansion, Phase 3 (2026-11-15): SEC Form D. No network."""

from datetime import date

import pytest

from agents.marketing import form_d as fd

INDEX = """Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
D                ACME ROBOTICS, INC.                                           2164431     20261113    edgar/data/2164431/0001470831-26-000994.txt
D                202607-M, a series of Tyrell Ventures Funds, LP               2147922     20261113    edgar/data/2147922/0002147922-26-000002.txt
D/A              Northwind Labs Inc                                            1999        20261113    edgar/data/1999/0000001999-26-000001.txt
10-K             Something Else Inc                                            5           20261113    edgar/data/5/0000000005-26-000001.txt
"""

DOC = """<?xml version="1.0"?>
<edgarSubmission><primaryIssuer><cik>0002164431</cik><entityName>ACME ROBOTICS, INC.</entityName>
<issuerAddress><stateOrCountry>CA</stateOrCountry></issuerAddress><entityType>Corporation</entityType>
<yearOfInc><value>2022</value></yearOfInc></primaryIssuer>
<relatedPersonsList>
 <relatedPersonInfo><relatedPersonName><firstName>Jane</firstName><lastName>Doe</lastName></relatedPersonName>
  <relatedPersonRelationshipList><relationship>Executive Officer</relationship><relationship>Director</relationship></relatedPersonRelationshipList>
  <relationshipClarification>CTO and Co-Founder</relationshipClarification></relatedPersonInfo>
 <relatedPersonInfo><relatedPersonName><firstName>Bob</firstName><lastName>Roe</lastName></relatedPersonName>
  <relatedPersonRelationshipList><relationship>Executive Officer</relationship></relatedPersonRelationshipList>
  <relationshipClarification></relationshipClarification></relatedPersonInfo>
 <relatedPersonInfo><relatedPersonName><firstName>Vic</firstName><lastName>Capital</lastName></relatedPersonName>
  <relatedPersonRelationshipList><relationship>Director</relationship></relatedPersonRelationshipList></relatedPersonInfo>
</relatedPersonsList>
<offeringData><industryGroup><industryGroupType>Other Technology</industryGroupType></industryGroup>
<typeOfFiling><newOrAmendment><isAmendment>false</isAmendment></newOrAmendment>
<dateOfFirstSale><value>2026-11-01</value></dateOfFirstSale></typeOfFiling>
<offeringSalesAmounts><totalOfferingAmount>12000000</totalOfferingAmount><totalAmountSold>9500000</totalAmountSold></offeringSalesAmounts>
</offeringData></edgarSubmission>"""


def test_index_parsing_keeps_d_and_amendments_only():
    es = [e for e in fd.parse_index(INDEX) if e.form in ("D", "D/A")]
    assert [e.company for e in es] == ["ACME ROBOTICS, INC.", "202607-M, a series of Tyrell Ventures Funds, LP",
                                       "Northwind Labs Inc"]
    assert es[0].accession == "0001470831-26-000994" and es[0].filed_on == date(2026, 11, 13)
    assert fd.primary_doc_url(es[0]) == \
        "https://www.sec.gov/Archives/edgar/data/2164431/000147083126000994/primary_doc.xml"


def test_index_url_is_the_official_daily_index():
    assert fd.index_url(date(2026, 11, 13)) == \
        "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/form.20261113.idx"


@pytest.mark.parametrize("name,vehicle", [
    ("202607-M, a series of Tyrell Ventures Funds, LP", True), ("Sunrise Real Estate Fund II", True),
    ("Acme Opportunity Fund", True), ("ACME ROBOTICS, INC.", False), ("Chai Discovery, Inc.", False),
])
def test_vehicle_names_are_skipped_before_fetching(name, vehicle):
    assert fd.is_vehicle_name(name) is vehicle


def test_primary_doc_fields():
    f = fd.parse_primary_doc(DOC)
    assert f["issuer_name"] == "ACME ROBOTICS, INC." and f["industry_group"] == "Other Technology"
    assert f["total_amount_sold"] == 9500000 and f["date_of_first_sale"] == "2026-11-01"
    assert [p["name"] for p in f["related_persons"]] == ["Jane Doe", "Bob Roe", "Vic Capital"]


def test_officers_only_and_titles_come_from_the_filing():
    f = {**fd.parse_primary_doc(DOC), "accession_no": "a-1", "filing_url": "u"}
    cands = fd.officer_candidates(f)
    assert [c["name"] for c in cands] == ["Jane Doe", "Bob Roe"], "a director-only person is not an officer"
    assert cands[0]["title"] == "CTO and Co-Founder" and cands[0]["confidence"] == "high"
    assert cands[1]["title"] == "Executive Officer (Form D)" and cands[1]["title_verdict"] != "allow"
    assert all(c["source"] == "form_d" and c["email"] is None for c in cands)


def test_legal_name_matches_ats_display_name():
    assert fd.norm("Chai Discovery, Inc.") == fd.norm("Chaidiscovery") == fd.norm("chai-discovery")


def test_slug_variants_never_guess_the_bare_first_word():
    assert fd.slug_variants("Acme Robotics, Inc.") == ["acmerobotics", "acme-robotics"]
    assert fd.slug_variants("AI, Inc.") == []


def test_consulting_gets_a_one_time_funding_boost_inside_180_days():
    from agents.marketing.mkt_lead_finder import CLOUD_DECODED_PRODUCT_ID, THDAGENTIC_CONSULTING_PRODUCT_ID
    filing = {"accession_no": "a-1", "filed_on": "2026-10-01", "date_of_first_sale": "2023-01-01",
              "total_amount_sold": 2_000_000}
    lead = {"product_id": THDAGENTIC_CONSULTING_PRODUCT_ID, "intent_score": 0.5, "score_reasons": []}
    up = fd.funding_update(lead, filing, date(2026, 11, 15))
    assert up["funding_date"] == "2026-10-01", "the filing date, not a years-old first sale"
    assert up["intent_score"] == 0.6 and "Form D funding" in up["score_reasons"][-1]
    assert "intent_score" not in fd.funding_update({**lead, "funding_date": "2026-09-01"}, filing, date(2026, 11, 15))
    assert "intent_score" not in fd.funding_update({**lead, "product_id": CLOUD_DECODED_PRODUCT_ID}, filing,
                                                   date(2026, 11, 15))
    old = {**filing, "filed_on": "2026-01-01"}
    assert "intent_score" not in fd.funding_update(lead, old, date(2026, 11, 15))


class Posting:
    def __init__(self, company=None, desc=""):
        self.company, self.description_text = company, desc


def test_guessed_board_must_name_the_company():
    assert fd.board_names_company([Posting("Acme Robotics")], "ACME ROBOTICS, INC.", "greenhouse", "acmerobotics")
    assert not fd.board_names_company([Posting("Acme Rockets")], "ACME ROBOTICS, INC.", "greenhouse", "acmerobotics")
    # Lever/Ashby have no display name: full-name token AND the JD names it.
    assert fd.board_names_company([Posting("Acmerobotics", "Acme Robotics builds arms")], "ACME ROBOTICS, INC.",
                                  "lever", "acmerobotics")
    assert not fd.board_names_company([Posting("Acmerobotics", "We build arms")], "ACME ROBOTICS, INC.",
                                      "lever", "acmerobotics")


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


def test_client_declares_itself_and_paces_requests():
    seen, sleeps = [], []
    clock = iter([0.0, 0.0, 0.05, 0.05, 1.0, 1.0])

    def get(url, **kw):
        seen.append(kw["headers"]["User-Agent"])
        return Resp(200, "ok")

    c = fd.SecClient(get, sleep=sleeps.append, clock=lambda: next(clock), ua="THD test ops@example.org")
    c.get("u1"); c.get("u2")
    assert seen == ["THD test ops@example.org"] * 2
    assert sleeps and abs(sleeps[0] - (0.2 - 0.05)) < 1e-9, "<=5 requests/second"


def test_user_agent_requires_a_contact_email(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "THD bot")
    with pytest.raises(RuntimeError, match="contact email"):
        fd.user_agent()
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "Kelvin <outreach@outreach.example.com>")
    assert fd.user_agent() == "THD Agentic Systems LLC outreach@outreach.example.com"


def test_404_is_not_retried_and_429_backs_off():
    calls, sleeps = [], []
    codes = iter([429, 200])

    def get(url, **_k):
        calls.append(url)
        return Resp(next(codes), "x") if url == "throttled" else Resp(404)

    c = fd.SecClient(get, sleep=sleeps.append, clock=lambda: 100.0, ua="a b@c.d")
    assert c.get("missing") is None and len(calls) == 1
    assert c.get("throttled").status_code == 200 and 10 in sleeps
