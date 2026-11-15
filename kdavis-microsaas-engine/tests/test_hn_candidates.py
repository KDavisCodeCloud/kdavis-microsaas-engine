"""Lead-source expansion, Phase 2 (2026-11-15): HN post fields and the poster
as a candidate buyer. No network."""

from datetime import datetime, timezone

from agents.marketing.hn_scout import (
    WHO_IS_HIRING,
    HnPost,
    candidate_buyer,
    find_hn_leads,
    keep_post,
    post_fields,
)


def post(text, by="jdoe"):
    return HnPost(item_id=1, thread_kind=WHO_IS_HIRING, thread_title="Ask HN: Who is hiring? (November 2026)",
                  by=by, posted_at=datetime(2026, 11, 2, tzinfo=timezone.utc), text=text)


def test_header_is_split_into_roles_location_remote():
    p = post("Acme Robotics | Senior DevOps Engineer, Platform Engineer | Austin, TX | REMOTE (US) | https://acme.io\n"
             "We run Terraform on AWS.")
    f = post_fields(p)
    assert f.roles == "Senior DevOps Engineer, Platform Engineer"
    assert "Austin, TX" in f.location
    assert f.remote is True
    assert "https://acme.io" in f.urls


def test_onsite_is_recorded_as_not_remote_and_unknown_stays_none():
    assert post_fields(post("Acme | SRE | Onsite in Berlin")).remote is False
    assert post_fields(post("Acme | SRE | Berlin")).remote is None


def test_emails_including_obfuscated_ones():
    f = post_fields(post("Acme | SRE\nEmail jane [at] acme [dot] io or hiring@acme.io. Not you@example.com"))
    assert f.emails == ["hiring@acme.io", "jane@acme.io"]


def test_self_identified_cto_is_a_candidate_with_company_email():
    p = post("Acme | Platform Engineer | Remote\nI'm the CTO -- email me at jane@acme.io, not jobs@gmail.com")
    c = candidate_buyer(p, "acme.io", post_fields(p))
    assert c["title"].lower() == "cto" and c["email"] == "jane@acme.io"
    assert c["source"] == "hn_poster" and c["email_grade"] is None, "never pre-verified"
    assert c["name"] is None and c["confidence"] == "low", "no name is invented"
    assert c["evidence_url"].startswith("https://news.ycombinator.com/item?id=")


def test_named_signoff_raises_confidence():
    p = post("Acme | DevOps Engineer\nWe are hiring. Jane Doe, Co-Founder & CTO")
    c = candidate_buyer(p, "acme.io", post_fields(p))
    assert c is not None and c["name"] == "Jane Doe" and c["confidence"] == "medium"


def test_recruiter_poster_is_not_a_candidate():
    p = post("Acme | DevOps Engineer\nI'm the recruiter for this role, reach me at r@acme.io")
    assert candidate_buyer(p, "acme.io", post_fields(p)) is None


def test_no_self_identification_means_no_candidate():
    p = post("Acme | DevOps Engineer | Remote\nApply at acme.io/careers")
    assert candidate_buyer(p, "acme.io", post_fields(p)) is None


def test_infra_role_terms_cover_infrastructure_and_cloud_roles_not_the_bare_words():
    assert keep_post(post("Acme | Cloud Engineer | Remote"))[0]
    assert keep_post(post("Acme | Infrastructure Engineer | NYC"))[0]
    assert not keep_post(post("Acme | Frontend Engineer | Our platform runs in the cloud"))[0]


def test_found_leads_carry_candidates_and_fields():
    thread = {"id": 100, "title": "Ask HN: Who is hiring? (November 2026)", "kids": [1, 2],
              "time": int(datetime.now(timezone.utc).timestamp())}
    items = {
        "user/whoishiring": {"submitted": [100]}, "user/jon_north": {"submitted": []},
        "item/100": thread,
        "item/1": {"id": 1, "by": "jdoe", "time": thread["time"],
                   "text": "Northwind Labs (northwindlabs.io) | Senior SRE | Remote<p>I&#x27;m the CTO. "
                           "Write to jane@northwindlabs.io"},
        "item/2": {"id": 2, "by": "x", "time": thread["time"], "text": "Beta | iOS Engineer | NYC"},
    }

    class Resp:
        def __init__(self, p):
            self.p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self.p

    def get(url, **_k):
        key = url.split("/v0/")[1].removesuffix(".json")
        return Resp(items.get(key))

    rows, stats = find_hn_leads("p", http_get=get)
    assert len(rows) == 1 and stats.candidate_buyers == 1 and stats.posts_with_email == 1
    r = rows[0]
    assert r["buyer_candidates"][0]["email"] == "jane@northwindlabs.io"
    assert r["hn_post_fields"]["remote"] is True and r["location"] == "Remote"
    assert r["job_posting_title"] == "Senior SRE"
    assert r["status"] == "company_qualified" and r["contact_status"] == "pending", \
        "a candidate is a suggestion: the lead stays in Find the Buyer"


def test_role_mailbox_is_not_a_candidate_email():
    p = post("Acme | SRE\nI'm the founder. jobs@acme.io")
    c = candidate_buyer(p, "acme.io", post_fields(p))
    assert c["email"] is None


def test_url_segment_is_not_a_role():
    f = post_fields(post("Simple | DevOps Engineer | https://simple.com/jobs/devops | REMOTE"))
    assert f.roles == "DevOps Engineer"


def test_ats_and_job_board_hosts_are_not_company_domains():
    from agents.marketing.hn_scout import domain_from_post
    p = post("ProphetX | Multiple Roles | REMOTE | https://prophetexchange.pinpointhq.com/jobs")
    assert domain_from_post(p, "ProphetX") is None
    p = post("iCallidus | Azure Intune Engineer | https://devitjobs.com/jobs/x")
    assert domain_from_post(p, "iCallidus") is None
