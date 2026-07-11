"""
MKT-S1 SEO Content Factory.

Reads an approved product's research_report.json (MKT-R1's output) and
produces one SEO support blog post: 800 words, answer-first (AEO), with
an embedded FAQPage JSON-LD block, plus a 155-char meta description and
a standalone Article JSON-LD schema block. Mirrors DecodedSix's
DSX-CA1 content standards: no buzzwords, no "AI-powered"/"revolutionary"/
"game-changing", hook answers search intent in the first ~40 words,
ICP language pulled from research_report's pain_language field.

Task spec note: asked for the target query as
"research_report's top_llm_queries[0]" — that field does not exist
anywhere in MKT-R1's real output schema (checked
agents/marketing/mkt_r1_research_core.py directly, and grepped the whole
repo and both marketing spec docs in kdavis-agentic-platform/knowledge/
Marketing/ — it's not defined anywhere). trending_topics[0]['topic'] is
the closest real equivalent: it's the single top-ranked topic MKT-R1's
own synthesis surfaced for this niche, which is what an AEO-targeted
support post should be built around. Used that instead — flagged here
rather than silently guessing a field name that doesn't exist.
"""

import json
from datetime import date, datetime, timezone

import core.llm_router as llm_router
from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

AGENT_ID = "mkt-s1"
TARGET_WORD_COUNT = 800
META_DESCRIPTION_MAX = 155

_SYSTEM_PROMPT = f"""You are MKT-S1, the SEO content factory for a micro-SaaS product marketing engine.
Given a target topic, pain language from real ICP research, and supporting content angles, write ONE
supporting blog post plus its metadata. Return ONLY a single JSON object — no prose, no markdown fences —
matching exactly this schema:

{{
  "title": str,
  "blog_post": str,              // ~{TARGET_WORD_COUNT} words, plain text with \\n\\n paragraph breaks
  "meta_description": str,       // under {META_DESCRIPTION_MAX} characters
  "faq_pairs": [{{"question": str, "answer": str}}]   // at least 3 pairs
}}

Content rules (non-negotiable):
- Answer-first / AEO structure: the opening ~40 words directly answer the target topic's implied
  question before any setup or preamble.
- Use the ICP's own pain language (given below) naturally in the body — their words, not marketing
  paraphrase of their words.
- Never use "AI-powered", "revolutionary", "game-changing", or any other buzzword. Write like a person
  who has actually solved this problem explaining it plainly, not like ad copy.
- Every claim should read as something a practitioner would say, not a vague benefit statement.
- E-E-A-T: write with genuine specificity — concrete numbers, named tools, real scenarios — not generic
  filler that could apply to any product in any niche.
- FAQ section: at least 3 question/answer pairs a real buyer in this ICP would actually ask, each
  answered directly in 1-3 sentences."""


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
        "action": "seo_content_build",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def _target_topic(research_report: dict) -> dict:
    topics = research_report.get("trending_topics") or []
    if not topics:
        raise ValueError("MKT-S1 requires at least one trending_topics entry in research_report")
    return topics[0]


def _truncate_meta(text: str, max_len: int = META_DESCRIPTION_MAX) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rsplit(" ", 1)[0].rstrip(".,;:") + "…"


def _faq_schema(faq_pairs: list[dict]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": pair["question"],
                "acceptedAnswer": {"@type": "Answer", "text": pair["answer"]},
            }
            for pair in faq_pairs
        ],
    }


def _article_schema(title: str, meta_description: str, product_id: str) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "description": meta_description,
        "datePublished": now_iso,
        "dateModified": now_iso,
        "author": {"@type": "Organization", "name": "THD Agentic Systems"},
        "publisher": {"@type": "Organization", "name": "THD Agentic Systems"},
        "about": {"@type": "Thing", "name": product_id},
    }


def run_s1_seo_content_factory(
    research_report: dict,
    product_id: str,
    supabase_client=None,
    anthropic_client=None,
) -> dict:
    """Builds one SEO support blog post from a product's research report.
    Raises on any failure — never fails silently."""
    db = supabase_client if supabase_client is not None else get_supabase()
    cycle_date = research_report.get("cycle_date") or date.today().isoformat()

    _emit_event(db, "seo_content_started", {"product_id": product_id, "cycle_date": cycle_date})

    try:
        target_topic = _target_topic(research_report)
        safe_context = DataSanitizationShield.clean({
            "target_topic": target_topic,
            "pain_language": research_report.get("pain_language") or [],
            "content_angles": research_report.get("content_angles") or [],
            "proof_signals": research_report.get("proof_signals") or [],
        })

        user_prompt = (
            f"Product ID: {product_id}\n"
            f"Target topic (this post's AEO focus): {json.dumps(safe_context['target_topic'])}\n"
            f"ICP pain language (use their real words): {json.dumps(safe_context['pain_language'])}\n"
            f"Supporting content angles: {json.dumps(safe_context['content_angles'])}\n"
            f"Proof signals available to cite: {json.dumps(safe_context['proof_signals'])}\n\n"
            "Write the blog post now, per your system prompt's schema and content rules."
        )
        raw = _analyze(_SYSTEM_PROMPT, user_prompt, anthropic_client=anthropic_client)
        parsed = json.loads(_strip_fences(raw))
        if not isinstance(parsed, dict):
            raise ValueError(f"MKT-S1 expected a JSON object, got {type(parsed).__name__}")

        title = parsed.get("title") or target_topic.get("topic", "")
        blog_body = (parsed.get("blog_post") or "").strip()
        faq_pairs = parsed.get("faq_pairs") or []
        if len(faq_pairs) < 3:
            raise ValueError(f"MKT-S1 requires at least 3 FAQ pairs, got {len(faq_pairs)}")

        meta_description = _truncate_meta(parsed.get("meta_description") or "")
        faq_schema = _faq_schema(faq_pairs)
        faq_schema_tag = f'<script type="application/ld+json">{json.dumps(faq_schema)}</script>'
        blog_post = f"{blog_body}\n\n{faq_schema_tag}"

        schema_json = _article_schema(title, meta_description, product_id)

        word_count = len(blog_body.split())

    except Exception as exc:
        _write_audit(db, "lose", product_id, {"cycle_date": cycle_date, "error": str(exc)})
        raise RuntimeError(f"MKT-S1 SEO content build failed for product {product_id}: {exc}") from exc

    _write_audit(db, "win", product_id, {
        "cycle_date": cycle_date, "word_count": word_count, "faq_count": len(faq_pairs),
    })
    _emit_event(db, "seo_content_completed", {"product_id": product_id, "cycle_date": cycle_date})

    return {
        "product_id": product_id,
        "title": title,
        "blog_post": blog_post,
        "meta_description": meta_description,
        "schema_json": schema_json,
        "word_count": word_count,
    }


def run(research_report: dict, campaign_build: dict) -> dict:
    """
    Adapter for MKT-ORCH's dynamic dispatch — mkt_orch_campaign_orchestrator.py's
    _fire_agent() calls getattr(mod, "run")(research_report=..., campaign_build=...).
    Updates campaign_builds.seo_factory_status, matching MKT-O1's adapter pattern.
    """
    db = get_supabase()
    product_id = campaign_build["product_id"]
    try:
        result = run_s1_seo_content_factory(research_report, product_id, supabase_client=db)
    except Exception:
        db.table("campaign_builds").update({"seo_factory_status": "failed"}).eq("id", campaign_build["id"]).execute()
        raise
    db.table("campaign_builds").update({"seo_factory_status": "complete"}).eq("id", campaign_build["id"]).execute()
    return result
