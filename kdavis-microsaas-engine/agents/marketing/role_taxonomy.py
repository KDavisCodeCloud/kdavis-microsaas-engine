"""
agents/marketing/role_taxonomy.py

Role matching for scraper v2 (Kelvin's decision 2, 2026-10-01).

WHY. The first live runs matched 37 of 2,197 postings (1.7%) for consulting
and 22 of 2,204 (1.0%) for Cloud Decoded, because matching was done against
five literal phrases ("platform engineer", "devops engineer", ...). Real ATS
titles are far more varied than that: "Staff Site Reliability Engineer II",
"Senior DevSecOps Engineer", "Principal K8s Platform Architect", "Build &
Release Engineer". Target after this change: 5-8%.

Three lists, loaded from mse_icp_configs.role_taxonomy and falling back to
the defaults here:

  include       -- role FAMILIES, matched as word-boundary patterns rather
                   than exact phrases, so seniority prefixes and suffixes
                   don't defeat the match.
  negative      -- titles that are infrastructure-adjacent but belong to a
                   different function entirely. "Solutions Engineer" and
                   "Sales Engineer" are the headline cases: they read as
                   technical and are not the buyer.
  intent_boost  -- title markers that signal a company building a function
                   from scratch ("founding", "first", "lead", "head of"),
                   which is when outside help is most wanted.

Matching is on the TITLE FIELD ONLY, word-boundary anchored -- the same
rule as agents/marketing/lead_qualification.py, and for the same reason.
Matching the description instead turns "we use Terraform to manage our
sales CRM" into a platform-engineering signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# ── Defaults ─────────────────────────────────────────────────────────────
#
# Each entry is a family NAME mapped to the regex that recognises it. The
# name is what gets reported in funnel stats and score reasons.

# Widened 2026-10-01 against 1,774 real ATS postings: the first pass hit
# 4.51% and the titles it missed were sampled rather than guessed at. The
# `(?:\w+\s+){0,2}` allowances exist because real titles interleave words
# the first version did not expect -- "Cloud Senior Engineer", "Cloud
# Software Architect", "Senior Data Architect - Databricks Platform".
# Includes the senior/ownership nouns, not just the IC ones: "Head of
# Platform" and "VP, Infrastructure" are the buyer titles this whole
# pipeline is aimed at, and the first draft of this list omitted them --
# so "Head of Platform" did not match at all.
_ROLE_NOUN = (r"(?:engineer|engineering|architect|developer|lead|manager|director"
              r"|administrator|admin|operations|head|owner|vp|svp|chief)")

DEFAULT_INCLUDE: dict[str, str] = {
    "devops": r"\bdev[\s-]?ops\b",
    "devsecops": r"\bdev[\s-]?sec[\s-]?ops\b",
    # "Software Engineer - Reliability" is an SRE role by another name.
    "sre": r"\bSRE\b|\bsite\s+reliability\b|\breliability\s+engineer(?:ing)?\b|\bengineer\b[^,]*\breliability\b|\breliability\b[^,]*\bengineer\b",
    "platform": (
        rf"\bplatform(?:s)?\s+(?:\w+\s+){{0,2}}{_ROLE_NOUN}\b"
        rf"|\b{_ROLE_NOUN}\s*[-–,]?\s*(?:\w+\s+){{0,2}}platform(?:s)?\b"
        r"|\b(?:data|cloud|infra(?:structure)?|internal|developer)\s+platform(?:s)?\b"
    ),
    "infrastructure": rf"\binfra(?:structure)?\s+(?:\w+\s+){{0,2}}{_ROLE_NOUN}\b|\b{_ROLE_NOUN}\s*[-–,]?\s*(?:\w+\s+){{0,2}}infra(?:structure)?\b",
    "cloud": rf"\bcloud\s+(?:\w+\s+){{0,2}}{_ROLE_NOUN}\b|\b{_ROLE_NOUN}\s*[-–,]?\s*(?:\w+\s+){{0,2}}cloud\b",
    "kubernetes": r"\bkubernetes\b|\bk8s\b",
    "build_release": r"\b(?:build|release)\s*(?:&|and|/)?\s*(?:release|engineering)?\s+engineer\b|\brelease\s+engineer(?:ing)?\b|\bbuild\s+engineer\b",
    # Systems engineering/administration is the same buyer in a smaller org.
    # "Analyst" is deliberately NOT a role noun here -- "Application Systems
    # Analyst" is a business analyst, not an infrastructure owner.
    "systems": r"\bsystems?\s+(?:engineer|administrator|admin|architect)\b",
    "network": r"\bnetwork(?:ing)?\s+(?:engineer|architect|operations)\b",
    # Cloud security sits alongside DevSecOps as an infrastructure owner.
    "cloud_security": r"\bcloud\s+security\s+(?:engineer|architect|lead|director)\b|\bsecurity\s+infrastructure\s+engineer\b",
}

# Infrastructure-adjacent titles belonging to another function. These are
# not "bad companies" -- they are the wrong PERSON/role signal.
DEFAULT_NEGATIVE: dict[str, str] = {
    # "Account Executive" is THE canonical sales title and contains no
    # instance of the word "sales" -- a \bsales\b-only pattern let every
    # AE/SDR/BDR posting through as a non-negative. Mirrors the list in
    # lead_qualification._NEGATIVE_TITLE_PATTERNS.
    "sales": (r"\bsales\b|\baccount\s+executive\b|\b(?:AE|SDR|BDR)\b"
              r"|\bbusiness\s+development\s+(?:rep|representative|manager)\b"),
    "solutions_engineer": r"\bsolutions?\s+engineer(?:ing)?\b|\bsolutions?\s+architect\b",
    "sales_engineer": r"\bsales\s+engineer(?:ing)?\b|\bpre[\s-]?sales\b",
    "support": r"\b(?:technical\s+)?support\s+(?:engineer|specialist|analyst|manager)\b|\bsupport\b",
    "customer_success": r"\bcustomer\s+success\b|\b(?:CSM|TAM)\b|\btechnical\s+account\s+manager\b",
    # A "Consultant" or "Forward Deployed Engineer" title means the company
    # bills this person out to ITS clients -- so the company is a
    # consultancy, which decision 3 excludes anyway. Caught here too
    # because the role signal is visible before the JD text is read.
    "consultant": r"\bconsultant\b|\bconsulting\s+engineer\b|\bforward\s+deployed\b|\bprofessional\s+services\s+engineer\b",
}

# Title markers meaning "this function is being built now".
DEFAULT_INTENT_BOOST: dict[str, str] = {
    "founding": r"\bfounding\b",
    "first": r"\bfirst\b",
    "lead": r"\blead\b",
    "head_of": r"\bhead\s+of\b",
}

# "Salesforce" must never trip the `sales` negative -- a Salesforce
# platform owner is a real buyer. Same carve-out as
# lead_qualification._NEGATIVE_EXEMPT_RE, kept here too because this list
# is separately configurable and would otherwise lose the exemption.
_NEGATIVE_EXEMPT_RE = re.compile(r"\b(?:salesforce|sales\s*force)\b", re.I)


@dataclass
class RoleTaxonomy:
    """Compiled matchers. Built once per run, not per posting."""

    include: dict[str, re.Pattern] = field(default_factory=dict)
    negative: dict[str, re.Pattern] = field(default_factory=dict)
    intent_boost: dict[str, re.Pattern] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Optional[dict[str, Any]] = None) -> "RoleTaxonomy":
        """Build from an mse_icp_configs.role_taxonomy value, falling back
        to the defaults per-section. A config supplying only `include`
        keeps the default negative and intent lists rather than silently
        losing them."""
        config = config or {}
        return cls(
            include=_compile(config.get("include") or DEFAULT_INCLUDE),
            negative=_compile(config.get("negative") or DEFAULT_NEGATIVE),
            intent_boost=_compile(config.get("intent_boost") or DEFAULT_INTENT_BOOST),
        )

    def match(self, title: Optional[str]) -> "RoleMatch":
        """Classify ONE title. Negative wins over include: "Platform
        Solutions Engineer" is a solutions engineer, not a platform hire."""
        if not title:
            return RoleMatch()
        negatives = [] if _NEGATIVE_EXEMPT_RE.search(title) else [
            name for name, pattern in self.negative.items() if pattern.search(title)
        ]
        families = [name for name, pattern in self.include.items() if pattern.search(title)]
        boosts = [name for name, pattern in self.intent_boost.items() if pattern.search(title)]
        return RoleMatch(families=families, negatives=negatives, boosts=boosts)


