# REFACTOR CANDIDATE: over 300 lines (client, parser, ingest, match, seed).
"""
agents/marketing/form_d.py

SEC Form D as a lead source: funding date + named executive officers
(lead-source expansion, Phase 3, 2026-11-15).

A Form D is filed within 15 days of the first sale in an exempt offering --
for a startup, its priced round. Each filing names the issuer, its industry
group, the offering amount and every "related person" (executive officers,
directors, promoters). Public, free, and unambiguous about WHO runs the
company -- which is exactly what Find the Buyer is missing.

OFFICIAL ENDPOINTS ONLY (checked against https://www.sec.gov/robots.txt
2026-11-15):
  * daily form index   www.sec.gov/Archives/edgar/daily-index/{Y}/QTR{q}/form.{YYYYMMDD}.idx
  * the filing itself  www.sec.gov/Archives/edgar/data/{cik}/{accession}/primary_doc.xml
robots.txt allows /Archives/edgar/data and does not disallow the daily index.
It DISALLOWS /cgi-bin, so EDGAR's company-name search (cgi-bin/browse-edgar)
is NOT used: matching a pipeline company to a filing is done against our own
index of filings, by normalised name. efts.sec.gov (full-text search) is not
a documented public API and refused a robots.txt read; not used either.

FAIR ACCESS (https://www.sec.gov/os/accessing-edgar-data): a User-Agent that
declares who we are with a contact email (SEC_EDGAR_USER_AGENT, falling back
to the business sending address in RESEND_FROM_EMAIL -- never a personal
address), and at most 10 requests a second. This client holds itself to 5.

WHAT IT PRODUCES
  ingest      mse_form_d_filings rows for every Form D in the window whose
              issuer name is not obviously a fund / SPV / real-estate vehicle
  match       pipeline leads (CD + consulting, not purged) whose company name
              matches exactly one issuer: funding date + amount on the lead,
              officers as buyer_candidates (source 'form_d')
  seed        tech filers (Computers / Other Technology / Telecommunications)
              funded within 180 days, not already in the pipeline or
              suppressed, whose ATS slug RESOLVES and whose board names the
              same company -> queued in mse_ats_board_tokens
              (discovered_from='form_d'), so Sunday's scout qualifies them
              through the normal company rules
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

import httpx

log = logging.getLogger(__name__)

SEC_BASE = "https://www.sec.gov"
MAX_REQUESTS_PER_SECOND = 5          # SEC allows 10; we stay at half
FILINGS_TABLE = "mse_form_d_filings"
INDEX_DAYS_TABLE = "mse_form_d_index_days"
TECH_INDUSTRY_GROUPS = frozenset({"Computers", "Other Technology", "Telecommunications"})
FUNDING_WINDOW_DAYS = 180

_INDEX_LINE = re.compile(r"^(D|D/A)\s+(.+?)\s{2,}(\d{1,10})\s+(\d{8})\s+(edgar/data/\S+\.txt)\s*$")
# Issuer names that are investment vehicles, not operating companies. Form D
# is dominated by funds and SPVs; skipping them by NAME saves the fetch.
_VEHICLE_NAME = re.compile(
    r"\b(?:fund|funds|l\.?p\.?|lp|series|spv|feeder|master|trust|reit|capital partners|partners fund"
    r"|investors?|investment|ventures? (?:fund|partners)|holdings? (?:i|ii|iii|iv|v)\b|opportunit(?:y|ies)"
    r"|real estate|realty|properties|property|apartments|homes|housing|land|acquisition corp"
    r"|portfolio|co-?invest|syndicate|offshore|onshore|income|yield|credit|lending|mortgage|notes"
    r"|\d{4}-[a-z]|a series of)\b",
    re.IGNORECASE)


def user_agent() -> str:
    explicit = (os.getenv("SEC_EDGAR_USER_AGENT") or "").strip()
    if explicit:
        if "@" not in explicit:
            raise RuntimeError("SEC_EDGAR_USER_AGENT must include a contact email (SEC fair-access policy)")
        return explicit
    sender = (os.getenv("RESEND_FROM_EMAIL") or "").strip()
    m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", sender)
    if not m:
        raise RuntimeError("set SEC_EDGAR_USER_AGENT ('Company contact@domain') -- SEC requires a contact email")
    return f"THD Agentic Systems LLC {m.group(0)}"


class SecClient:
    """Rate-limited GET against www.sec.gov. Thread-safe limiter."""

    def __init__(self, http_get: Optional[Callable[..., Any]] = None, *,
                 sleep: Optional[Callable[[float], None]] = None, clock: Optional[Callable[[], float]] = None,
                 ua: Optional[str] = None):
        self._get = http_get or httpx.get
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._ua = ua or user_agent()
        self._lock = threading.Lock()
        self._last = float("-inf")
        self.requests = 0
        self.errors: dict[str, int] = {}

    def get(self, url: str, *, retries: int = 2) -> Optional[Any]:
        for attempt in range(retries + 1):
            with self._lock:
                wait = (1.0 / MAX_REQUESTS_PER_SECOND) - (self._clock() - self._last)
                if wait > 0:
                    self._sleep(wait)
                self._last = self._clock()
                self.requests += 1
            try:
                r = self._get(url, timeout=30, headers={"User-Agent": self._ua, "Accept-Encoding": "gzip, deflate"})
            except Exception as exc:
                self.errors["request_error"] = self.errors.get("request_error", 0) + 1
                log.warning("[FormD] %s failed: %s", url, exc)
                continue
            code = getattr(r, "status_code", 0)
            if code == 200:
                return r
            if code == 404:
                self.errors["http_404"] = self.errors.get("http_404", 0) + 1
                return None
            self.errors[f"http_{code}"] = self.errors.get(f"http_{code}", 0) + 1
            if code in (429, 503) and attempt < retries:
                self._sleep(10 * (attempt + 1))   # back off hard; SEC throttles by IP
                continue
            return None
        return None


# ── parsing ───────────────────────────────────────────────────────────────

@dataclass
class IndexEntry:
    form: str
    company: str
    cik: str
    filed_on: date
    path: str

    @property
    def accession(self) -> str:
        return self.path.rsplit("/", 1)[-1].removesuffix(".txt")


def index_url(day: date) -> str:
    q = (day.month - 1) // 3 + 1
    return f"{SEC_BASE}/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx"


def parse_index(text: str) -> list[IndexEntry]:
    out = []
    for line in (text or "").splitlines():
        m = _INDEX_LINE.match(line.rstrip())
        if not m:
            continue
        form, company, cik, filed, path = m.groups()
        out.append(IndexEntry(form, company.strip(), cik, datetime.strptime(filed, "%Y%m%d").date(), path))
    return out


def is_vehicle_name(name: str) -> bool:
    return bool(_VEHICLE_NAME.search(name or ""))


def primary_doc_url(entry: IndexEntry) -> str:
    return f"{SEC_BASE}/Archives/edgar/data/{int(entry.cik)}/{entry.accession.replace('-', '')}/primary_doc.xml"


def _text(node: Optional[ET.Element], path: str) -> Optional[str]:
    if node is None:
        return None
    el = node.find(path)
    return el.text.strip() if el is not None and el.text and el.text.strip() else None


def _money(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # "Indefinite"


def parse_primary_doc(xml_text: str) -> dict:
    """The fields we use from a Form D primary_doc.xml. Missing -> None."""
    root = ET.fromstring(xml_text)
    issuer = root.find("primaryIssuer")
    offering = root.find("offeringData")
    persons = []
    for p in root.findall("relatedPersonsList/relatedPersonInfo"):
        first = _text(p, "relatedPersonName/firstName")
        last = _text(p, "relatedPersonName/lastName")
        rels = [r.text.strip() for r in p.findall("relatedPersonRelationshipList/relationship") if r.text]
        persons.append({
            "first_name": first, "last_name": last,
            "name": " ".join(x for x in (first, last) if x) or None,
            "relationships": rels,
            "clarification": _text(p, "relationshipClarification"),
        })
    first_sale = _text(offering, "typeOfFiling/dateOfFirstSale/value")
    return {
        "issuer_name": _text(issuer, "entityName"),
        "entity_type": _text(issuer, "entityType"),
        "state": _text(issuer, "issuerAddress/stateOrCountry"),
        "year_of_inc": _text(issuer, "yearOfInc/value"),
        "industry_group": _text(offering, "industryGroup/industryGroupType"),
        "total_offering_amount": _money(_text(offering, "offeringSalesAmounts/totalOfferingAmount")),
        "total_amount_sold": _money(_text(offering, "offeringSalesAmounts/totalAmountSold")),
        "date_of_first_sale": first_sale if first_sale and re.match(r"\d{4}-\d{2}-\d{2}$", first_sale) else None,
        "is_amendment": (_text(offering, "typeOfFiling/newOrAmendment/isAmendment") or "").lower() == "true",
        "related_persons": persons,
    }


def norm(name: Optional[str]) -> str:
    """Legal suffixes dropped ("Chai Discovery, Inc." == "Chaidiscovery"), so
    a filer's legal name meets the ATS display name."""
    from agents.marketing.company_classification import distinctive_company_name
    return distinctive_company_name(name or "")


