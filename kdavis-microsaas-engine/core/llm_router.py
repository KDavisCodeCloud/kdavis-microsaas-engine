import logging

from anthropic import Anthropic

log = logging.getLogger(__name__)

_client = Anthropic()

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

# Approximate published per-million-token pricing, used only for the cost
# logging below -- not billed against, just a visibility aid so real cost
# drivers are visible in logs instead of guessed at after the fact.
_PRICE_PER_M = {
    HAIKU:  {"input": 0.80, "output": 4.00},
    SONNET: {"input": 3.00, "output": 15.00},
}


def _log_usage(label: str, model: str, usage) -> None:
    prices = _PRICE_PER_M.get(model)
    cost_str = ""
    if prices and usage is not None:
        cost = (usage.input_tokens / 1_000_000) * prices["input"] + (usage.output_tokens / 1_000_000) * prices["output"]
        cost_str = f" ~${cost:.4f}"
    if usage is not None:
        log.info(
            "[llm_router] %s model=%s input_tokens=%d output_tokens=%d%s",
            label, model, usage.input_tokens, usage.output_tokens, cost_str,
        )


def scrape(system: str, user: str, max_tokens: int = 2048) -> str:
    """High-volume scraping and raw data ingestion — Haiku."""
    msg = _client.messages.create(
        model=HAIKU,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    _log_usage("scrape", HAIKU, msg.usage)
    return msg.content[0].text


def analyze(system: str, user: str, max_tokens: int = 4096, model: str = SONNET) -> str:
    """
    Structured analysis, JSON extraction, scoring. Sonnet by default (other
    callers of this shared helper — brief_generator, naming, digest_generator,
    ceo routes — still want Sonnet-level quality and are unaffected); pass
    model=HAIKU explicitly for high-volume, well-structured tasks like MSE's
    Dispatch, where the output format is fixed and the judgment calls are
    narrow enough that Haiku handles them reliably (2026-07-19 cost pass).

    System prompt is cache-eligible (cache_control: ephemeral) — MSE's
    Dispatch/Verdict prompts are large and mostly static across every call
    in a batch, so repeated calls in the same ~5 minute window hit a cache
    read (10% of normal input-token price) instead of paying full price for
    the whole prompt every single time.
    """
    msg = _client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    _log_usage("analyze", model, msg.usage)
    return msg.content[0].text


def analyze_with_web_search(
    system: str,
    user: str,
    max_uses: int = 15,
    max_tokens: int = 8000,
    model: str = SONNET,
) -> str:
    """
    Analysis backed by live web search — Anthropic's server-side web_search
    tool (Anthropic runs the search itself; nothing to execute client-side).
    For research that must verify CURRENT competitor/pricing/market data
    rather than recall training data — two named failure modes in the
    Verdict v2.0 audit were exactly "asserted no competitor exists without
    searching" and "stale pricing pulled from memory". Requires
    anthropic>=0.40 (web_search_20250305 tool) and web search enabled for
    the org in the Claude Console (Settings > Privacy) — a disabled org
    setting fails the whole call with a 400, not a graceful per-search
    error. Costs $10/1,000 searches on top of normal token cost.

    Sonnet by default; MSE's aggregator/orchestrator pass model=HAIKU
    (2026-07-19 cost pass) — verify with real regression runs before
    trusting Haiku's research quality on a new prompt, same as any model
    swap on a live-search-backed agent.

    max_tokens default lowered from 16000 to 8000 as part of the same cost
    pass, NOT down to the 2000-3000 range suggested elsewhere — a real
    incident this session already truncated a Verdict call mid-narration
    at 8192 tokens before it ever reached its closing JSON contract, and
    v5.0's prompt is if anything longer now (added a mandatory confidence-
    score step). 8000 is a real reduction from 16000, not an unverified
    guess; treat as a starting point to re-tighten only after confirming
    real runs aren't hitting it.

    System prompt is cache-eligible (cache_control: ephemeral), same as
    analyze() above.

    Returns the full assembled text: all "text" content blocks concatenated
    in order (the model narrates its reasoning between searches, per
    Anthropic's documented interleaving) — callers needing structured
    output should end their prompt with an instruction to emit one final
    JSON object, then parse the trailing JSON out of the returned text.
    """
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}]
    messages: list[dict] = [{"role": "user", "content": user}]
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    msg = _client.messages.create(model=model, max_tokens=max_tokens, system=system_blocks, messages=messages, tools=tools)

    # A long research turn can pause mid-flight (stop_reason "pause_turn").
    # Per Anthropic's docs, resubmit the paused assistant content unchanged
    # to let the API continue it. Capped so a pathological case can't loop
    # forever and silently rack up search cost.
    retries = 0
    while msg.stop_reason == "pause_turn" and retries < 3:
        messages.append({"role": "assistant", "content": msg.content})
        msg = _client.messages.create(model=model, max_tokens=max_tokens, system=system_blocks, messages=messages, tools=tools)
        retries += 1

    _log_usage("analyze_with_web_search", model, msg.usage)
    return "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
