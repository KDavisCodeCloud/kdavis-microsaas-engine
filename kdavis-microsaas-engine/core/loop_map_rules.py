# REFACTOR CANDIDATE -- one evaluator per rule key; split by playbook if it grows.
"""
Loop Map desk status -- pure evaluators, one per mse_loop_stations.status_rule_key.

Every evaluator takes the preloaded sources dict (core/loop_map_sources.py),
a product id and `now`, and returns a Status. No I/O happens here, so each
rule is unit-testable against hand-built rows.

The five statuses mean exactly this, and nothing softer:

  working         produced REAL output inside its expected window -- a row
                  written, a send delivered, a page published. A 200 from a
                  job that wrote nothing is not "working".
  not_proven      the code path exists but has no successful real output yet
                  (or not recently enough to count, without evidence of failure)
  waiting_on_you  items are queued for a human step; `count` says how many
  blocked         the last run failed, a dependency check fails, or the output
                  has been absent for more than twice its expected window
  not_built       no code path exists
  unknown         we could not read the source. NEVER rendered green.

Desk color comes from here. Kelvin's check marks live in mse_loop_progress and
are never read or written by this module.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from core.outreach_counters import count_outreach

STATUSES = ("working", "not_proven", "waiting_on_you", "blocked", "not_built", "unknown")

# Ordering for "the day's worst status" in the 4-week history strip.
SEVERITY = {"working": 0, "not_built": 1, "not_proven": 2, "waiting_on_you": 3,
            "unknown": 4, "blocked": 5}

RUN_DONE = ("complete", "completed")  # mkt_lead_finder writes 'complete'
QUALIFIED_LEAD_STATUSES = ("company_qualified", "pending_dm", "pending_email", "contacted", "converted")
APPROVED_SEQ_STATUSES = ("approved_hitl", "approved_manual", "touch_1_sending", "touch_1_sent",
                         "touch_2_sending", "sequence_complete", "replied")


@dataclass
class Status:
    status: str
    evidence: str
    count: Optional[int] = None
    evidence_at: Optional[str] = None
    evidence_link: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


class SourceUnavailable(Exception):
    """A source this rule needs could not be read; the rule reports unknown."""


def _src(S: dict, name: str):
    value = S.get(name)
    if isinstance(value, dict) and value.get("_error"):
        raise SourceUnavailable(f"{name}: {value['_error']}")
    if value is None:
        raise SourceUnavailable(f"{name}: not loaded")
    return value


def _rows(S: dict, name: str, product_id: Optional[str] = None) -> list[dict]:
    rows = _src(S, name)
    if product_id is None:
        return list(rows)
    return [r for r in rows if r.get("product_id") == product_id]


def _latest(rows: list[dict], key: str) -> Optional[str]:
    vals = [str(r[key]) for r in rows if r.get(key)]
    return max(vals) if vals else None


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _day(ts: Optional[str]) -> str:
    return (ts or "")[:10] or "never"


# --------------------------------------------------------------------------
# Outbound (signal_outbound playbook)
# --------------------------------------------------------------------------

LEAD_RUN_WINDOW_DAYS = 8  # weekly Sunday run, plus a day of slack


# Runs that are not the desk's own sweep. The Hacker News scout records its
# runs under the consulting product (2026-10-18) but is monthly and separate;
# letting it stand in for the weekly ATS run would hide a missed Sunday. The
# weekly pain-phrase rescan (2026-10-19) finds no companies at all. Both are
# shown as a line on their desk (HN on Find, the rescan on Qualify) and turn
# it blocked if their own latest run failed.
SIDE_SOURCES = ("hn_thread", "pain_rescan")
# Sources scheduled monthly (agents/marketing/lead_schedule.py). A desk fed
# only by these is judged against a month, not a week.
MONTHLY_SOURCES = ("partner_signal", "vc_operating_partner", "hn_thread")
MONTHLY_WINDOW_DAYS = 35
SIDE_LABELS = {"hn_thread": "Hacker News scout", "pain_rescan": "Pain-phrase rescan"}


def _desk_runs(S, pid) -> list[dict]:
    return [r for r in _rows(S, "runs", pid)
            if not set(r.get("sources_used") or []) & set(SIDE_SOURCES)]


def _window_days(runs: list[dict]) -> int:
    srcs = {s for r in runs for s in (r.get("sources_used") or [])}
    return MONTHLY_WINDOW_DAYS if srcs and srcs <= set(MONTHLY_SOURCES) else LEAD_RUN_WINDOW_DAYS


def _side_run(S, pid, source: str) -> tuple[Optional[str], bool]:
    """(one-line summary of the latest `source` run, did it fail)."""
    runs = sorted((r for r in _rows(S, "runs", pid) if source in (r.get("sources_used") or [])),
                  key=lambda r: str(r.get("started_at") or ""), reverse=True)
    if not runs:
        return None, False
    r, label = runs[0], SIDE_LABELS[source]
    if r.get("status") in ("failed", "error"):
        return f"{label} {_day(r.get('started_at'))} FAILED: {r.get('error_message') or 'no error text'}", True
    if r.get("status") not in RUN_DONE:
        return f"{label} {_day(r.get('started_at'))}: {r.get('status')}.", False
    f = r.get("funnel_stats") or {}
    if source == "pain_rescan":
        detail = f"{f.get('scanned', 0)} leads re-read, {f.get('newly_matched_leads', 0)} gained a pain phrase"
    else:
        detail = f"{r.get('leads_found') or 0} new leads"
    return f"{label} {_day(r.get('completed_at'))}: {detail}.", False


def _with_side(st: Status, S, pid, source: str) -> Status:
    note, failed = _side_run(S, pid, source)
    if not note:
        return st
    st.evidence = f"{st.evidence} {note}"
    if failed and st.status in ("working", "not_proven"):
        st.status = "blocked"
    return st


def outbound_lead_run(S, pid, now) -> Status:
    return _with_side(_lead_run(S, pid, now), S, pid, "hn_thread")


def _lead_run(S, pid, now) -> Status:
    runs = sorted(_desk_runs(S, pid), key=lambda r: str(r.get("started_at") or ""), reverse=True)
    if not runs:
        return Status("not_proven", "No lead-finder run has ever been recorded for this product.")
    last = runs[0]
    st = last.get("status")
    if st in ("failed", "error"):
        return Status("blocked", f"Last run {_day(last.get('started_at'))} failed: "
                                 f"{last.get('error_message') or 'no error text recorded'}",
                      evidence_at=last.get("started_at"))
    budget = S.get("brave_budget") or {}
    if isinstance(budget, dict) and budget.get("allowed") is False:
        return Status("blocked", f"Brave budget guard is refusing runs: {budget.get('reason')}")
    done = [r for r in runs if r.get("status") in RUN_DONE and r.get("completed_at")]
    if st == "running" and not done:
        return Status("not_proven", f"First run in progress since {last.get('started_at')}.",
                      evidence_at=last.get("started_at"))
    if not done:
        return Status("not_proven", f"No completed run; latest status '{st}'.", evidence_at=last.get("started_at"))
    latest = done[0]
    age = now - datetime.fromisoformat(str(latest["completed_at"]).replace("Z", "+00:00"))
    found = latest.get("leads_found") or 0
    postings = (latest.get("funnel_stats") or {}).get("postings_returned")
    read = f"{postings:,} postings read, " if isinstance(postings, int) else ""
    window = _window_days(runs)
    if age > timedelta(days=window):
        return Status("blocked", f"No completed run in {age.days} days "
                                 f"(expected {'monthly' if window > LEAD_RUN_WINDOW_DAYS else 'weekly'}). "
                                 f"Last completed {_day(latest['completed_at'])}: {read}{found} leads.",
                      count=found, evidence_at=latest["completed_at"])
    if found <= 0:
        return Status("not_proven", f"Last run {_day(latest['completed_at'])} completed but found 0 leads.",
                      count=0, evidence_at=latest["completed_at"])
    return Status("working", f"Run completed {_day(latest['completed_at'])}: {read}{found} leads found.",
                  count=found, evidence_at=latest["completed_at"])


def outbound_qualify(S, pid, now) -> Status:
    return _with_side(_qualify(S, pid, now), S, pid, "pain_rescan")


def _qualify(S, pid, now) -> Status:
    runs = [r for r in _desk_runs(S, pid) if r.get("status") in RUN_DONE and r.get("completed_at")]
    runs.sort(key=lambda r: str(r["completed_at"]), reverse=True)
    if not runs:
        return Status("not_proven", "No completed run to qualify companies from.")
    latest = runs[0]
    stats = latest.get("funnel_stats") or {}
    qualified = stats.get("leads_qualified")
    if qualified is None:
        since = str(latest.get("started_at"))
        qualified = sum(1 for l in _rows(S, "leads", pid)
                        if str(l.get("created_at") or "") >= since and l.get("status") in QUALIFIED_LEAD_STATUSES)
    age = now - datetime.fromisoformat(str(latest["completed_at"]).replace("Z", "+00:00"))
    if age > timedelta(days=_window_days(runs)):
        return Status("blocked", f"Nothing qualified since the run of {_day(latest['completed_at'])} "
                                 f"({qualified} then); no run in {age.days} days.",
                      count=qualified, evidence_at=latest["completed_at"])
    if not qualified:
        return Status("not_proven", f"Run of {_day(latest['completed_at'])} qualified 0 companies.",
                      count=0, evidence_at=latest["completed_at"])
    return Status("working", f"Run of {_day(latest['completed_at'])} qualified {qualified} companies.",
                  count=qualified, evidence_at=latest["completed_at"])


def _counters(S, pid, now) -> dict:
    return count_outreach(_rows(S, "leads"), _rows(S, "seqs"), _rows(S, "convs"), product_id=pid, now=now)


def outbound_find_buyer(S, pid, now) -> Status:
    c = _counters(S, pid, now)
    if c["awaiting_buyer"]:
        return Status("waiting_on_you", f"{c['awaiting_buyer']} qualified companies have no named buyer.",
                      count=c["awaiting_buyer"])
    found = [l for l in _rows(S, "leads", pid) if l.get("contact_status") == "found"]
    if found:
        return Status("working", f"No company waiting for a buyer; {len(found)} buyers named so far.",
                      count=0, evidence_at=_latest(found, "created_at"))
    return Status("not_proven", "No company has had a buyer named yet.", count=0)


DRAFT_WINDOW_HOURS = 48


ENROLL_CHECK_UTC = (15, 45)  # n8n fires the 08:00 MST run at 15:00 UTC


def _enrollment_line(S, pid, now) -> tuple[Optional[str], bool]:
    """(the latest 08:00 enrollment run and this product's skips in it,
    is it broken). Broken = the latest run failed for this product, or no
    run is recorded today after the 08:45 MST check time."""
    runs = S.get("dm_runs")
    if not isinstance(runs, list):
        return None, False
    if not runs:
        return "No 08:00 enrollment run recorded in 7 days.", True
    last = runs[0]
    day = _day(last.get("created_at"))
    errors = (last.get("metadata") or {}).get("errors") or {}
    mine = errors.get(pid) or errors.get("_run")
    if mine:
        return f"08:00 enrollment {day} FAILED: {mine}", True
    if day < now.date().isoformat() and (now.hour, now.minute) >= ENROLL_CHECK_UTC:
        return f"No 08:00 enrollment run today; last {day}.", True
    skips = [k for k in (S.get("dm_skips") if isinstance(S.get("dm_skips"), list) else [])
             if k.get("product_id") == pid and str(k.get("run_at") or "")[:19] ==
             str((last.get("metadata") or {}).get("run_at") or "")[:19]]
    if not skips:
        return f"08:00 enrollment {day}: no lead skipped.", False
    by: dict[str, int] = {}
    for k in skips:
        by[k["reason"]] = by.get(k["reason"], 0) + 1
    reasons = ", ".join(f"{n} {r.replace('_', ' ')}" for r, n in sorted(by.items(), key=lambda x: -x[1]))
    return f"08:00 enrollment {day} skipped {len(skips)}: {reasons}.", False


def outbound_draft_enrollment(S, pid, now) -> Status:
    st = _draft_enrollment(S, pid, now)
    line, broken = _enrollment_line(S, pid, now)
    if line:
        st.evidence = f"{st.evidence} {line}"
        if broken and st.status != "waiting_on_you":
            st.status = "blocked"
    return st


def _draft_enrollment(S, pid, now) -> Status:
    seqs = _rows(S, "seqs", pid)
    recent = [s for s in seqs if str(s.get("created_at") or "") >= _iso(now - timedelta(hours=DRAFT_WINDOW_HOURS))]
    eligible = [l for l in _rows(S, "leads", pid) if l.get("status") == "pending_dm"]
    last = _latest(seqs, "created_at")
    if recent:
        return Status("working", f"{len(recent)} draft(s) written in the last {DRAFT_WINDOW_HOURS}h.",
                      count=len(recent), evidence_at=_latest(recent, "created_at"))
    if eligible:
        oldest = min(str(l.get("created_at") or "") for l in eligible)
        return Status("blocked", f"{len(eligible)} lead(s) ready to draft since {_day(oldest)} "
                                 f"but nothing drafted in {DRAFT_WINDOW_HOURS}h.", count=len(eligible))
    if last:
        return Status("working", f"Idle: no lead waiting to be drafted. Last draft {_day(last)}.",
                      count=0, evidence_at=last)
    return Status("not_proven", "No draft has ever been written for this product.", count=0)


def outbound_approve(S, pid, now) -> Status:
    c = _counters(S, pid, now)
    # By current status, not hitl_approved_at: an approval later rejected or
    # expired never reached a prospect, and the funnel counts it the same way.
    approved = [s for s in _rows(S, "seqs", pid) if s.get("status") in APPROVED_SEQ_STATUSES]
    if c["awaiting_approval"]:
        return Status("waiting_on_you", f"{c['awaiting_approval']} draft(s) waiting for your approval; "
                                        f"{len(approved)} approved ever.", count=c["awaiting_approval"])
    if approved:
        return Status("working", f"Queue clear. {len(approved)} draft(s) approved so far.",
                      count=0, evidence_at=_latest(approved, "hitl_approved_at"))
    return Status("not_proven", "Nothing waiting, and no draft has ever been approved.", count=0)


def _send_blockers(S) -> list[str]:
    out = []
    dmarc = _src(S, "dmarc")
    if not dmarc.get("found"):
        out.append(f"No DMARC record at {dmarc.get('checked')} (authoritative {dmarc.get('nameserver')}).")
    resend = _src(S, "resend_domains")
    sender = S.get("sending_domain")
    st = resend.get(sender)
    if st != "verified":
        out.append(f"Resend sending domain {sender} is '{st or 'missing'}', not verified.")
    paused = S.get("sends_paused") or {}
    if paused.get("paused"):
        out.append(f"Sends paused: {paused.get('reason')}")
    return out


def outbound_send(S, pid, now) -> Status:
    seqs = _rows(S, "seqs", pid)
    sent_at = [str(s[k]) for s in seqs for k in ("touch_1_sent_at", "touch_2_sent_at", "touch_3_sent_at") if s.get(k)]
    blockers = _send_blockers(S)
    if blockers:
        return Status("blocked", " ".join(blockers) + f" Real sends ever: {len(sent_at)}.",
                      count=len(sent_at), evidence_link="https://resend.com/domains")
    today = now.date().isoformat()
    today_n = sum(1 for t in sent_at if t.startswith(today))
    if today_n:
        return Status("working", f"{today_n} touch(es) sent today.", count=today_n, evidence_at=max(sent_at))
    week = [t for t in sent_at if t >= _iso(now - timedelta(days=7))]
    if week:
        return Status("working", f"{len(week)} touch(es) sent in 7 days; none yet today.",
                      count=len(week), evidence_at=max(week))
    if sent_at:
        return Status("not_proven", f"Last real send {_day(max(sent_at))}.", count=0, evidence_at=max(sent_at))
    return Status("not_proven", "Sender runs, but 0 real sends have ever gone out for this product.", count=0)


def _conv_stage(S, pid, col: str, label: str, now) -> Status:
    convs = _rows(S, "convs", pid)
    hits = [c for c in convs if c.get(col)]
    sent = sum(1 for s in _rows(S, "seqs", pid) if s.get("touch_1_sent_at"))
    if hits:
        return Status("working", f"{len(hits)} {label} marked; latest {_day(_latest(hits, col))}.",
                      count=len(hits), evidence_at=_latest(hits, col))
    if not sent:
        return Status("not_proven", f"0 {label}: nothing has been sent yet.", count=0)
    return Status("not_proven", f"0 {label} marked from {sent} sent.", count=0)


def outbound_replies(S, pid, now) -> Status:
    return _conv_stage(S, pid, "replied_at", "replies", now)


def outbound_calls_booked(S, pid, now) -> Status:
    return _conv_stage(S, pid, "call_booked_at", "calls booked", now)


def outbound_proposals(S, pid, now) -> Status:
    return _conv_stage(S, pid, "proposal_sent_at", "proposals sent", now)


def partner_sent(S, pid, now) -> Status:
    """Partner notes go out by hand on LinkedIn, so the email blockers (DMARC,
    Resend) are irrelevant here. Sent means a human marked touch 1 sent."""
    seqs = _rows(S, "seqs", pid)
    sent = [str(s["touch_1_sent_at"]) for s in seqs if s.get("touch_1_sent_at")]
    ready = sum(1 for s in seqs if s.get("status") == "approved_manual" and not s.get("touch_1_sent_at"))
    if ready:
        return Status("waiting_on_you", f"{ready} approved partner note(s) waiting to be pasted on LinkedIn; "
                                        f"{len(sent)} sent ever.", count=ready)
    if sent:
        return Status("working", f"{len(sent)} partner note(s) sent; latest {_day(max(sent))}.",
                      count=len(sent), evidence_at=max(sent))
    return Status("not_proven", "No partner note has been sent yet.", count=0)


def partner_agreed(S, pid, now) -> Status:
    return _conv_stage(S, pid, "won_at", "referral agreements", now)


def outbound_icp_configured(S, pid, now) -> Status:
    icp = _rows(S, "icp", pid)
    if not icp:
        return Status("not_built", "No ICP config / target list for this product.")
    if not any(r.get("outbound_enabled") for r in icp):
        return Status("not_built", "ICP config exists, but outbound is disabled and the lead finder "
                                   "has no source for this product's buyers.")
    return outbound_lead_run(S, pid, now)


# --------------------------------------------------------------------------
# Cloud Decoded
# --------------------------------------------------------------------------

def _real_workspaces(S) -> list[dict]:
    internal = set(S.get("internal_workspace_ids") or [])
    return [w for w in _rows(S, "workspaces") if not w.get("is_demo_workspace") and w.get("id") not in internal]


def cd_demo_sessions(S, pid, now) -> Status:
    rows = _rows(S, "demo_sessions")
    week = [r for r in rows if str(r.get("created_at") or "") >= _iso(now - timedelta(days=7))]
    if week:
        return Status("working", f"{len(week)} demo session(s) in 7 days.", count=len(week),
                      evidence_at=_latest(week, "created_at"), evidence_link="https://theclouddecoded.com/demo")
    if rows:
        return Status("not_proven", f"No demo session in 7 days; last {_day(_latest(rows, 'created_at'))}.",
                      count=0, evidence_at=_latest(rows, "created_at"))
    return Status("not_proven", "Demo is built, but demo_sessions has 0 rows ever.", count=0,
                  evidence_link="https://theclouddecoded.com/demo")


def cd_signup_pay(S, pid, now) -> Status:
    resend = _src(S, "resend_domains")
    st = resend.get("theclouddecoded.com")
    paying = [w for w in _real_workspaces(S) if w.get("stripe_subscription_status") in ("active", "trialing")]
    if st != "verified":
        return Status("blocked", f"Welcome email cannot send: Resend domain theclouddecoded.com is "
                                 f"'{st or 'missing'}'. Real paying workspaces: {len(paying)}.",
                      count=len(paying), evidence_link="https://resend.com/domains")
    if paying:
        return Status("working", f"{len(paying)} real customer workspace(s) active or trialing.",
                      count=len(paying), evidence_at=_latest(paying, "created_at"))
    return Status("not_proven", "Checkout is live; 0 real customer workspaces (internal and demo excluded).", count=0)


def cd_cloud_connected(S, pid, now) -> Status:
    real = _real_workspaces(S)
    cols = ("aws_role_verified_at", "azure_verified_at", "github_pat_verified_at", "k8s_verified_at")
    hits = [w for w in real if any(w.get(c) for c in cols)]
    if hits:
        return Status("working", f"{len(hits)} real customer(s) connected a cloud.", count=len(hits))
    if not real:
        return Status("not_proven", "No real customer yet; only internal QA has connected a cloud.", count=0)
    return Status("waiting_on_you", f"{len(real)} customer(s) signed up without connecting a cloud.", count=len(real))


# --------------------------------------------------------------------------
# Product / community + search playbook
# --------------------------------------------------------------------------

def product_positioning(S, pid, now) -> Status:
    rows = sorted(_rows(S, "positioning", pid), key=lambda r: r.get("version") or 0, reverse=True)
    if not rows:
        return Status("not_built", "No positioning brief exists for this product.")
    approved = [r for r in rows if r.get("status") == "approved"]
    # A pending version older than the approved one is superseded, not waiting.
    floor = approved[0].get("version") or 0 if approved else 0
    pending = [r for r in rows if r.get("status") in ("pending_review", "draft") and (r.get("version") or 0) > floor]
    if pending:
        return Status("waiting_on_you", f"Positioning v{pending[0].get('version')} is waiting for your review"
                      + (f" (v{approved[0].get('version')} approved {_day(approved[0].get('approved_at'))})." if approved else "."),
                      count=len(pending))
    if approved:
        return Status("working", f"Positioning v{approved[0].get('version')} approved "
                                 f"{_day(approved[0].get('approved_at'))}.", evidence_at=approved[0].get("approved_at"))
    return Status("not_proven", f"Latest positioning v{rows[0].get('version')} is '{rows[0].get('status')}'.")


def product_search_pages(S, pid, now) -> Status:
    surfaces = _rows(S, "surfaces", pid)
    seo = _rows(S, "seo", pid)
    live = [r for r in surfaces if r.get("status") == "published" or r.get("published_at")]
    live += [r for r in seo if r.get("status") == "published"]
    pending = [r for r in surfaces + seo if r.get("status") in ("pending_review", "pending_hitl", "draft")]
    if pending:
        return Status("waiting_on_you", f"{len(pending)} search page(s) waiting for review; {len(live)} live.",
                      count=len(pending))
    if live:
        return Status("working", f"{len(live)} search page(s) published.", count=len(live),
                      evidence_at=_latest(live, "published_at") or _latest(live, "created_at"))
    return Status("not_proven", "Page generator exists; 0 search pages generated for this product.", count=0)


def product_community_posts(S, pid, now) -> Status:
    rows = _rows(S, "social", pid)
    pending = [r for r in rows if r.get("status") in ("pending_review", "pending_hitl", "draft", "approved")]
    sent = [r for r in rows if r.get("sent_at")]
    week = [r for r in sent if str(r["sent_at"]) >= _iso(now - timedelta(days=7))]
    if pending:
        return Status("waiting_on_you", f"{len(pending)} community post(s) waiting on you to review or post.",
                      count=len(pending))
    if week:
        return Status("working", f"{len(week)} post(s) marked sent in 7 days.", count=len(week),
                      evidence_at=_latest(week, "sent_at"))
    if sent:
        return Status("blocked", f"No post in 7 days; last {_day(_latest(sent, 'sent_at'))}.", count=0,
                      evidence_at=_latest(sent, "sent_at"))
    return Status("not_proven", "0 community posts drafted or sent for this product.", count=0)


INDEXATION_WINDOW_DAYS = 8


def product_search_console(S, pid, now) -> Status:
    product = next((p for p in _rows(S, "products") if p.get("id") == pid), None)
    if not product or not product.get("verified_property_url"):
        return Status("blocked", "Search Console not connected: no verified property for this product.")
    rows = _rows(S, "indexation", pid)
    if not rows:
        return Status("blocked", f"Search Console property {product['verified_property_url']} is set, "
                                 f"but the indexation sync has written 0 rows.", count=0)
    last = _latest(rows, "checked_at")
    if last and last >= _iso(now - timedelta(days=INDEXATION_WINDOW_DAYS)):
        clicks = sum(int(r.get("clicks") or 0) for r in rows)
        return Status("working", f"{len(rows)} URLs synced, {clicks} clicks; last sync {_day(last)}.",
                      count=clicks, evidence_at=last)
    return Status("blocked", f"Indexation sync stale: last row {_day(last)}.", evidence_at=last)


def product_signups(S, pid, now) -> Status:
    data = _external(S, "sph_accounts", pid)
    n, last = data.get("count") or 0, data.get("latest")
    if n:
        return Status("working", f"{n} signup(s); latest {_day(last)}.", count=n, evidence_at=last)
    return Status("not_proven", "Signup is live; 0 accounts created.", count=0)


def product_payments(S, pid, now) -> Status:
    data = _src(S, "stripe_products")
    entry = data.get(pid)
    if entry is None:
        raise SourceUnavailable("no Stripe product mapped to this product")
    if entry.get("active_subscriptions"):
        return Status("working", f"{entry['active_subscriptions']} active subscription(s) in Stripe.",
                      count=entry["active_subscriptions"])
    return Status("not_proven", f"Stripe product {entry.get('stripe_product')} has "
                                f"{entry.get('prices', 0)} active prices and 0 subscriptions.", count=0)


def product_onboarding_emails(S, pid, now) -> Status:
    rows = _rows(S, "email_seqs", pid)
    pending = [r for r in rows if r.get("status") in ("pending_hitl", "pending_review", "draft")]
    approved = [r for r in rows if r.get("hitl_approved_at") or r.get("status") in ("approved", "deployed", "active")]
    if pending:
        return Status("waiting_on_you", f"{len(pending)} email sequence(s) waiting for approval.", count=len(pending))
    if approved:
        return Status("working", f"{len(approved)} email sequence(s) approved.", count=len(approved),
                      evidence_at=_latest(approved, "hitl_approved_at"))
    return Status("not_proven", "0 onboarding email sequences drafted for this product.", count=0)


def product_paying_customers(S, pid, now) -> Status:
    data = S.get("stripe_products")
    stripe_n = None
    if isinstance(data, dict) and not data.get("_error") and pid in data:
        stripe_n = data[pid].get("active_subscriptions") or 0
    product = next((p for p in _rows(S, "products") if p.get("id") == pid), {})
    db_n = int(product.get("paying_customer_count") or 0)
    n = max(db_n, stripe_n or 0)
    src = "Stripe subscriptions" if stripe_n is not None else "mse_products.paying_customer_count"
    if n:
        return Status("working", f"{n} paying customer(s) ({src}).", count=n)
    return Status("not_proven", f"0 paying customers ({src}).", count=0)


def product_build(S, pid, now) -> Status:
    product = next((p for p in _rows(S, "products") if p.get("id") == pid), {})
    briefs = [b for b in _rows(S, "build_briefs")
              if b.get("product_slug") and b.get("product_slug") == product.get("slug")]
    if not briefs:
        return Status("not_built", "No build brief and no repo for this product.")
    b = max(briefs, key=lambda r: str(r.get("updated_at") or ""))
    return Status("not_proven", f"Build brief '{b.get('status')}', dispatch '{b.get('dispatch_status')}'.",
                  evidence_at=b.get("updated_at"))


# --------------------------------------------------------------------------
# DecodedSix (content playbook) -- read from DecodedSix's own Supabase
# --------------------------------------------------------------------------

def _product_slug(S, pid) -> Optional[str]:
    return next((p.get("slug") for p in _rows(S, "products") if p.get("id") == pid), None)


def _external(S, name: str, pid) -> dict:
    """A source read from another product's own database. Only that product's
    desks may use it -- a new product on the same playbook must not inherit
    DecodedSix's or Small Portfolio Hub's numbers."""
    data = _src(S, name)
    if data.get("slug") != _product_slug(S, pid):
        raise SourceUnavailable(f"no {name} source is configured for this product")
    return data


def _d6(S, pid) -> dict:
    return _external(S, "d6", pid)


DISCOVERY_STALE_HOURS = 12  # three missed 4-hourly cycles


def d6_topics(S, pid, now) -> Status:
    d = _d6(S, pid)
    last, week = d.get("topics_latest"), d.get("topics_7d") or 0
    beat = d.get("discovery_latest")
    if beat is not None and str(beat) < _iso(now - timedelta(hours=DISCOVERY_STALE_HOURS)):
        return Status("blocked", f"Discovery has not fetched since {str(beat)[:16].replace('T', ' ')} UTC "
                                 f"(expected every 4 hours); {week} topic(s) in 7 days.",
                      count=week, evidence_at=str(beat))
    if week:
        return Status("working", f"{week} topic(s) found in 7 days.", count=week, evidence_at=last)
    if last and last < _iso(now - timedelta(days=2)):
        return Status("blocked", f"No new topic since {_day(last)} (expected every 4 hours).", count=0, evidence_at=last)
    return Status("not_proven", "0 topics found yet.", count=0)


def d6_drafts(S, pid, now) -> Status:
    d = _d6(S, pid)
    week, last = d.get("articles_created_7d") or 0, d.get("articles_latest")
    if week:
        return Status("working", f"{week} article(s) written in 7 days.", count=week, evidence_at=last)
    return Status("not_proven", f"No article written in 7 days; last {_day(last)}.", count=0, evidence_at=last)


def d6_approval_queue(S, pid, now) -> Status:
    d = _d6(S, pid)
    pending = d.get("hitl_pending") or 0
    if pending:
        return Status("waiting_on_you", f"{pending} item(s) in the DecodedSix approval queue.", count=pending)
    return Status("working", f"Queue clear; last decision {_day(d.get('hitl_last_resolved'))}.", count=0,
                  evidence_at=d.get("hitl_last_resolved"))


def d6_published(S, pid, now) -> Status:
    d = _d6(S, pid)
    week, last = d.get("published_7d") or 0, d.get("published_latest")
    if week:
        return Status("working", f"{week} article(s) published in 7 days ({d.get('published_total')} total).",
                      count=week, evidence_at=last)
    return Status("blocked" if last else "not_proven",
                  f"Nothing published in 7 days; last {_day(last)}.", count=0, evidence_at=last)


def d6_indexed(S, pid, now) -> Status:
    return product_search_console(S, pid, now)


def d6_traffic(S, pid, now) -> Status:
    d = _d6(S, pid)
    week, last = d.get("pageviews_7d") or 0, d.get("pageviews_latest")
    if week:
        return Status("working", f"{week} pageviews in 7 days (first-party analytics).", count=week, evidence_at=last)
    return Status("not_proven", f"0 pageviews in 7 days; last {_day(last)}.", count=0, evidence_at=last)


def d6_earnings(S, pid, now) -> Status:
    _d6(S, pid)
    return Status("unknown", "No affiliate click/conversion or ad revenue data is recorded anywhere "
                             "the platform can read.")


def none_not_built(S, pid, now) -> Status:
    return Status("not_built", "No code path performs this step yet.")


def linkedin_posts(S, pid, now) -> Status:
    rows = _rows(S, "linkedin")
    published = [r for r in rows if r.get("published_at")]
    week = [r for r in published if str(r["published_at"]) >= _iso(now - timedelta(days=7))]
    overdue = [r for r in rows if not r.get("published_at") and r.get("status") in ("approved", "scheduled")
               and r.get("scheduled_for") and str(r["scheduled_for"]) < _iso(now)]
    if overdue:
        return Status("blocked", f"{len(overdue)} approved post(s) past their slot and never published.",
                      count=len(overdue))
    if week:
        return Status("working", f"{len(week)} post(s) published in 7 days.", count=len(week),
                      evidence_at=_latest(week, "published_at"))
    return Status("not_proven", f"No post in 7 days; last {_day(_latest(published, 'published_at'))}.", count=0)


RULES: dict[str, Callable] = {
    "outbound.lead_run": outbound_lead_run,
    "outbound.qualify": outbound_qualify,
    "outbound.find_buyer": outbound_find_buyer,
    "outbound.draft_enrollment": outbound_draft_enrollment,
    "outbound.approve": outbound_approve,
    "outbound.send": outbound_send,
    "outbound.replies": outbound_replies,
    "outbound.calls_booked": outbound_calls_booked,
    "outbound.proposals": outbound_proposals,
    "outbound.icp_configured": outbound_icp_configured,
    "partner.sent": partner_sent,
    "partner.agreed": partner_agreed,
    "cd.demo_sessions": cd_demo_sessions,
    "cd.signup_pay": cd_signup_pay,
    "cd.cloud_connected": cd_cloud_connected,
    "product.positioning_approved": product_positioning,
    "product.search_pages": product_search_pages,
    "product.community_posts": product_community_posts,
    "product.search_console": product_search_console,
    "product.signups": product_signups,
    "product.payments": product_payments,
    "product.onboarding_emails": product_onboarding_emails,
    "product.paying_customers": product_paying_customers,
    "product.build": product_build,
    "d6.topics": d6_topics,
    "d6.drafts": d6_drafts,
    "d6.approval_queue": d6_approval_queue,
    "d6.published": d6_published,
    "d6.indexed": d6_indexed,
    "d6.ranking": d6_indexed,
    "d6.traffic": d6_traffic,
    "d6.earnings": d6_earnings,
    "none.not_built": none_not_built,
    "linkedin.posts": linkedin_posts,
}


def evaluate(rule_key: str, S: dict, product_id: str, now: datetime) -> Status:
    """Never raises. An unreadable source or an unknown key is 'unknown', never green."""
    fn = RULES.get(rule_key)
    if fn is None:
        return Status("unknown", f"No evaluator for rule key '{rule_key}'.")
    try:
        result = fn(S, product_id, now)
    except SourceUnavailable as exc:
        return Status("unknown", f"Source unreadable -- {exc}")
    except Exception as exc:  # a rule bug must surface as unknown, not crash the map
        return Status("unknown", f"Evaluator error: {type(exc).__name__}: {exc}")
    if result.status not in STATUSES:
        return Status("unknown", f"Evaluator returned invalid status '{result.status}'.")
    return result
