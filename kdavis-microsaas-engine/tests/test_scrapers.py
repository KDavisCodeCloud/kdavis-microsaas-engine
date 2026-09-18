"""
scrapers/google_search.py + scrapers/verticals/real_estate.py — httpx
calls are fully injected (http_get / http response mocks); no real
network access. Covers result parsing, not Google's/ADRE's actual live
response shape.
"""
from unittest.mock import MagicMock, patch

from scrapers.base import RawLead
from scrapers.brave_search import BraveSearchScraper
from scrapers.google_search import GoogleSearchScraper
from scrapers.verticals.real_estate import RealEstateScraper, _parse_results_table


def _fake_response(json_data=None, text="", status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


# ── google_search ────────────────────────────────────────────────────

def test_google_search_scraper_skips_gracefully_without_credentials():
    scraper = GoogleSearchScraper(api_key=None, engine_id=None)
    result = scraper.scrape("Phoenix AZ", {"search_templates": ["{title} {location}"], "job_titles": ["agent"]})
    assert result == []


def test_google_search_scraper_parses_linkedin_result_without_fetching_it():
    cse_response = _fake_response(json_data={
        "items": [{"title": "Jane Doe - Buyer's Agent | LinkedIn", "link": "https://www.linkedin.com/in/janedoe"}]
    })
    http_get = MagicMock(return_value=cse_response)

    with patch("scrapers.google_search.time.sleep"):
        scraper = GoogleSearchScraper(api_key="key", engine_id="cx", http_get=http_get)
        leads = scraper.scrape("Phoenix AZ", {
            "search_templates": ['site:linkedin.com/in "{title}" "{location}"'],
            "job_titles": ["buyer's agent"],
        })

    assert len(leads) == 1
    lead = leads[0]
    assert isinstance(lead, RawLead)
    assert lead.linkedin_url == "https://www.linkedin.com/in/janedoe"
    assert lead.name == "Jane Doe"
    assert lead.source == "google_search"
    # Never fetched the LinkedIn URL itself -- only one HTTP call total (the CSE query).
    assert http_get.call_count == 1


def test_google_search_scraper_follows_public_page_and_extracts_email():
    cse_response = _fake_response(json_data={
        "items": [{"title": "Jane Doe - Realtor | Acme Realty", "link": "https://acmerealty.com/agents/jane"}]
    })
    robots_response = _fake_response(status_code=404)  # no robots.txt -> allowed
    page_response = _fake_response(text="<html><body>Contact Jane at jane@acmerealty.com</body></html>")

    http_get = MagicMock(side_effect=[cse_response, robots_response, page_response])

    with patch("scrapers.google_search.time.sleep"):
        scraper = GoogleSearchScraper(api_key="key", engine_id="cx", http_get=http_get)
        leads = scraper.scrape("Phoenix AZ", {
            "search_templates": ['"{title}" "{location}" email site:acmerealty.com'],
            "job_titles": ["realtor"],
        })

    assert len(leads) == 1
    assert leads[0].email == "jane@acmerealty.com"
    assert leads[0].domain == "acmerealty.com"


def test_google_search_scraper_skips_excluded_domains():
    cse_response = _fake_response(json_data={
        "items": [{"title": "Jane Doe", "link": "https://www.zillow.com/agent/jane"}]
    })
    http_get = MagicMock(return_value=cse_response)

    with patch("scrapers.google_search.time.sleep"):
        scraper = GoogleSearchScraper(api_key="key", engine_id="cx", http_get=http_get)
        leads = scraper.scrape("Phoenix AZ", {
            "search_templates": ["{title} {location}"], "job_titles": ["agent"],
            "exclude_domains": ["zillow.com"],
        })

    assert leads == []


def test_google_search_scraper_hard_stops_at_daily_cap():
    http_get = MagicMock()
    scraper = GoogleSearchScraper(api_key="key", engine_id="cx", http_get=http_get, daily_query_count=100)
    leads = scraper.scrape("Phoenix AZ", {"search_templates": ["{title} {location}"], "job_titles": ["agent"]})
    assert leads == []
    http_get.assert_not_called()


# ── brave_search (Source 1, 2026-09-18 — replaces google_search above,
#    which is now dormant/unused; kept side by side for comparison) ────

def _fake_brave_response(results=None, text="", status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"web": {"results": results or []}}
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def test_brave_search_scraper_skips_gracefully_without_credentials():
    scraper = BraveSearchScraper(api_key=None)
    result = scraper.scrape("Phoenix AZ", {"search_templates": ["{title} {location}"], "job_titles": ["agent"]})
    assert result == []


def test_brave_search_scraper_parses_linkedin_result_without_fetching_it():
    brave_response = _fake_brave_response(results=[
        {"title": "Jane Doe - Buyer's Agent | LinkedIn", "url": "https://www.linkedin.com/in/janedoe", "description": ""}
    ])
    http_get = MagicMock(return_value=brave_response)

    with patch("scrapers.brave_search.time.sleep"):
        scraper = BraveSearchScraper(api_key="key", http_get=http_get)
        leads = scraper.scrape("Phoenix AZ", {
            "search_templates": ['site:linkedin.com/in "{title}" "{location}"'],
            "job_titles": ["buyer's agent"],
        })

    assert len(leads) == 1
    lead = leads[0]
    assert isinstance(lead, RawLead)
    assert lead.linkedin_url == "https://www.linkedin.com/in/janedoe"
    assert lead.name == "Jane Doe"
    assert lead.source == "brave_search"
    # Never fetched the LinkedIn URL itself -- only one HTTP call total (the Brave query).
    assert http_get.call_count == 1
    # X-Subscription-Token, not Authorization: Bearer -- Brave's own auth convention.
    _, kwargs = http_get.call_args
    assert kwargs["headers"]["X-Subscription-Token"] == "key"


def test_brave_search_scraper_follows_public_page_and_extracts_email():
    brave_response = _fake_brave_response(results=[
        {"title": "Jane Doe - Realtor | Acme Realty", "url": "https://acmerealty.com/agents/jane", "description": ""}
    ])
    robots_response = _fake_response(status_code=404)  # no robots.txt -> allowed
    page_response = _fake_response(text="<html><body>Contact Jane at jane@acmerealty.com</body></html>")

    http_get = MagicMock(side_effect=[brave_response, robots_response, page_response])

    with patch("scrapers.brave_search.time.sleep"):
        scraper = BraveSearchScraper(api_key="key", http_get=http_get)
        leads = scraper.scrape("Phoenix AZ", {
            "search_templates": ['"{title}" "{location}" email site:acmerealty.com'],
            "job_titles": ["realtor"],
        })

    assert len(leads) == 1
    assert leads[0].email == "jane@acmerealty.com"
    assert leads[0].domain == "acmerealty.com"


def test_brave_search_scraper_skips_excluded_domains():
    brave_response = _fake_brave_response(results=[
        {"title": "Jane Doe", "url": "https://www.zillow.com/agent/jane", "description": ""}
    ])
    http_get = MagicMock(return_value=brave_response)

    with patch("scrapers.brave_search.time.sleep"):
        scraper = BraveSearchScraper(api_key="key", http_get=http_get)
        leads = scraper.scrape("Phoenix AZ", {
            "search_templates": ["{title} {location}"], "job_titles": ["agent"],
            "exclude_domains": ["zillow.com"],
        })

    assert leads == []


def test_brave_search_scraper_hard_stops_at_monthly_cap():
    http_get = MagicMock()
    scraper = BraveSearchScraper(api_key="key", http_get=http_get, query_count=900)
    leads = scraper.scrape("Phoenix AZ", {"search_templates": ["{title} {location}"], "job_titles": ["agent"]})
    assert leads == []
    http_get.assert_not_called()


# ── real_estate ───────────────────────────────────────────────────────

_RESULTS_TABLE_HTML = """
<html><body>
<table>
  <tr><th>Name</th><th>License Type</th><th>Brokerage</th><th>City</th></tr>
  <tr><td>Jane Doe</td><td>Salesperson</td><td>Acme Realty</td><td>Phoenix</td></tr>
  <tr><td>John Smith</td><td>Broker</td><td>Sun Realty</td><td>Scottsdale</td></tr>
</table>
</body></html>
"""


def test_parse_results_table_returns_raw_leads():
    leads = _parse_results_table(_RESULTS_TABLE_HTML, location="Phoenix AZ")
    assert len(leads) == 2
    assert all(isinstance(lead, RawLead) for lead in leads)
    assert leads[0].name == "Jane Doe"
    assert leads[0].title == "Salesperson"
    assert leads[0].company == "Acme Realty"
    assert leads[0].source == "real_estate_db"


def test_parse_results_table_returns_empty_for_no_table():
    assert _parse_results_table("<html><body>no results</body></html>", location="Phoenix AZ") == []


def test_real_estate_scraper_scrape_returns_raw_leads():
    http_get = MagicMock(return_value=_fake_response(text=_RESULTS_TABLE_HTML))
    scraper = RealEstateScraper(http_get=http_get)

    with patch("scrapers.verticals.real_estate.time.sleep"):
        leads = scraper.scrape("Phoenix AZ", {"license_types": ["Salesperson"]})

    assert len(leads) == 2
    assert isinstance(leads[0], RawLead)


def test_real_estate_scraper_continues_past_a_failed_request():
    http_get = MagicMock(side_effect=[Exception("connection refused"), _fake_response(text=_RESULTS_TABLE_HTML)])
    scraper = RealEstateScraper(http_get=http_get)

    with patch("scrapers.verticals.real_estate.time.sleep"):
        leads = scraper.scrape("Phoenix AZ", {"license_types": ["Salesperson", "Broker"]})

    assert len(leads) == 2  # second license_type's request still succeeded
