"""Loop Map desk statuses: real output is green, anything unreadable is unknown."""

from datetime import datetime, timedelta, timezone

from core.loop_map_rules import RULES, SEVERITY, STATUSES, evaluate
from core.outreach_counters import count_outreach

NOW = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)
P = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


def base(**over) -> dict:
    S = {
        "products": [{"id": P, "slug": "consulting", "name": "C", "verified_property_url": None,
                      "paying_customer_count": 0}],
        "leads": [], "seqs": [], "convs": [], "runs": [], "icp": [], "positioning": [], "surfaces": [],
        "seo": [], "social": [], "email_seqs": [], "indexation": [], "build_briefs": [], "workspaces": [],
        "demo_sessions": [], "linkedin": [], "internal_workspace_ids": [],
        "sending_domain": "outreach.example.com",
        "dmarc": {"found": True, "checked": "_dmarc.example.com", "nameserver": "ns1"},
        "resend_domains": {"outreach.example.com": "verified", "theclouddecoded.com": "verified"},
        "brave_budget": {"allowed": True, "reason": "ok"},
        "sends_paused": {"paused": False, "reason": ""},
        "stripe_products": {}, "d6": {"_error": "no creds"}, "sph_accounts": {"_error": "no creds"},
    }
    S.update(over)
    return S


def run(rule, S, pid=P):
    return evaluate(rule, S, pid, NOW)


def test_every_rule_returns_a_known_status_on_empty_data():
    for key in RULES:
        assert run(key, base()).status in STATUSES, key


def test_unknown_rule_key_is_unknown_not_green():
    assert run("no.such.rule", base()).status == "unknown"


def test_unreadable_source_is_unknown_with_the_error_as_evidence():
    st = run("outbound.find_buyer", base(leads={"_error": "ConnectionTerminated"}))
    assert st.status == "unknown"
    assert "ConnectionTerminated" in st.evidence


def test_evaluator_bug_is_unknown_not_a_crash():
    st = run("outbound.lead_run", base(runs=[{"product_id": P, "status": "complete", "completed_at": "garbage"}]))
    assert st.status == "unknown"


def test_lead_run_working_only_with_real_leads_in_window():
    ok = base(runs=[{"product_id": P, "status": "complete", "started_at": ago(days=6),
                     "completed_at": ago(days=6), "leads_found": 6,
                     "funnel_stats": {"postings_returned": 9043}}])
    st = run("outbound.lead_run", ok)
    assert st.status == "working" and st.count == 6 and "9,043 postings" in st.evidence
    zero = base(runs=[{"product_id": P, "status": "complete", "started_at": ago(days=1),
                       "completed_at": ago(days=1), "leads_found": 0}])
    assert run("outbound.lead_run", zero).status == "not_proven"


def test_lead_run_stale_or_failed_is_blocked():
    stale = base(runs=[{"product_id": P, "status": "complete", "started_at": ago(days=13),
                        "completed_at": ago(days=13), "leads_found": 4}])
    assert run("outbound.lead_run", stale).status == "blocked"
    failed = base(runs=[{"product_id": P, "status": "failed", "started_at": ago(days=1), "error_message": "boom"}])
    st = run("outbound.lead_run", failed)
    assert st.status == "blocked" and "boom" in st.evidence


def test_brave_budget_refusal_blocks_the_lead_run():
    S = base(brave_budget={"allowed": False, "reason": "at 720/900"},
             runs=[{"product_id": P, "status": "complete", "started_at": ago(days=1),
                    "completed_at": ago(days=1), "leads_found": 3}])
    st = run("outbound.lead_run", S)
    assert st.status == "blocked" and "720/900" in st.evidence


