import re
import html
from typing import Any


_INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"disregard\s+(all\s+)?prior",
    r"you\s+are\s+now\s+a",
    r"act\s+as\s+(a\s+)?different",
    r"new\s+instructions?:",
    r"system\s+prompt\s*:",
    r"<\|.*?\|>",
    r"\[\[.*?inject.*?\]\]",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE | re.DOTALL)

MAX_TEXT_LENGTH = 32_000


class DataSanitizationShield:
    """
    Must be called before every LLM call that processes external or user-supplied data.
    Catches prompt injection, strips HTML, and enforces a length cap.
    """

    @staticmethod
    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return DataSanitizationShield._clean_str(value)
        if isinstance(value, dict):
            return {k: DataSanitizationShield.clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [DataSanitizationShield.clean(item) for item in value]
        return value

    @staticmethod
    def _clean_str(text: str) -> str:
        text = html.unescape(text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = text[:MAX_TEXT_LENGTH]
        if _INJECTION_RE.search(text):
            raise ValueError("Potential prompt injection detected — input rejected")
        return text.strip()