# A matched name this short ("cape", "oneapp") is too common to trust on name
# alone: the filing is still linked, but its officers are low confidence and
# never auto-promoted.
MIN_CONFIDENT_NAME_LEN = 7


# ── officers as buyer candidates ─────────────────────────────────────────

_TITLE_IN_CLARIFICATION = re.compile(
    r"\b(?:chief technology officer|CTO|co-?founder|founder|chief executive officer|CEO|president"
    r"|VP(?: of)? engineering|head of engineering)\b", re.IGNORECASE)


def officer_candidates(filing: dict) -> list[dict]:
    """Executive officers as buyer_candidates. The Form D relationship is
    'Executive Officer' / 'Director' / 'Promoter' -- not a job title -- so the
    candidate's title is the filing's own clarification when it states one
    ("CEO", "CTO and Co-Founder"), else "Executive Officer (Form D)", which the
    buyer gate reads as AMBIGUOUS. Nobody becomes a contact from this alone."""
    from agents.marketing.buyer_gate import classify_buyer_title

    out = []
    for p in filing.get("related_persons") or []:
        if "Executive Officer" not in (p.get("relationships") or []) or not p.get("name"):
            continue
        clar = p.get("clarification") or ""
        m = _TITLE_IN_CLARIFICATION.search(clar)
        title = clar.strip() if m else "Executive Officer (Form D)"
        verdict = classify_buyer_title(title).verdict
        out.append({
            "name": p["name"], "title": title, "email": None, "email_grade": None,
            "source": "form_d",
            "confidence": "high" if verdict == "allow" and re.search(r"CTO|technology|founder", title, re.I)
            else "medium" if verdict == "allow" else "low",
            "title_verdict": verdict,
            "evidence": f"SEC Form D {filing.get('accession_no')}: {', '.join(p.get('relationships') or [])}"
                        + (f" ({clar})" if clar else ""),
            "evidence_url": filing.get("filing_url"),
        })
    # CTO / founder first, then CEO, then plain officers.
    rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda c: rank[c["confidence"]])


