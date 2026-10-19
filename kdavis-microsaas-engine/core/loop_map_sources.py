"""
Loop Map sources -- everything core/loop_map_rules.py evaluates, loaded once
per request.

Each source is loaded independently. A source that fails to load is stored as
{"_error": "<what went wrong>"} instead of raising, so the desks that depend on
it render 'unknown' with that text as evidence while every other desk still
renders. Nothing here ever turns a read failure into a green.

External checks (DNS, Resend, Stripe, the DecodedSix and Small Portfolio Hub
databases) are cached in-process for EXTERNAL_TTL_SECONDS: the dashboard
refreshes every 60s and none of those facts change minute to minute.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import httpx

log = logging.getLogger(__name__)

EXTERNAL_TTL_SECONDS = 300

# Cloud Decoded workspaces that are ours, not customers'. There is no
# is_internal flag on workspaces (is_demo_workspace covers only the demo), so
# these are listed explicitly; override with LOOP_MAP_INTERNAL_WORKSPACE_IDS.
_DEFAULT_INTERNAL_WORKSPACES = (
    "03ac055f-a929-4b7a-a566-1fbd38e16c30",  # THD Agentic Systems (Owner Access)
    "fdf61aaf-2fb0-4562-bc9b-7d6ab6e85817",  # Playwright MCP Verification
    "97e058c8-b101-4ae1-b58f-15987995898e",  # Cloud Decoded -- Internal QA
)

_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, fn: Callable[[], Any]) -> Any:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < EXTERNAL_TTL_SECONDS:
        return hit[1]
    value = _guard(key, fn)
    _cache[key] = (time.monotonic(), value)
    return value


def _guard(name: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception as exc:
        log.warning("[LoopMap] source %s unreadable: %s", name, exc)
        return {"_error": f"{type(exc).__name__}: {exc}"[:400]}


def _select(db, table: str, cols: str, order: Optional[str] = None, limit: int = 5000) -> list[dict]:
    q = db.table(table).select(cols)
    if order:
        q = q.order(order, desc=True)
    return q.limit(limit).execute().data or []


# ------------------------------------------------------------------ DNS
def sending_domain() -> str:
    sender = os.environ.get("RESEND_FROM_EMAIL", "")
    if "@" not in sender:
        raise RuntimeError("RESEND_FROM_EMAIL is not set; cannot tell which domain outreach sends from")
    return sender.split("@", 1)[1].strip().strip(">").lower()


def _authoritative_txt(name: str, zone: str) -> tuple[list[str], str]:
    """TXT records for `name`, asked of `zone`'s own nameserver (not a cache)."""
    import dns.message
    import dns.query
    import dns.rcode
    import dns.resolver

    ns_host = str(dns.resolver.resolve(zone, "NS")[0].target).rstrip(".")
    ns_ip = str(dns.resolver.resolve(ns_host, "A")[0])
    resp = dns.query.udp(dns.message.make_query(name, "TXT"), ns_ip, timeout=5)
    if resp.rcode() not in (dns.rcode.NOERROR, dns.rcode.NXDOMAIN):
        raise RuntimeError(f"{ns_host} answered {dns.rcode.to_text(resp.rcode())} for {name}")
    txt = [b"".join(r.strings).decode() for rrset in resp.answer for r in rrset if hasattr(r, "strings")]
    return txt, ns_host


def check_dmarc(domain: str) -> dict:
    """DMARC for the sending domain, falling back to its organizational domain
    (RFC 7489 policy discovery). Asked of the authoritative nameserver."""
    org = ".".join(domain.split(".")[-2:])
    for name in dict.fromkeys((f"_dmarc.{domain}", f"_dmarc.{org}")):
        txt, ns = _authoritative_txt(name, org)
        records = [t for t in txt if t.lower().startswith("v=dmarc1")]
        if records:
            return {"found": True, "checked": name, "record": records[0], "nameserver": ns}
    return {"found": False, "checked": f"_dmarc.{org}", "nameserver": ns}