def _compile(spec: Any) -> dict[str, re.Pattern]:
    """Accepts {name: pattern} or a bare list of terms. A list entry is
    turned into a \\b-anchored literal, so a config author writing
    ["DevOps", "SRE"] gets safe matching without knowing regex."""
    if isinstance(spec, dict):
        out = {}
        for name, pattern in spec.items():
            try:
                out[str(name)] = re.compile(pattern, re.I)
            except re.error:
                # A bad pattern in config must not take the run down; it is
                # treated as a literal term instead, and logged by caller.
                out[str(name)] = re.compile(rf"\b{re.escape(str(pattern))}\b", re.I)
        return out
    if isinstance(spec, (list, tuple)):
        return {str(term): re.compile(rf"\b{re.escape(str(term))}\b", re.I) for term in spec if term}
    return {}


@dataclass
class RoleMatch:
    families: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    boosts: list[str] = field(default_factory=list)

    @property
    def is_match(self) -> bool:
        """A relevant role: at least one include family and no negative."""
        return bool(self.families) and not self.negatives

    @property
    def reasons(self) -> list[str]:
        out = [f"role:{f}" for f in self.families]
        out += [f"role_negative:{n}" for n in self.negatives]
        out += [f"intent:{b}" for b in self.boosts]
        return out