# ── ingest ────────────────────────────────────────────────────────────────

@dataclass
class FormDStats:
    days_read: list[str] = field(default_factory=list)
    days_missing: int = 0
    index_entries: int = 0
    skipped_vehicle_name: int = 0
    skipped_known: int = 0
    docs_fetched: int = 0
    docs_failed: int = 0
    filings_written: int = 0
    tech_filings: int = 0
    leads_matched: int = 0
    leads_ambiguous: int = 0
    candidates_added: int = 0
    funding_intent_boosts: int = 0
    seed_considered: int = 0
    seed_slug_resolved: int = 0
    seed_tokens_queued: int = 0
    seed_skipped_existing: int = 0
    sec_requests: int = 0
    sec_errors: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _days_to_read(db, today: date, *, window_days: int, max_days: int) -> list[date]:
    done = set()
    try:
        rows = (db.table(INDEX_DAYS_TABLE).select("day")
                .gte("day", (today - timedelta(days=window_days)).isoformat()).execute().data or [])
        done = {r["day"] for r in rows}
    except Exception as exc:
        log.warning("[FormD] could not read processed days (%s); reading the newest only", exc)
    days = []
    d = today - timedelta(days=1)
    while d >= today - timedelta(days=window_days) and len(days) < max_days:
        if d.weekday() < 5 and d.isoformat() not in done:
            days.append(d)
        d -= timedelta(days=1)
    return days