# --------------------------------------------------------------- Resend
def resend_domains() -> dict:
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        raise RuntimeError("RESEND_API_KEY is not set")
    r = httpx.get("https://api.resend.com/domains", headers={"Authorization": f"Bearer {key}"}, timeout=10)
    r.raise_for_status()
    return {d["name"]: d.get("status") for d in r.json().get("data", [])}


# --------------------------------------------------------------- Stripe
def stripe_products(products: list[dict]) -> dict:
    """{mse product id: {stripe_product, prices, active_subscriptions}} for every
    mse_products row whose name matches an active Stripe product."""
    import stripe

    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    by_name = {(p.get("name") or "").strip().lower(): p["id"] for p in products}
    out: dict[str, dict] = {}
    for sp in stripe.Product.list(active=True, limit=100).auto_paging_iter():
        pid = by_name.get((sp.name or "").strip().lower())
        if not pid:
            continue
        prices = [p.id for p in stripe.Price.list(product=sp.id, active=True, limit=100).auto_paging_iter()]
        subs = sum(1 for price in prices
                   for _ in stripe.Subscription.list(price=price, status="active", limit=100).auto_paging_iter())
        out[pid] = {"stripe_product": sp.id, "prices": len(prices), "active_subscriptions": subs}
    return out


# ------------------------------------------------- other products' databases
def _postgrest_count(url: str, key: str, table: str, params: dict) -> tuple[int, Optional[str]]:
    """(row count, newest created_at-ish value) via PostgREST, without fetching rows."""
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact", "Range": "0-0"}
    order_col = params.pop("_order", "created_at")
    r = httpx.get(f"{url.rstrip('/')}/rest/v1/{table}",
                  params={"select": order_col, "order": f"{order_col}.desc.nullslast", **params},
                  headers=headers, timeout=10)
    r.raise_for_status()
    total = int(r.headers.get("content-range", "*/0").split("/")[-1] or 0)
    rows = r.json()
    return total, (rows[0].get(order_col) if rows else None)


def _env_pair(prefix: str) -> tuple[str, str]:
    url, key = os.environ.get(f"{prefix}_SUPABASE_URL"), os.environ.get(f"{prefix}_SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError(f"mse-api has no {prefix} database credentials "
                           f"({prefix}_SUPABASE_URL / {prefix}_SUPABASE_SERVICE_KEY not set)")
    return url, key


def decodedsix_stats(now: datetime) -> dict:
    url, key = _env_pair("DECODEDSIX")
    week = (now - timedelta(days=7)).isoformat()
    c = lambda t, p: _postgrest_count(url, key, t, dict(p))  # noqa: E731
    _, topics_latest = c("topic_candidates", {})
    topics_7d, _ = c("topic_candidates", {"created_at": f"gte.{week}"})
    _, articles_latest = c("articles", {})
    articles_7d, _ = c("articles", {"created_at": f"gte.{week}"})
    published_total, published_latest = c("articles", {"status": "eq.published", "_order": "published_at"})
    published_7d, _ = c("articles", {"published_at": f"gte.{week}", "_order": "published_at"})
    hitl_pending, _ = c("hitl_queue", {"status": "eq.pending"})
    _, hitl_last = c("hitl_queue", {"resolved_at": "not.is.null", "_order": "resolved_at"})
    pageviews_7d, _ = c("analytics_pageviews", {"created_at": f"gte.{week}"})
    _, pageviews_latest = c("analytics_pageviews", {})
    return {"slug": "decodedsix", "topics_latest": topics_latest, "topics_7d": topics_7d, "articles_latest": articles_latest,
            "articles_created_7d": articles_7d, "published_total": published_total,
            "published_latest": published_latest, "published_7d": published_7d,
            "hitl_pending": hitl_pending, "hitl_last_resolved": hitl_last,
            "pageviews_7d": pageviews_7d, "pageviews_latest": pageviews_latest}