# Freshness policy (Kelvin's decision 2, 2026-10-01). The 60-day window
# replaces a 30-day one that was discarding 342 and 384 MATCHED
# infrastructure roles per run -- more matched roles than it kept.
#
# The two products read an ageing posting differently, which is why this is
# per-product rather than one constant:
#   Consulting    0-60 days at full weight. A role still open at 31-60 days
#                 means they are STRUGGLING to hire, which is the pitch.
#   Cloud Decoded 0-30 full, 31-60 at x0.7. It sells to a team that is
#                 already operating, so a stale req is a weaker signal.
# Over 60 days: dropped for both.
#
# This also absorbs the undated case. The previous x0.7 undated multiplier was
# inert -- both 2026-10-01 runs reported undated_kept=0, because every ATS
# posting in this dataset carries a date. What I had earlier called
# "stale_or_undated" was entirely STALE. An undated posting is now treated as
# the oldest in-window band rather than carrying its own separate rule.
MAX_POSTING_AGE_DAYS = 60

FRESHNESS_FULL_WEIGHT = 1.0
FRESHNESS_AGEING_WEIGHT = 0.7


@dataclass(frozen=True)
class FreshnessPolicy:
    """How one product weights an ageing posting."""

    max_age_days: int = MAX_POSTING_AGE_DAYS
    full_weight_days: int = MAX_POSTING_AGE_DAYS
    ageing_multiplier: float = FRESHNESS_AGEING_WEIGHT

    def weight_for(self, age_days: Optional[int]) -> tuple[Optional[float], str]:
        """(multiplier, reason). None means DROP.

        An undated posting is treated as the oldest in-window band: kept, at
        the ageing weight, because an ATS listing a role usually means it is
        open -- but never at full weight, since nothing establishes it is
        recent.
        """
        if age_days is None:
            return self.ageing_multiplier, f"undated (x{self.ageing_multiplier})"
        if age_days > self.max_age_days:
            return None, f"posting {age_days}d old > {self.max_age_days}d"
        if age_days <= self.full_weight_days:
            return FRESHNESS_FULL_WEIGHT, f"posting {age_days}d old (full weight)"
        return self.ageing_multiplier, (
            f"posting {age_days}d old, past {self.full_weight_days}d "
            f"(x{self.ageing_multiplier})"
        )


# Consulting: a req open 31-60 days is EVIDENCE, not decay.
CONSULTING_FRESHNESS = FreshnessPolicy(max_age_days=60, full_weight_days=60)

# Cloud Decoded: 0-30 full, 31-60 downweighted.
CLOUD_DECODED_FRESHNESS = FreshnessPolicy(max_age_days=60, full_weight_days=30)