def ingest(db, client: SecClient, *, today: Optional[date] = None, window_days: int = FUNDING_WINDOW_DAYS,
           max_days: int = 10, max_docs: int = 1500, stats: Optional[FormDStats] = None) -> FormDStats:
    """Read up to `max_days` unprocessed index days (newest first) inside the
    window, fetch each non-vehicle Form D, write mse_form_d_filings."""
    today = today or date.today()
    stats = stats or FormDStats()
    for day in _days_to_read(db, today, window_days=window_days, max_days=max_days):
        r = client.get(index_url(day))
        if r is None:
            # Holiday or not yet published: record nothing, retry next run.
            stats.days_missing += 1
            continue
        entries = [e for e in parse_index(r.text) if e.form in ("D", "D/A")]
        stats.index_entries += len(entries)
        fetched_today = 0
        for e in entries:
            if stats.docs_fetched >= max_docs:
                break
            if is_vehicle_name(e.company):
                stats.skipped_vehicle_name += 1
                continue
            doc = client.get(primary_doc_url(e))
            stats.docs_fetched += 1
            fetched_today += 1
            if doc is None:
                stats.docs_failed += 1
                continue
            try:
                f = parse_primary_doc(doc.text)
            except ET.ParseError:
                stats.docs_failed += 1
                continue
            row = {
                "accession_no": e.accession, "cik": e.cik, "form_type": e.form, "filed_on": e.filed_on.isoformat(),
                "issuer_name": f["issuer_name"] or e.company, "issuer_norm": norm(f["issuer_name"] or e.company),
                "entity_type": f["entity_type"], "state": f["state"], "year_of_inc": f["year_of_inc"],
                "industry_group": f["industry_group"], "total_offering_amount": f["total_offering_amount"],
                "total_amount_sold": f["total_amount_sold"], "date_of_first_sale": f["date_of_first_sale"],
                "related_persons": f["related_persons"], "filing_url": primary_doc_url(e),
            }
            if f["industry_group"] in TECH_INDUSTRY_GROUPS:
                stats.tech_filings += 1
            try:
                db.table(FILINGS_TABLE).upsert(row, on_conflict="accession_no").execute()
                stats.filings_written += 1
            except Exception as exc:
                log.warning("[FormD] write failed for %s: %s", e.accession, exc)
        if stats.docs_fetched >= max_docs and fetched_today < len(entries):
            break  # day not finished; it is read again next run
        db.table(INDEX_DAYS_TABLE).upsert({"day": day.isoformat(), "entries": len(entries),
                                           "processed_at": datetime.now(timezone.utc).isoformat()},
                                          on_conflict="day").execute()
        stats.days_read.append(day.isoformat())
    stats.sec_requests = client.requests
    stats.sec_errors = dict(client.errors)
    return stats


# ── match pipeline leads ─────────────────────────────────────────────────

def _all(db, table: str, select: str, **filters) -> list[dict]:
    out, start = [], 0
    while True:
        q = db.table(table).select(select)
        for k, v in filters.items():
            q = q.in_(k, v) if isinstance(v, (list, tuple)) else q.eq(k, v)
        page = q.range(start, start + 999).execute().data or []
        out += page
        if len(page) < 1000:
            return out
        start += 1000


def filings_by_norm(db, since: date) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    out, start = [], 0
    while True:
        page = (db.table(FILINGS_TABLE).select("*").gte("filed_on", since.isoformat())
                .range(start, start + 999).execute().data or [])
        out += page
        if len(page) < 1000:
            break
        start += 1000
    for f in out:
        if f.get("issuer_norm"):
            by.setdefault(f["issuer_norm"], []).append(f)
    return by