def test_waiting_counts_match_the_digest_counter_function():
    leads = [{"product_id": P, "contact_status": "pending"}, {"product_id": P, "contact_status": "needs_review"},
             {"product_id": OTHER, "contact_status": "pending"}]
    seqs = [{"product_id": P, "status": "pending_hitl"}, {"product_id": OTHER, "status": "pending_hitl"}]
    S = base(leads=leads, seqs=seqs)
    buyer, approve = run("outbound.find_buyer", S), run("outbound.approve", S)
    c = count_outreach(leads, seqs, [], product_id=P, now=NOW)
    assert (buyer.status, buyer.count) == ("waiting_on_you", c["awaiting_buyer"]) == ("waiting_on_you", 2)
    assert (approve.status, approve.count) == ("waiting_on_you", c["awaiting_approval"]) == ("waiting_on_you", 1)
    # And the global (digest) number is the sum across products.
    assert count_outreach(leads, seqs, [], now=NOW)["awaiting_buyer"] == 3


def test_approved_then_rejected_does_not_count_as_approved():
    S = base(seqs=[{"product_id": P, "status": "rejected_hitl", "hitl_approved_at": ago(days=3)}])
    st = run("outbound.approve", S)
    assert st.status == "not_proven" and "no draft has ever been approved" in st.evidence


def test_send_blocked_without_dmarc_or_verified_domain():
    assert run("outbound.send", base(dmarc={"found": False, "checked": "_dmarc.example.com",
                                             "nameserver": "ns1"})).status == "blocked"
    assert run("outbound.send", base(resend_domains={"outreach.example.com": "failed"})).status == "blocked"
    sent = base(seqs=[{"product_id": P, "status": "touch_1_sent", "touch_1_sent_at": ago(hours=1)}])
    assert run("outbound.send", sent).status == "working"


def test_draft_enrollment_blocked_when_eligible_leads_sit_undrafted():
    S = base(leads=[{"product_id": P, "status": "pending_dm", "created_at": ago(days=3)}],
             seqs=[{"product_id": P, "status": "pending_hitl", "created_at": ago(days=5)}])
    assert run("outbound.draft_enrollment", S).status == "blocked"


def test_cloud_decoded_internal_workspaces_are_not_customers():
    S = base(workspaces=[{"id": "qa", "is_demo_workspace": False, "stripe_subscription_status": "active",
                          "aws_role_verified_at": ago(days=30)}], internal_workspace_ids=["qa"])
    assert run("cd.cloud_connected", S).status == "not_proven"
    assert run("cd.signup_pay", S).status == "not_proven"
    assert run("cd.signup_pay", base(resend_domains={"theclouddecoded.com": "failed"})).status == "blocked"


def test_superseded_pending_positioning_is_not_waiting():
    S = base(positioning=[{"product_id": P, "version": 2, "status": "pending_review"},
                          {"product_id": P, "version": 4, "status": "approved", "approved_at": ago(days=40)}])
    assert run("product.positioning_approved", S).status == "working"
    S["positioning"].append({"product_id": P, "version": 5, "status": "pending_review"})
    assert run("product.positioning_approved", S).status == "waiting_on_you"


def test_other_products_database_never_leaks_into_a_new_product():
    d6 = {"slug": "decodedsix", "published_7d": 4, "published_latest": ago(hours=2)}
    assert run("d6.published", base(d6=d6)).status == "unknown"  # P's slug is 'consulting'
    S = base(d6=d6, products=[{"id": P, "slug": "decodedsix"}])
    assert run("d6.published", S).status == "working"


def test_missing_credentials_are_unknown():
    st = run("product.signups", base(products=[{"id": P, "slug": "small-portfolio-hub"}]))
    assert st.status == "unknown" and "no creds" in st.evidence


def test_search_console_property_without_rows_is_blocked():
    S = base(products=[{"id": P, "slug": "x", "verified_property_url": "sc-domain:x.com"}])
    assert run("product.search_console", S).status == "blocked"


def test_severity_covers_every_status():
    assert set(SEVERITY) == set(STATUSES)
    assert max(SEVERITY, key=SEVERITY.get) == "blocked"
