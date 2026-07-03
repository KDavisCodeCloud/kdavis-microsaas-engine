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