def match_pipeline(db, *, today: Optional[date] = None, stats: Optional[FormDStats] = None,
                   product_ids: Iterable[str] = ()) -> FormDStats:
    """Attach funding + officer candidates to pipeline leads whose company
    name matches exactly ONE issuer (by CIK). Two different issuers with the
    same normalised name is ambiguous and left alone."""
    from agents.marketing.mkt_lead_finder import CLOUD_DECODED_PRODUCT_ID, THDAGENTIC_CONSULTING_PRODUCT_ID

    today = today or date.today()
    stats = stats or FormDStats()
    pids = list(product_ids) or [CLOUD_DECODED_PRODUCT_ID, THDAGENTIC_CONSULTING_PRODUCT_ID]
    by_norm = filings_by_norm(db, today - timedelta(days=FUNDING_WINDOW_DAYS))
    leads = _all(db, "mse_leads", "id,product_id,company,status,funding_date,form_d_accession,buyer_candidates,"
                                  "intent_score,score_reasons", product_id=pids)
    for lead in leads:
        if lead.get("status") in ("rejected_wrong_icp", "disqualified"):
            continue
        hits = by_norm.get(norm(lead.get("company")))
        if not hits:
            continue
        if len({h["cik"] for h in hits}) > 1:
            stats.leads_ambiguous += 1
            continue
        latest = max(hits, key=lambda h: h["filed_on"])
        if lead.get("form_d_accession") == latest["accession_no"]:
            continue
        stats.leads_matched += 1
        update = funding_update(lead, latest, today)
        if "intent_score" in update:
            stats.funding_intent_boosts += 1
        cands = officer_candidates({**latest})
        if len(norm(lead.get("company"))) < MIN_CONFIDENT_NAME_LEN:
            for c in cands:
                c["confidence"] = "low"
                c["evidence"] += " -- name-only match on a short company name; confirm it is the same company"
        existing = [c for c in (lead.get("buyer_candidates") or []) if c.get("source") != "form_d"]
        if cands:
            update["buyer_candidates"] = existing + cands
            stats.candidates_added += len(cands)
        db.table("mse_leads").update(update).eq("id", lead["id"]).execute()
    return stats


# Consulting reads a recent round as budget for outside help (brief, Phase 6).
CONSULTING_FUNDING_INTENT_BOOST = 0.10


def funding_update(lead: dict, filing: dict, today: date) -> dict:
    from agents.marketing.mkt_lead_finder import THDAGENTIC_CONSULTING_PRODUCT_ID

    # The FILING date, not dateOfFirstSale: a continuing offering reports a
    # first sale years back (live 2026-11-13: Bitpulse filed with a 2023 first
    # sale). The filing is the evidence of new money now.
    funded_on = filing["filed_on"]
    update = {
        "funding_date": funded_on,
        "funding_amount": filing.get("total_amount_sold") or filing.get("total_offering_amount"),
        "funding_source": "sec_form_d",
        "form_d_accession": filing["accession_no"],
    }
    age = (today - date.fromisoformat(str(funded_on)[:10])).days
    first_time = not lead.get("funding_date")
    if (lead.get("product_id") == THDAGENTIC_CONSULTING_PRODUCT_ID and first_time
            and 0 <= age <= FUNDING_WINDOW_DAYS and lead.get("intent_score") is not None):
        update["intent_score"] = round(min(1.0, float(lead["intent_score"]) + CONSULTING_FUNDING_INTENT_BOOST), 3)
        amount = update["funding_amount"]
        update["score_reasons"] = list(lead.get("score_reasons") or []) + [
            f"Form D funding {funded_on}" + (f" (${amount:,.0f})" if amount else "")
            + f", {age} days ago (+{CONSULTING_FUNDING_INTENT_BOOST:.2f})"]
    return update


# ── seed new companies ───────────────────────────────────────────────────

def slug_variants(issuer_name: str) -> list[str]:
    """Board-token guesses from a legal name, most specific first. "Acme
    Robotics, Inc." -> acmerobotics, acme-robotics. Never the bare first word
    ("acme"): a short token is the likeliest to be someone else's board."""
    base = re.sub(r"[,.]?\s+(?:inc|incorporated|corp|corporation|co|company|llc|ltd|limited|pbc|plc)\.?$", "",
                  issuer_name.strip(), flags=re.I)
    words = [w for w in re.split(r"[^a-z0-9]+", base.lower()) if w]
    if not words or len("".join(words)) < 4:
        return []
    out = ["".join(words), "-".join(words)]
    if words[-1] in ("ai", "labs", "technologies", "technology", "software", "systems", "inc"):
        out.append("".join(words[:-1]) + words[-1])
    return list(dict.fromkeys(v for v in out if len(v) >= 4))


def board_names_company(postings, issuer_name: str, provider: str, token: str) -> bool:
    """A guessed board is accepted only if it is demonstrably this company:
    the board's own display name normalises to the issuer's, or (Lever/Ashby,
    which return no display name) the token IS the full normalised name AND a
    posting mentions the company name."""
    from agents.marketing.company_classification import text_mentions_company

    key = norm(issuer_name)
    names = {norm(p.company) for p in postings if p.company and p.company != _token_fallback(token)}
    if key and key in names:
        return True
    if provider in ("lever", "ashby") and token.replace("-", "") == key:
        return any(text_mentions_company(p.description_text, issuer_name) for p in postings[:10])
    return False


