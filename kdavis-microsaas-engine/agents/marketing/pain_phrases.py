"""
agents/marketing/pain_phrases.py

Pain-phrase intent boost (Kelvin, 2026-10-18).

A job description that says "ARM to Bicep" or "landing zone" names the exact
problem the consulting offer solves. That is a stronger buying signal than the
role title alone, so a match raises intent_score, and the matched phrase plus
the sentence it came from are stored on the lead for MKT-O2 to quote.

The phrase list is DATA, not code: mse_icp_configs.pain_phrases, with the
boost size in mse_icp_configs.pain_phrase_boost. Adding a phrase is a row
edit.

Matching is literal, not semantic. "tech debt" does not match "technical
debt" unless that variant is added to the list -- a fuzzy match here would put
words in the prospect's mouth that their posting never used, and the whole
point of storing the snippet is that touch 1 can quote them exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

DEFAULT_PER_MATCH = 0.10
DEFAULT_MAX_BOOST = 0.20
SNIPPET_MAX = 220

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _phrase_pattern(phrase: str) -> Optional[re.Pattern]:
    """Case-insensitive, whitespace- and hyphen-tolerant, anchored on word
    edges. "SOC 2 readiness" also matches "SOC2 readiness" and
    "SOC-2 readiness"; "Key Vault" matches "key-vault" but not "keyvaults"."""
    words = _WORD_RE.findall(phrase or "")
    if not words:
        return None
    parts = [re.escape(words[0])]
    for prev, word in zip(words, words[1:]):
        # Between a letter run and a digit run ("SOC 2") the separator is
        # optional; between two words it is required, or "tech debt" would
        # match "techdebt" inside a URL slug.
        joiner = r"[\s\-]*" if (prev[-1].isdigit() != word[0].isdigit()) else r"[\s\-]+"
        parts.append(joiner + re.escape(word))
    return re.compile(r"(?<![A-Za-z0-9])" + "".join(parts) + r"(?![A-Za-z0-9])", re.IGNORECASE)


def _snippet(text: str, start: int, end: int) -> str:
    """The sentence around a match, verbatim, capped at SNIPPET_MAX."""
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start),
               text.rfind("!", 0, start), text.rfind("?", 0, start))
    left = left + 1 if left >= 0 and start - left < 200 else max(0, start - 100)
    stops = [i for i in (text.find(c, end) for c in ".\n!?") if i != -1]
    right = min(stops) + 1 if stops and min(stops) - end < 200 else min(len(text), end + 100)
    out = " ".join(text[left:right].split())
    return out[:SNIPPET_MAX].rstrip()


@dataclass
class PainMatch:
    phrases: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.phrases)


def match_pain_phrases(text: Optional[str], phrases: Iterable[str]) -> PainMatch:
    """Every configured phrase found in `text`, in list order, with the
    sentence it was found in."""
    result = PainMatch()
    if not text:
        return result
    for phrase in phrases or []:
        if not isinstance(phrase, str) or not phrase.strip():
            continue
        pattern = _phrase_pattern(phrase)
        if pattern is None:
            continue
        m = pattern.search(text)
        if not m:
            continue
        result.phrases.append(phrase.strip())
        result.evidence.append({"phrase": phrase.strip(), "snippet": _snippet(text, m.start(), m.end())})
    return result


@dataclass(frozen=True)
class PainConfig:
    phrases: tuple[str, ...] = ()
    per_match: float = DEFAULT_PER_MATCH
    max_boost: float = DEFAULT_MAX_BOOST

    @classmethod
    def from_icp(cls, icp: Optional[dict]) -> "PainConfig":
        icp = icp or {}
        phrases = tuple(p for p in (icp.get("pain_phrases") or []) if isinstance(p, str) and p.strip())
        boost = icp.get("pain_phrase_boost") or {}
        try:
            per = float(boost.get("per_match", DEFAULT_PER_MATCH))
            cap = float(boost.get("max", DEFAULT_MAX_BOOST))
        except (TypeError, ValueError, AttributeError):
            per, cap = DEFAULT_PER_MATCH, DEFAULT_MAX_BOOST
        return cls(phrases=phrases, per_match=max(0.0, per), max_boost=max(0.0, cap))


def boost_intent(intent: float, match: PainMatch, config: PainConfig) -> tuple[float, Optional[str]]:
    """(new intent, reason or None). Capped at 1.0 like every other score."""
    if not match:
        return intent, None
    boost = min(config.max_boost, config.per_match * len(match.phrases))
    new = round(min(1.0, (intent or 0.0) + boost), 3)
    return new, f"pain phrases in JD (+{boost:.2f}): {', '.join(match.phrases)}"
