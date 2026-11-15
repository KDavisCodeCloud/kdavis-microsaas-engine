"""
core/lead_source_yield.py

Per-source yield and the weekly target (lead-source expansion, Phase 7,
2026-11-15). One implementation, read by the 8am digest
(GET /marketing/outreach/summary) and the Loop Map's Step 1 card, so the two
cannot disagree.

A lead's SOURCE is where the company came from:
  hn               Hacker News hiring thread (mse_leads.source='hn_thread')
  slug_discovery   an ATS board found by the weekly Brave slug discovery
  form_d           an ATS board seeded from an SEC Form D filer
  wellfound        an ATS board resolved from a Wellfound result title
  vc_portfolio     an ATS board from a curated fund's portfolio page
  ats_cache        an ATS board already in the cache before 2026-11-15
                   (or discovered by the scouts' own old discovery)
For ATS leads the board is found from the lead's posting URL, and its
mse_ats_board_tokens.discovered_from names the origin.

Per source, over the window: companies qualified (not purged), named buyer
(a contact on the lead), verified buyer email (grade 'valid'). Plus how many
named buyers the resolver supplied, by resolver source.

Weekly target (the brief): 40+ qualified companies, 15+ with a named buyer,
10+ verified buyer emails.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

WEEKLY_TARGET = {"qualified": 40, "named_buyer": 15, "verified_email": 10}
SOURCE_PRODUCT_IDS = ("777a1852-f84c-49d8-890e-cd14670b7f6f", "9b6c8f36-985f-4d7a-a416-c40da89e23af")
PURGED = ("rejected_wrong_icp", "disqualified")
ORIGIN_BY_DISCOVERED_FROM = {
    "brave_slug_discovery": "slug_discovery",
    "form_d": "form_d",
    "wellfound_signal": "wellfound",
    "vc_portfolio": "vc_portfolio",
}
SOURCE_ORDER = ("ats_cache", "slug_discovery", "form_d", "wellfound", "vc_portfolio", "hn")


def _page(db, table: str, select: str, **gte) -> list[dict]:
    out, start = [], 0
    while True:
        q = db.table(table).select(select)
        for k, v in gte.items():
            q = q.gte(k, v)
        page = q.range(start, start + 999).execute().data or []
        out += page
        if len(page) < 1000:
            return out
        start += 1000


def _name_key(name: Optional[str]) -> str:
    from agents.marketing.company_classification import distinctive_company_name
    return distinctive_company_name(name or "")


def lead_origin(lead: dict, token_origin: dict[str, str], name_origin: Optional[dict[str, str]] = None) -> str:
    """Board from the posting URL; when the URL is the company's own careers
    page (Greenhouse absolute_url often is), the board whose display name is
    the lead's company."""
    from scrapers.ats_boards import board_ref_from_url

    if lead.get("source") == "hn_thread":
        return "hn"
    ref = board_ref_from_url(lead.get("job_posting_url") or "")
    origin = token_origin.get(ref.cache_key) if ref else (name_origin or {}).get(_name_key(lead.get("company")))
    return ORIGIN_BY_DISCOVERED_FROM.get(origin or "", "ats_cache")


def compute_yield(leads: list[dict], tokens: list[dict]) -> dict:
    token_origin = {f"{t['provider']}:{t['board_token']}": t.get("discovered_from") for t in tokens
                    if t.get("provider") and t.get("board_token")}
    name_origin = {_name_key(t.get("company")): t.get("discovered_from") for t in tokens if t.get("company")}
    by: dict[str, dict] = {}
    resolver: dict[str, int] = {}
    total = {"qualified": 0, "named_buyer": 0, "verified_email": 0}
    for lead in leads:
        if lead.get("status") in PURGED:
            continue
        src = lead_origin(lead, token_origin, name_origin)
        row = by.setdefault(src, {"qualified": 0, "named_buyer": 0, "verified_email": 0})
        named = bool(lead.get("first_name") and lead.get("title"))
        verified = named and lead.get("email_grade") == "valid"
        row["qualified"] += 1
        total["qualified"] += 1
        if named:
            row["named_buyer"] += 1
            total["named_buyer"] += 1
            via = (lead.get("buyer_suggestion") or {}).get("source")
            if via:
                resolver[via] = resolver.get(via, 0) + 1
        if verified:
            row["verified_email"] += 1
            total["verified_email"] += 1
    return {
        "by_source": {k: by[k] for k in SOURCE_ORDER if k in by} | {k: v for k, v in by.items()
                                                                    if k not in SOURCE_ORDER},
        "total": total,
        "named_by_resolver_source": resolver,
        "target": WEEKLY_TARGET,
        "on_target": {k: total[k] >= WEEKLY_TARGET[k] for k in WEEKLY_TARGET},
    }


def source_yield(db, *, days: int = 7, now: Optional[datetime] = None) -> dict:
    """Leads CREATED in the window (company found this week), CD + consulting."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    leads = [r for r in _page(db, "mse_leads", "product_id,source,status,job_posting_url,company,first_name,title,"
                                               "email_grade,buyer_suggestion", created_at=since)
             if r.get("product_id") in SOURCE_PRODUCT_IDS]
    tokens = _page(db, "mse_ats_board_tokens", "provider,board_token,company,discovered_from")
    out = compute_yield(leads, tokens)
    out["window_days"] = days
    out["since"] = since
    return out


def yield_lines(y: dict) -> list[str]:
    """Plain-text lines for the 8am email and the Loop Map card."""
    t, tgt = y["total"], y["target"]
    lines = [f"Lead sources, last {y.get('window_days', 7)} days "
             f"(target {tgt['qualified']} companies / {tgt['named_buyer']} named buyers / "
             f"{tgt['verified_email']} verified emails):",
             f"  total            : {t['qualified']} / {t['named_buyer']} / {t['verified_email']}"]
    for src, row in y["by_source"].items():
        lines.append(f"  {src:<17}: {row['qualified']} / {row['named_buyer']} / {row['verified_email']}")
    if y.get("named_by_resolver_source"):
        lines.append("  buyers named by the resolver: " + ", ".join(
            f"{k} {v}" for k, v in sorted(y["named_by_resolver_source"].items())))
    return lines
