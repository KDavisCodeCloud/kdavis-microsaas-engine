"""
MKT-R1 Research Core.

Runs once per product per cycle (weekly). Scrapes the product's niche
(Reddit, competitor URLs, news, review platforms) and produces one
structured research_report.json — the single source of truth every
downstream marketing agent (MKT-N1, MKT-V1, MKT-O1..O3, MKT-S1, ...)
reads from. No downstream agent does its own research.

No real HTTP scraping client exists in this repo yet (requirements.txt
has no requests/httpx/bs4) — matching agents/orchestrator/agent.py's own
established pattern (_run_one_vertical's "Stub: Sonnet performs the
research directly" fallback), source gathering and analysis both go
through the LLM directly: Haiku reasons over the niche/source config to
produce raw candidate findings, Sonnet structures those into the exact
schema below. Swap in a real scraper later without changing this
module's public signature.

Output schema (research_report.json / mse_research_reports.report_json):
    {
      "product_id": str,
      "cycle_date": str (ISO date),
      "trending_topics": [{"topic", "why_it_matters", "source_urls"}],
      "pain_language": [{"phrase", "context", "source", "frequency"}],
      "competitor_moves": [{"competitor", "action", "source_url"}],
      "content_angles": [{"angle", "supporting_data"}],
      "proof_signals": [{"signal", "source"}],
      "icp_channels": list[str],
      "willingness_to_pay_band": str,
      "wtp_evidence": list[str],
      "suggested_price": int
    }
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

import core.llm_router as llm_router
from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

AGENT_ID = "mkt-r1"
OUTPUT_ROOT = Path("/mse/marketing/outputs")

_REQUIRED_LIST_FIELDS = [
    "trending_topics", "pain_language", "competitor_moves",
    "content_angles", "proof_signals", "icp_channels", "wtp_evidence",
]

_SYSTEM_PROMPT = """You are MKT-R1, the marketing research core for a micro-SaaS product factory.
Given a product's niche keywords and source configuration, produce ONE structured research report.
Return ONLY a single JSON object — no prose, no markdown fences — matching exactly this schema:

{
  "trending_topics": [{"topic": str, "why_it_matters": str, "source_urls": [str]}],
  "pain_language": [{"phrase": str, "context": str, "source": str, "frequency": int}],
  "competitor_moves": [{"competitor": str, "action": str, "source_url": str}],
  "content_angles": [{"angle": str, "supporting_data": str}],
  "proof_signals": [{"signal": str, "source": str}],
  "icp_channels": [str],
  "willingness_to_pay_band": str,
  "wtp_evidence": [str],
  "suggested_price": int
}

icp_channels values are drawn from where the ICP actually spends time — use values like
"linkedin", "reddit", "facebook_groups", "seo", "email" as applicable; this feeds
MKT-ORCH's channel selection downstream, so be precise about where this audience really is."""


def _scrape(system: str, user: str, anthropic_client=None, max_tokens: int = 4096) -> str:
    if anthropic_client is None:
        return llm_router.scrape(system, user, max_tokens=max_tokens)
    msg = anthropic_client.messages.create(
        model=llm_router.HAIKU, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text


def _analyze(system: str, user: str, anthropic_client=None, max_tokens: int = 4096) -> str:
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
        "action": "research_core_run",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def run_research_core(
    product_id: str,
    niche_keywords: list[str],
    source_config: dict[str, Any],
    supabase_client=None,
    anthropic_client=None,
) -> dict:
    """Runs one full research cycle for a single product. Raises on any
    failure — never fails silently. Returns the research_report dict."""
    db = supabase_client if supabase_client is not None else get_supabase()
    cycle_date = date.today().isoformat()

    _emit_event(db, "research_core_started", {"product_id": product_id, "cycle_date": cycle_date})

    try:
        safe_keywords = DataSanitizationShield.clean(niche_keywords)
        safe_source_config = DataSanitizationShield.clean(source_config)

        scrape_user_prompt = (
            f"Niche keywords: {json.dumps(safe_keywords)}\n"
            f"Source config: {json.dumps(safe_source_config)}\n\n"
            "List the raw signal you'd expect to find across these sources for this niche: "
            "trending discussion topics, verbatim pain-language phrases, recent competitor "
            "moves, and pricing/willingness-to-pay signals. Be concrete and specific to this "
            "niche, not generic. Plain text notes, not JSON — the next step structures this."
        )
        raw_signal = _scrape(
            "You are a research scraper. Surface concrete, niche-specific raw signal from the "
            "described sources. No fabricated URLs you wouldn't expect to be real for this niche.",
            scrape_user_prompt,
            anthropic_client=anthropic_client,
        )

        analyze_user_prompt = (
            f"Product ID: {product_id}\n"
            f"Niche keywords: {json.dumps(safe_keywords)}\n"
            f"Source config: {json.dumps(safe_source_config)}\n\n"
            f"Raw research signal:\n{raw_signal}\n\n"
            "Structure this into the exact JSON schema from your system prompt. Return only the JSON object."
        )
        raw_report = _analyze(_SYSTEM_PROMPT, analyze_user_prompt, anthropic_client=anthropic_client)

        parsed = json.loads(_strip_fences(raw_report))
        if not isinstance(parsed, dict):
            raise ValueError(f"MKT-R1 expected a JSON object, got {type(parsed).__name__}")

        report = {
            "product_id": product_id,
            "cycle_date": cycle_date,
            **{field: parsed.get(field) or [] for field in _REQUIRED_LIST_FIELDS},
            "willingness_to_pay_band": parsed.get("willingness_to_pay_band", ""),
            "suggested_price": int(parsed.get("suggested_price") or 0),
        }

        output_dir = OUTPUT_ROOT / product_id / cycle_date
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "research_report.json").write_text(json.dumps(report, indent=2))

        db.table("mse_research_reports").upsert(
            {"product_id": product_id, "cycle_date": cycle_date, "report_json": report},
            on_conflict="product_id,cycle_date",
        ).execute()

    except Exception as exc:
        _write_audit(db, "lose", product_id, {"cycle_date": cycle_date, "error": str(exc)})
        raise RuntimeError(f"MKT-R1 research core failed for product {product_id}: {exc}") from exc

    _write_audit(db, "win", product_id, {"cycle_date": cycle_date})
    _emit_event(db, "research_core_completed", {"product_id": product_id, "cycle_date": cycle_date})

    return report
