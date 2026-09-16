# THD Consulting Lead Scout

Finds SMBs that show public signals of poor security hygiene, for Kelvin's
own IT-security-implementation service (MFA, RBAC, least privilege, zero
trust, failover planning, security policy documentation). This is a
prospecting tool for THD Agentic Systems' own consulting line — it is
**not** an MSE product, and it does not touch `mse_leads`/`mse_icp_configs`.

## What it actually does (and doesn't)

Reuses this repo's existing, real, zero-cost lead-finder infrastructure
(`agents/marketing/mkt_lead_finder.py`'s pattern) rather than the original
task spec's literal source list:

| Spec asked for | What's actually built | Why |
|---|---|---|
| LinkedIn public-page scraping | Not built — `contact_linkedin` stays empty unless entered manually | This repo's own lead-finder already ruled this out (`mkt_lead_finder.py`'s docstring: "no LinkedIn scraping") and `kdavis-agentic-platform/api/routes/outreach.py` carries the same compliance boundary. LinkedIn's ToS prohibits it regardless of login state. |
| Indeed / Glassdoor job-posting scraping | Not built | No public, unauthenticated API for either; same "don't scrape what has no public API" rule `agents/internal/research_agent.py` already applies to G2/Quora. |
| Clutch.co / G2 / Yelp Business listings | Not built | Same reason. |
| Apollo.io API | Not built | Already tried and dropped in this exact codebase 2026-08-14 — Free plan has no API access, paid plan is a recurring cost this system was rebuilt specifically to avoid. |
| Hunter.io API | Not built | Paid per-lookup API; `core/email_finder.py` already does pattern-guessing + real SMTP verification at zero ongoing cost — same job, no bill. |
| Google Places API | Not built | Google Custom Search (below) already covers company discovery; adding a second Google API for marginal benefit wasn't worth the extra credential to manage. |
| Google Custom Search | **Built** (`scrapers/google_search.py`, official API, never scraped HTML) | Already the house Source 1 for every product's lead finder. |
| Public license/registry scraping | **Built for construction** (`scrapers/verticals/trades.py`) | Reused as-is. Every other target industry (food production, manufacturing, logistics, professional services, healthcare-adjacent, real estate) gets Source 1 only today — adding a registry scraper for any of them is follow-on work, one file in `scrapers/verticals/`, same pattern. |
| Company website signal check | **Built** (`scrapers/company_signals.py`, new) | Fetches the candidate's own public homepage (robots.txt-checked) and reads it for MSP/security-cert/dedicated-IT mentions and an outdated-CMS hint. |
| Email discovery + verification | **Built** (`core/email_finder.py`, reused as-is) | Real SMTP `RCPT TO` probing, never sends mail. |

## Signal scoring

`agents/marketing/thd_lead_scout.score_lead()` is a pure, deterministic
point system (baseline 5, clamped to 1-10) — no LLM call, so scores are
reproducible:

- Target industry match: **+2**
- Employee count in requested range: **+2**
- No MSP/managed-IT mention on their own site: **+2** (mentioned: **-3**)
- Security certification mentioned (SOC 2 / ISO 27001 / HIPAA / PCI-DSS): **-3**
- Dedicated IT role mentioned (IT Manager, CISO, etc.): **-2**
- Outdated CMS generator tag detected: **+1**
- Site unreachable: **+1** (can't disqualify what we can't see — floor, not a strong signal)

Only leads clearing `min_signal_score` (default `THD_MIN_SIGNAL_SCORE=6`,
per-run overridable) are ever written to `thd_consulting_leads` — nothing
below the bar takes up a review click, same principle the MSE Verdict gate
uses.

## Running it

**CLI (no dashboard, no API server needed):**
```bash
python scripts/run_thd_lead_scout.py \
  --industries construction,logistics \
  --locations "Dallas, TX" "Tulsa, OK" \
  --min-score 6 \
  --out leads.csv
```
Writes `leads.csv` always. Also writes to `thd_consulting_leads` if
`SUPABASE_URL`/`SUPABASE_SERVICE_KEY` are set (pass `--skip-supabase` to
force CSV-only even when they are).

**API (dashboard-triggered):**
```
POST /thd-consulting/scrape/find    {"industries": [...], "locations": [...], "min_signal_score": 6}
GET  /thd-consulting/scrape/status  [?run_id=...]   (omit run_id for the most recent run)
GET  /thd-consulting/leads          [?industry=&status=&min_score=&location=&limit=&offset=]
PATCH /thd-consulting/leads/{id}    {"status": "contacted", "notes": "..."}
GET  /thd-consulting/leads/export.csv
```
All require `Authorization: Bearer $MARKETING_API_KEY` — same shared
secret as every other n8n/internal-triggered route in `api/routers/`.

## Adding a new target industry or vertical scraper

1. Add the industry key to `TARGET_INDUSTRIES` in
   `agents/marketing/thd_lead_scout.py`. It works immediately via
   Google Custom Search alone (Source 1), no scraper needed.
2. To add a real public-registry Source 2 for it: write
   `scrapers/verticals/<name>.py` implementing `BaseScraper` (see
   `scrapers/verticals/trades.py` for the template — one state's public
   license lookup is enough to start), then set that industry's value in
   `TARGET_INDUSTRIES` to the vertical name and import/branch it in
   `find_and_score_leads`.

## Adding a new signal indicator

Edit `score_lead()` in `agents/marketing/thd_lead_scout.py` and, if it
needs a new page-level signal, add the corresponding boolean/field to
`scrapers/company_signals.CompanySignals` and `fetch_company_signals()`.
Every scoring change should keep the breakdown dict entries human-readable
— they're stored verbatim in `thd_consulting_leads.signal_breakdown` and
shown on the CEO Decoded dashboard.

## Rate limits and legal usage notes

- Google Custom Search: hard-capped at 100 queries/day (`FREE_TIER_DAILY_CAP`
  in `scrapers/google_search.py`), and that cap is **shared** with every
  MSE product's own lead-finder runs via the same `usage_events` bookkeeping
  — a heavy THD Consulting run on the same day as a scheduled MSE product
  run can starve the other. There's no separate quota; if this becomes a
  real contention problem, the fix is a second Google Cloud project /
  Custom Search Engine ID dedicated to THD Consulting, not a bigger shared cap.
- Every scraped page fetch checks `robots.txt` first and fails closed
  (treats an error as disallowed, not allowed).
- 2-5 second randomized delay between requests (`scrapers/google_search.py`'s
  existing constants).
- No login is ever used, no page behind authentication is ever fetched.
- Raw HTML is never stored — `company_signals.py` extracts booleans/strings
  in the same call and discards the response body.
- No LinkedIn, Indeed, Glassdoor, Yelp, or Clutch scraping, by design — see
  the table above.
