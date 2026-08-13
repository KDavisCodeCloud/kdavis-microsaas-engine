"""
MKT-V1 Content Multiplier.

Reads an approved product's research_report.json (MKT-R1's output) and
writes one value-first community post per platform the ICP is actually
in — Reddit and/or Facebook groups, per research_report's icp_channels
(mirrors MKT-ORCH's own select_channels() mapping: "reddit" -> reddit,
"facebook_groups" -> facebook). Only fires when MKT-ORCH's gate matched
at least one of those two channels (see mkt_orch_campaign_orchestrator.py's
_DOWNSTREAM_AGENTS entry for mkt-v1), so this module never has to handle
"neither channel applies" itself.

Reddit and Facebook groups both actively police and remove posts that
read as an ad — the whole point of "multiplying" content onto these
channels is a genuine, non-salesy community post built from the same
pain_language/content_angles research grounds every other MSE content
agent uses, not a repost of marketing copy. Mirrors MKT-S1's content
rules (no buzzwords, ICP's own words, real specificity) with platform-
native shape: Reddit posts get a title, Facebook group posts don't.
"""

import json
from datetime import date

import core.llm_router as llm_router
from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

AGENT_ID = "mkt-v1"

_PLATFORM_BY_ICP_CHANNEL = {
    "reddit": "reddit",
    "facebook_groups": "facebook",
}

_SYSTEM_PROMPT = """You are MKT-V1, the content multiplier for a micro-SaaS product marketing engine.
Given pain language and content angles from real ICP research, and a list of target platforms, write
ONE genuine community post per platform. Return ONLY a single JSON object — no prose, no markdown
fences — matching exactly this schema:

{
  "posts": [
    {"platform": "reddit" | "facebook", "title": str or null, "body": str}
  ]
}

One post per requested platform, in the order requested.

Content rules (non-negotiable):
- These are community posts, not ads. Reddit and Facebook groups both remove posts that read as
  self-promotion. Write like a practitioner sharing a genuine lesson learned or asking a real
  question — the product, if mentioned at all, is a passing, natural mention, never the point of
  the post.
- Use the ICP's own pain language (given below) naturally in the body — their words, not marketing
  paraphrase of their words.
- Never use "AI-powered", "revolutionary", "game-changing", or any other buzzword.
- Reddit posts: include a plain, non-clickbait "title" (how a real subreddit post is titled), body
  150-400 words.
- Facebook group posts: "title" is null — group posts are a single body, conversational, 80-200 words,
  written the way a real person posts into a group feed.
- Every claim should read as something a practitioner would say, not a vague benefit statement."""


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
        "action": "social_content_build",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def _target_platforms(research_report: dict) -> list[str]:
    icp_channels = research_report.get("icp_channels") or []
    platforms = [
        platform for channel, platform in _PLATFORM_BY_ICP_CHANNEL.items()
        if channel in icp_channels
    ]
    if not platforms:
        raise ValueError(
            "MKT-V1 requires icp_channels to include 'reddit' and/or 'facebook_groups'"
        )
    return platforms


def _persist(db, product_id: str, campaign_build_id, post: dict) -> str:
    row = {
        "product_id": product_id,
        "campaign_build_id": campaign_build_id,
        "platform": post["platform"],
        "title": post["title"],
        "body": post["body"],
    }
    inserted = db.table("mse_social_content").insert(row).execute()
    return inserted.data[0]["id"]


def run_v1_content_multiplier(
    research_report: dict,
    product_id: str,
    campaign_build_id,
    supabase_client=None,
    anthropic_client=None,
) -> dict:
    """Writes one community post per applicable platform (Reddit and/or
    Facebook groups) from a product's research report. Raises on any
    failure — never fails silently."""
    db = supabase_client if supabase_client is not None else get_supabase()
    cycle_date = research_report.get("cycle_date") or date.today().isoformat()

    _emit_event(db, "social_content_started", {"product_id": product_id, "cycle_date": cycle_date})

    try:
        platforms = _target_platforms(research_report)
        safe_context = DataSanitizationShield.clean({
            "pain_language": research_report.get("pain_language") or [],
            "content_angles": research_report.get("content_angles") or [],
            "proof_signals": research_report.get("proof_signals") or [],
        })

        user_prompt = (
            f"Product ID: {product_id}\n"
            f"Target platforms (one post per platform, in this order): {json.dumps(platforms)}\n"
            f"ICP pain language (use their real words): {json.dumps(safe_context['pain_language'])}\n"
            f"Supporting content angles: {json.dumps(safe_context['content_angles'])}\n"
            f"Proof signals available to cite: {json.dumps(safe_context['proof_signals'])}\n\n"
            "Write the posts now, per your system prompt's schema and content rules."
        )
        raw = _analyze(_SYSTEM_PROMPT, user_prompt, anthropic_client=anthropic_client)
        parsed = json.loads(_strip_fences(raw))
        if not isinstance(parsed, dict):
            raise ValueError(f"MKT-V1 expected a JSON object, got {type(parsed).__name__}")

        posts = parsed.get("posts") or []
        if len(posts) != len(platforms):
            raise ValueError(f"MKT-V1 expected {len(platforms)} post(s), got {len(posts)}")

        for platform, post in zip(platforms, posts):
            if post.get("platform") != platform:
                raise ValueError(
                    f"MKT-V1 post order mismatch — expected platform {platform!r}, got {post.get('platform')!r}"
                )
            if not (post.get("body") or "").strip():
                raise ValueError(f"MKT-V1 got an empty body for platform {platform!r}")

    except Exception as exc:
        _write_audit(db, "lose", product_id, {"cycle_date": cycle_date, "error": str(exc)})
        raise RuntimeError(f"MKT-V1 content multiplier failed for product {product_id}: {exc}") from exc

    content_ids = []
    try:
        for post in posts:
            content_ids.append(_persist(db, product_id, campaign_build_id, post))
    except Exception as exc:
        _write_audit(db, "lose", product_id, {"cycle_date": cycle_date, "error": f"persist failed: {exc}"})
        raise RuntimeError(f"MKT-V1 succeeded but failed to persist output for product {product_id}: {exc}") from exc

    _write_audit(db, "win", product_id, {
        "cycle_date": cycle_date, "platforms": platforms, "content_ids": content_ids,
    })
    _emit_event(db, "social_content_completed", {
        "product_id": product_id, "cycle_date": cycle_date, "content_ids": content_ids,
    })

    return {"product_id": product_id, "platforms": platforms, "posts": posts, "ids": content_ids}


def run(research_report: dict, campaign_build: dict) -> dict:
    """
    Adapter for MKT-ORCH's dynamic dispatch — mkt_orch_campaign_orchestrator.py's
    _fire_agent() calls getattr(mod, "run")(research_report=..., campaign_build=...).
    Updates campaign_builds.social_status, matching MKT-S1's adapter pattern.
    """
    db = get_supabase()
    product_id = campaign_build["product_id"]
    try:
        result = run_v1_content_multiplier(
            research_report, product_id, campaign_build["id"], supabase_client=db,
        )
    except Exception:
        db.table("campaign_builds").update({"social_status": "failed"}).eq("id", campaign_build["id"]).execute()
        raise
    db.table("campaign_builds").update({"social_status": "complete"}).eq("id", campaign_build["id"]).execute()
    return result