def _token_fallback(token: str) -> str:
    from scrapers.ats_boards import company_name_from_token
    return company_name_from_token(token)


def resolve_company_slug(company_name: str, ats_client, known: set[str]):
    """("known" | "new", BoardRef) for the company's ATS board, or None.

    Direct ATS API calls only (no Brave): each name variant is tried on each
    provider; a board already in the cache is "known" without a fetch, and a
    new one is accepted only when board_names_company() proves it is this
    company. Shared by the Form D and Wellfound seeds."""
    from scrapers.ats_boards import PROVIDERS, BoardRef

    for token in slug_variants(company_name):
        for provider in PROVIDERS:
            ref = BoardRef(provider, token)
            if ref.cache_key in known:
                return ("known", ref)
        for provider in PROVIDERS:
            ref = BoardRef(provider, token)
            postings = ats_client.fetch_board(ref)
            if postings and board_names_company(postings, company_name, provider, token):
                return ("new", ref)
    return None


def seed_from_filings(db, ats_client, *, today: Optional[date] = None, max_companies: int = 60,
                      stats: Optional[FormDStats] = None) -> FormDStats:
    from agents.marketing.mkt_lead_finder import _existing_job_signal_companies
    from agents.marketing.slug_discovery import known_slugs

    today = today or date.today()
    stats = stats or FormDStats()
    since = today - timedelta(days=FUNDING_WINDOW_DAYS)
    filings = [f for fs in filings_by_norm(db, since).values() for f in fs
               if f.get("industry_group") in TECH_INDUSTRY_GROUPS]
    existing = {norm(n) for n in _existing_job_signal_companies(db)} | _existing_job_signal_companies(db)
    suppressed = {r["company_norm"] for r in (db.table("mse_suppressed_companies").select("company_norm")
                                              .execute().data or []) if r.get("company_norm")}
    known = known_slugs(db)
    tried = {r["issuer_norm"] for r in (db.table(FILINGS_TABLE).select("issuer_norm")
                                        .not_.is_("seed_checked_at", "null").gte("filed_on", since.isoformat())
                                        .execute().data or [])}
    seen = set()
    for f in sorted(filings, key=lambda x: x["filed_on"], reverse=True):
        key = f["issuer_norm"]
        if key in seen or key in tried:
            continue
        seen.add(key)
        from agents.marketing.company_classification import normalise_company_name
        if key in existing or normalise_company_name(f["issuer_name"]) in existing or key in suppressed:
            stats.seed_skipped_existing += 1
            continue
        if stats.seed_considered >= max_companies:
            break
        stats.seed_considered += 1
        found = resolve_company_slug(f["issuer_name"], ats_client, known)
        db.table(FILINGS_TABLE).update({"seed_checked_at": datetime.now(timezone.utc).isoformat(),
                                        "seed_board": found[1].cache_key if found else None}
                                       ).eq("accession_no", f["accession_no"]).execute()
        if not found:
            continue
        stats.seed_slug_resolved += 1
        if found[0] == "new":
            db.table("mse_ats_board_tokens").upsert({
                "provider": found[1].provider, "board_token": found[1].token, "company": f["issuer_name"],
                "discovered_from": "form_d", "updated_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="provider,board_token").execute()
            stats.seed_tokens_queued += 1
            known.add(found[1].cache_key)
    return stats


def run_form_d(db, *, client: Optional[SecClient] = None, ats_client=None, today: Optional[date] = None,
               max_days: int = 10, max_docs: int = 1500, max_seed: int = 60) -> dict:
    """The scheduled job: ingest new index days, match the pipeline, seed."""
    from scrapers.ats_boards import AtsBoardClient

    client = client or SecClient()
    stats = ingest(db, client, today=today, max_days=max_days, max_docs=max_docs)
    match_pipeline(db, today=today, stats=stats)
    seed_from_filings(db, ats_client or AtsBoardClient(), today=today, max_companies=max_seed, stats=stats)
    stats.sec_requests = client.requests
    stats.sec_errors = dict(client.errors)
    log.warning("[FormD] %s", stats.as_dict())
    return {"status": "complete", "leads_written": 0, "funnel_stats": stats.as_dict()}
