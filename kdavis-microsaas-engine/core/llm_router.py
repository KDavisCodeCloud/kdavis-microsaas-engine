from anthropic import Anthropic

_client = Anthropic()

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"


def scrape(system: str, user: str, max_tokens: int = 2048) -> str:
    """High-volume scraping and raw data ingestion — Haiku."""
    msg = _client.messages.create(
        model=HAIKU,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text


def analyze(system: str, user: str, max_tokens: int = 4096) -> str:
    """Structured analysis, JSON extraction, scoring — Sonnet."""
    msg = _client.messages.create(
        model=SONNET,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text


def analyze_with_web_search(system: str, user: str, max_uses: int = 15, max_tokens: int = 16000) -> str:
    """
    Analysis backed by live web search — Sonnet + Anthropic's server-side
    web_search tool (Anthropic runs the search itself; nothing to execute
    client-side). For research that must verify CURRENT competitor/pricing/
    market data rather than recall training data — two named failure modes
    in the Verdict v2.0 audit were exactly "asserted no competitor exists
    without searching" and "stale pricing pulled from memory". Requires
    anthropic>=0.40 (web_search_20250305 tool) and web search enabled for
    the org in the Claude Console (Settings > Privacy) — a disabled org
    setting fails the whole call with a 400, not a graceful per-search
    error. Costs $10/1,000 searches on top of normal token cost.

    Returns the full assembled text: all "text" content blocks concatenated
    in order (the model narrates its reasoning between searches, per
    Anthropic's documented interleaving) — callers needing structured
    output should end their prompt with an instruction to emit one final
    JSON object, then parse the trailing JSON out of the returned text.
    """
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}]
    messages: list[dict] = [{"role": "user", "content": user}]

    msg = _client.messages.create(model=SONNET, max_tokens=max_tokens, system=system, messages=messages, tools=tools)

    # A long research turn can pause mid-flight (stop_reason "pause_turn").
    # Per Anthropic's docs, resubmit the paused assistant content unchanged
    # to let the API continue it. Capped so a pathological case can't loop
    # forever and silently rack up search cost.
    retries = 0
    while msg.stop_reason == "pause_turn" and retries < 3:
        messages.append({"role": "assistant", "content": msg.content})
        msg = _client.messages.create(model=SONNET, max_tokens=max_tokens, system=system, messages=messages, tools=tools)
        retries += 1

    return "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
