"""Lead-source expansion, Phase 7: per-source yield + weekly target."""

from datetime import datetime, timezone

from core.lead_source_yield import WEEKLY_TARGET, compute_yield, yield_lines

TOKENS = [
    {"provider": "greenhouse", "board_token": "old", "discovered_from": None},
    {"provider": "greenhouse", "board_token": "new", "discovered_from": "brave_slug_discovery"},
    {"provider": "lever", "board_token": "fd", "discovered_from": "form_d"},
    {"provider": "ashby", "board_token": "wf", "discovered_from": "wellfound_signal"},
    {"provider": "greenhouse", "board_token": "nw", "company": "Northwind Labs", "discovered_from": "brave_slug_discovery"},
]


def test_careers_page_url_falls_back_to_the_boards_company_name():
    lead = {"source": "cloud_decoded_job_signal", "status": "company_qualified", "company": "Northwind Labs",
            "job_posting_url": "https://northwindlabs.io/careers/1"}
    assert compute_yield([lead], TOKENS)["by_source"] == {"slug_discovery": {"qualified": 1, "named_buyer": 0,
                                                                             "verified_email": 0}}


def L(url=None, source="cloud_decoded_job_signal", status="company_qualified", named=False, grade=None, via=None):
    return {"source": source, "status": status, "job_posting_url": url,
            "first_name": "Jane" if named else None, "title": "CTO" if named else None,
            "email_grade": grade, "buyer_suggestion": {"source": via} if via else None}


def test_yield_attributes_ats_leads_to_the_slug_origin():
    leads = [
        L("https://boards.greenhouse.io/old/jobs/1"),
        L("https://job-boards.greenhouse.io/new/jobs/2", named=True, grade="valid", via="form_d"),
        L("https://jobs.lever.co/fd/abc", named=True, grade="risky"),
        L("https://jobs.ashbyhq.com/wf/x"),
        L("https://news.ycombinator.com/item?id=1", source="hn_thread"),
        L("https://boards.greenhouse.io/new/jobs/3", status="rejected_wrong_icp"),
    ]
    y = compute_yield(leads, TOKENS)
    assert y["total"] == {"qualified": 5, "named_buyer": 2, "verified_email": 1}
    assert y["by_source"]["slug_discovery"] == {"qualified": 1, "named_buyer": 1, "verified_email": 1}
    assert y["by_source"]["form_d"]["named_buyer"] == 1 and y["by_source"]["wellfound"]["qualified"] == 1
    assert y["by_source"]["hn"]["qualified"] == 1 and y["by_source"]["ats_cache"]["qualified"] == 1
    assert y["named_by_resolver_source"] == {"form_d": 1}
    assert y["target"] == WEEKLY_TARGET == {"qualified": 40, "named_buyer": 15, "verified_email": 10}
    assert y["on_target"] == {"qualified": False, "named_buyer": False, "verified_email": False}
    lines = yield_lines({**y, "window_days": 7})
    assert lines[0].startswith("Lead sources, last 7 days (target 40 companies / 15 named buyers / 10 verified")
    assert any("slug_discovery" in ln and "1 / 1 / 1" in ln for ln in lines)


def test_loop_map_treats_new_jobs_as_side_lines_not_the_scout():
    from core.loop_map_rules import outbound_lead_run
    pid = "p"
    now = datetime(2026, 11, 15, 14, tzinfo=timezone.utc)
    S = {"runs": [
        {"product_id": pid, "status": "complete", "sources_used": ["cloud_decoded_job_signal"],
         "started_at": "2026-11-15T13:00:00+00:00", "completed_at": "2026-11-15T13:10:00+00:00",
         "leads_found": 12, "funnel_stats": {"postings_returned": 100}},
        {"product_id": pid, "status": "complete", "sources_used": ["ats_slug_discovery"],
         "started_at": "2026-11-15T13:30:00+00:00", "completed_at": "2026-11-15T13:31:00+00:00",
         "leads_found": 0, "funnel_stats": {"slugs_written": 314, "queries": 36}},
    ], "source_yield": {"total": {"qualified": 12, "named_buyer": 3, "verified_email": 1},
                        "target": WEEKLY_TARGET, "by_source": {"slug_discovery": {"qualified": 7, "named_buyer": 2,
                                                                                  "verified_email": 1}}}}
    st = outbound_lead_run(S, pid, now)
    assert st.status == "working" and st.count == 12, "the 0-lead discovery run is not the scout"
    assert "314 new ATS slugs" in st.evidence
    assert "This week: 12 companies / 3 named buyers / 1 verified emails (target 40/15/10)" in st.evidence