def matching_postings(
    postings: Iterable[Any],
    taxonomy: RoleTaxonomy,
    *,
    freshness: Optional[FreshnessPolicy] = None,
    max_age_days: Optional[int] = None,
) -> tuple[list[Any], dict[str, int]]:
    """
    (matched postings, drop-reason counts).

    Role matching is on the TITLE FIELD ONLY, word-boundary anchored -- see
    the module docstring. The returned reason counts distinguish "not an
    infrastructure role" (no_role_family) from "the wrong FUNCTION"
    (negative_*), which is what tells you whether the include list or the
    negative list needs tuning.

    `freshness` is the per-product policy (decision 2). `max_age_days` is
    kept only as a legacy override for callers that pass a bare number; the
    policy wins when both are given.
    """
    policy = freshness or (FreshnessPolicy(max_age_days=max_age_days, full_weight_days=max_age_days)
                           if max_age_days is not None else FreshnessPolicy())
    matched: list[Any] = []
    drops: dict[str, int] = {}

    def _count(reason: str) -> None:
        drops[reason] = drops.get(reason, 0) + 1

    for posting in postings:
        title = getattr(posting, "title", None)
        result = taxonomy.match(title)
        if result.negatives:
            _count(f"negative_{result.negatives[0]}")
            continue
        if not result.families:
            _count("no_role_family")
            continue
        weight, _reason = policy.weight_for(getattr(posting, "age_days", None))
        if weight is None:
            _count("over_max_age")
            continue
        if weight < FRESHNESS_FULL_WEIGHT:
            _count("ageing_downweighted")
        matched.append(posting)

    return matched, drops


def freshness_multiplier_for(
    postings: Iterable[Any], policy: Optional[FreshnessPolicy] = None
) -> tuple[float, str]:
    """(multiplier, reason) for a company's matching postings.

    The FRESHEST matching posting decides. One recent req establishes that the
    company is hiring now, so an older sibling req must not drag the company's
    intent down.
    """
    policy = policy or FreshnessPolicy()
    best_weight, best_reason = policy.ageing_multiplier, "no in-window posting"
    for posting in postings:
        weight, reason = policy.weight_for(getattr(posting, "age_days", None))
        if weight is None:
            continue
        if weight > best_weight or best_reason == "no in-window posting":
            best_weight, best_reason = weight, reason
        if weight >= FRESHNESS_FULL_WEIGHT:
            return weight, reason
    return best_weight, best_reason


# ── Size proxy (Kelvin's decision 4) ─────────────────────────────────────
#
# headcount_band stays unknown from ATS text -- job descriptions essentially
# never state company headcount, and the first live runs confirmed it
# (headcount_band was NULL on every single lead). The board's OPEN-ROLE
# COUNT is the proxy instead: it comes from the cache for free, with zero
# Brave cost and no extra HTTP.

SIZE_PROXY_MIN_OPEN_ROLES = 3
SIZE_PROXY_MAX_OPEN_ROLES = 40


def size_proxy_in_band(
    open_role_count: Optional[int],
    *,
    low: int = SIZE_PROXY_MIN_OPEN_ROLES,
    high: int = SIZE_PROXY_MAX_OPEN_ROLES,
) -> tuple[bool, str]:
    """
    (in_band, reason). Counts the company's TOTAL open roles, not just the
    matching ones -- total is what indicates company size, whereas the
    matching count is an intent signal and is scored separately.

    Below `low`: too small or barely hiring, so there is no real programme
    to help with. Above `high`: an enterprise or a staffing/consulting
    operation. Rubrik and Capco both ran 120 open roles in the first live
    runs and are exactly what this removes.

    Unknown (None) is NOT in band here, unlike headcount_band's three-valued
    logic. The open-role count is always known for a board we successfully
    fetched, so None means the fetch failed -- and a company we could not
    read is not a company we should qualify.
    """
    if open_role_count is None:
        return False, "open_roles unknown"
    if open_role_count < low:
        return False, f"open_roles={open_role_count} < {low} (not hiring at scale)"
    if open_role_count > high:
        return False, f"open_roles={open_role_count} > {high} (enterprise/staffing)"
    return True, f"open_roles={open_role_count} in {low}-{high}"