def sph_accounts() -> dict:
    url, key = _env_pair("SPH")
    n, latest = _postgrest_count(url, key, "accounts", {})
    return {"slug": "small-portfolio-hub", "count": n, "latest": latest}


# ------------------------------------------------------------------ main
def load_sources(db, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    S: dict[str, Any] = {"now": now.isoformat()}
    tables = {
        "products": ("mse_products", "id,slug,name,verified_property_url,paying_customer_count", None),
        "leads": ("mse_leads", "id,product_id,status,contact_status,lead_route,created_at", None),
        "seqs": ("mse_dm_sequences", "id,product_id,status,created_at,hitl_approved_at,"
                                     "touch_1_sent_at,touch_2_sent_at,touch_3_sent_at", None),
        "convs": ("mse_outreach_conversations", "product_id,stage,replied_at,call_booked_at,"
                                                "proposal_sent_at,won_at,lost_at", None),
        "runs": ("mse_lead_finder_runs", "id,product_id,status,started_at,completed_at,leads_found,"
                                         "error_message,funnel_stats,sources_used", "started_at"),
        "icp": ("mse_icp_configs", "product_id,outbound_enabled", None),
        "positioning": ("mse_positioning", "product_id,version,status,approved_at", None),
        "surfaces": ("mse_content_surfaces", "product_id,status,published_at,created_at", None),
        "seo": ("mse_seo_content", "product_id,status,created_at", None),
        "social": ("mse_social_content", "product_id,status,created_at,sent_at", None),
        "email_seqs": ("mse_email_sequences", "product_id,status,created_at,hitl_approved_at", None),
        "indexation": ("mse_indexation", "product_id,clicks,impressions,checked_at", "checked_at"),
        "build_briefs": ("mse_build_briefs", "product_slug,status,dispatch_status,updated_at", None),
        "workspaces": ("workspaces", "id,company_name,is_demo_workspace,stripe_subscription_status,created_at,"
                                     "aws_role_verified_at,azure_verified_at,github_pat_verified_at,k8s_verified_at", None),
        "demo_sessions": ("demo_sessions", "created_at", "created_at"),
        "linkedin": ("linkedin_content_queue", "status,published_at,scheduled_for", "created_at"),
    }
    # Independent reads, so in parallel: sequentially they took ~3.5s warm.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {name: pool.submit(_guard, name, lambda t=table, c=cols, o=order: _select(db, t, c, o))
                   for name, (table, cols, order) in tables.items()}
    for name, fut in futures.items():
        S[name] = fut.result()

    S["internal_workspace_ids"] = [w.strip() for w in os.environ.get(
        "LOOP_MAP_INTERNAL_WORKSPACE_IDS", ",".join(_DEFAULT_INTERNAL_WORKSPACES)).split(",") if w.strip()]

    domain = _guard("sending_domain", sending_domain)
    S["sending_domain"] = domain if isinstance(domain, str) else None
    S["dmarc"] = (_cached(f"dmarc:{domain}", lambda: check_dmarc(domain)) if isinstance(domain, str)
                  else domain)
    S["resend_domains"] = _cached("resend_domains", resend_domains)

    def _budget():
        from agents.marketing.brave_budget import check_budget
        return check_budget(db).as_dict()
    S["brave_budget"] = _guard("brave_budget", _budget)

    def _paused():
        from core.email_compliance import sends_paused
        paused, reason = sends_paused(db)
        return {"paused": paused, "reason": reason}
    S["sends_paused"] = _guard("sends_paused", _paused)

    products = S["products"] if isinstance(S["products"], list) else []
    S["stripe_products"] = _cached("stripe_products", lambda: stripe_products(products))
    S["d6"] = _cached("d6", lambda: decodedsix_stats(now))
    S["sph_accounts"] = _cached("sph_accounts", sph_accounts)
    return S
