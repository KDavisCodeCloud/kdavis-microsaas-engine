"""
MKT-O1 Apollo List Builder.

Reads an approved product's research_report, derives Apollo.io search
parameters from the ICP (Sonnet), pulls up to 100 verified leads via
Apollo's People Search API, and stores them for MKT-O2 to write DMs
against. Skips gracefully (never raises) when APOLLO_API_KEY isn't set —
this agent needs a real, paid Apollo account to do anything.

Output lands in mse_apollo_leads (see
supabase/migrations/20260709000006_outreach_engine.sql) and
campaign_builds.apollo_status is updated to reflect the outcome.

NOTE: the Apollo request shape below (POST /v1/mixed_people/search,
X-Api-Key header, organization_num_employees_ranges) matches their
documented v1 API as of this writing, but hasn't been exercised against a
live key — verify against current Apollo docs once APOLLO_API_KEY is set.
"""

import json
import os
from typing import Any, Optional

import httpx

import core.llm_router as llm_router
from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

AGENT_ID = "mkt-o1"
APOLLO_SEARCH_URL = "https://api.apollo.io/v1/mixed_people/search"
MAX_LEADS = 100

_SYSTEM_PROMPT = """You are MKT-O1, deriving Apollo.io People Search parameters from a
product's ICP research. Return ONLY a single JSON object — no prose, no markdown fences —
matching exactly this schema:

{
  "titles": [str],            // job titles matching the ICP, most senior-relevant first
  "keywords": [str],          // pain-language-derived keywords for person/company search
  "company_size_range": str   // one of: "1,10" | "11,50" | "51,200" | "201,500" | "501,1000" | "1001,5000" | "5001,10000" | "10001,"
}

company_size_range must be derived from willingness_to_pay_band: lower WTP bands imply
smaller companies (solo/small team buyers), higher WTP bands imply larger companies with
bigger budgets. Pick the single most likely range, not a list."""


def _analyze(system: str, user: str, anthropic_client=None, max_tokens: int = 1024) -> str:
    if anthropic_client is None:
        return llm_router.analyze(system, user, max_tokens=max_tokens)
    msg = anthropic_client.messages.create(
        model=llm_router.SONNET, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()
    return raw


def _emit_event(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": event_type,
        "metadata": metadata,
    }).execute()


def _write_audit(db, outcome: str, product_id: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "apollo_list_build",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def _derive_search_params(research_report: dict, anthropic_client=None) -> dict:
    safe_report = DataSanitizationShield.clean({
        "icp_channels": research_report.get("icp_channels", []),
        "pain_language": research_report.get("pain_language", []),
        "willingness_to_pay_band": research_report.get("willingness_to_pay_band", ""),
    })
    user_prompt = f"ICP research:\n{json.dumps(safe_report, indent=2)}\n\nDerive the Apollo search parameters now."
    raw = _analyze(_SYSTEM_PROMPT, user_prompt, anthropic_client=anthropic_client)
    parsed = json.loads(_strip_fences(raw))
    if not isinstance(parsed, dict):
        raise ValueError(f"MKT-O1 expected a JSON object for search params, got {type(parsed).__name__}")
    return {
        "titles": parsed.get("titles") or [],
        "keywords": parsed.get("keywords") or [],
        "company_size_range": parsed.get("company_size_range") or "",
    }


def _search_apollo(search_params: dict, apollo_api_key: str) -> list[dict]:
    payload: dict[str, Any] = {
        "person_titles": search_params["titles"],
        "q_keywords": " ".join(search_params["keywords"]),
        "per_page": MAX_LEADS,
    }
    if search_params.get("company_size_range"):
        payload["organization_num_employees_ranges"] = [search_params["company_size_range"]]

    response = httpx.post(
        APOLLO_SEARCH_URL,
        headers={"X-Api-Key": apollo_api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return (data.get("people") or [])[:MAX_LEADS]


def _leads_to_rows(people: list[dict], campaign_build_id: str, product_id: str) -> list[dict]:
    rows = []
    for person in people:
        organization = person.get("organization") or {}
        rows.append({
            "campaign_build_id": campaign_build_id,
            "product_id": product_id,
            "first_name": person.get("first_name"),
            "last_name": person.get("last_name"),
            "email": person.get("email"),
            "company": organization.get("name"),
            "title": person.get("title"),
            "linkedin_url": person.get("linkedin_url"),
            "apollo_id": person.get("id"),
        })
    return rows


def run_o1_apollo_list_builder(
    product_id: str,
    research_report: dict,
    campaign_build_id: str,
    apollo_api_key: Optional[str] = None,
    supabase_client: Optional[Any] = None,
) -> dict:
    """
    Builds a targeted Apollo.io lead list for an approved product's campaign.

    Returns {status: "complete", leads_count} on success, or
    {"status": "skipped", "reason": "no_api_key"} if APOLLO_API_KEY isn't
    configured — this agent never raises for a missing key, since that's an
    expected, recoverable state (Apollo is a paid third-party account, not
    always provisioned). Raises RuntimeError for any other failure — never
    fails silently.
    """
    db = supabase_client if supabase_client is not None else get_supabase()
    api_key = apollo_api_key or os.getenv("APOLLO_API_KEY")

    if not api_key:
        print("APOLLO_API_KEY not set — mkt_o1 skipped")
        _write_audit(db, "lose", product_id, {
            "campaign_build_id": campaign_build_id, "reason": "no_api_key",
        })
        return {"status": "skipped", "reason": "no_api_key"}

    _emit_event(db, "apollo_list_build_started", {
        "product_id": product_id, "campaign_build_id": campaign_build_id,
    })

    try:
        search_params = _derive_search_params(research_report)
        people = _search_apollo(search_params, api_key)
        rows = _leads_to_rows(people, campaign_build_id, product_id)

        if rows:
            insert_result = db.table("mse_apollo_leads").insert(rows).execute()
            if not insert_result.data:
                raise RuntimeError("Insert into mse_apollo_leads returned no data")

        db.table("campaign_builds").update({"apollo_status": "complete"}).eq("id", campaign_build_id).execute()

    except Exception as exc:
        _write_audit(db, "lose", product_id, {
            "campaign_build_id": campaign_build_id, "error": str(exc),
        })
        db.table("campaign_builds").update({"apollo_status": "failed"}).eq("id", campaign_build_id).execute()
        raise RuntimeError(f"MKT-O1 Apollo list build failed for product {product_id}: {exc}") from exc

    _write_audit(db, "win", product_id, {
        "campaign_build_id": campaign_build_id, "leads_count": len(rows),
    })
    _emit_event(db, "apollo_list_build_completed", {
        "product_id": product_id, "campaign_build_id": campaign_build_id, "leads_count": len(rows),
    })

    return {"status": "complete", "leads_count": len(rows)}


def run(research_report: dict, campaign_build: dict) -> dict:
    """
    Adapter for MKT-ORCH's dynamic dispatch — agents/marketing/mkt_orch_campaign_orchestrator.py's
    _fire_agent() calls getattr(mod, "run")(research_report=..., campaign_build=...).
    """
    return run_o1_apollo_list_builder(
        product_id=campaign_build["product_id"],
        research_report=research_report,
        campaign_build_id=campaign_build["id"],
    )
