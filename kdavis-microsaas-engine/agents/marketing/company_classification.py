"""
agents/marketing/company_classification.py

Exclusion rules for scraper v2 (Kelvin's decision 3, 2026-10-01).

WHY. Both products sell TO companies that run their own infrastructure.
A company whose business IS running other people's infrastructure is not a
prospect, it's a competitor or a peer:

  Both products exclude: consultancies, MSPs, cloud partners/resellers,
                         staffing firms.
  Consulting also excludes: infra/devtools vendors (they build the tools;
                         they don't buy architecture help).
  Cloud Decoded downweights infra/devtools vendors rather than excluding
                         them -- they genuinely do buy observability and
                         cost tooling.

EVIDENCE-BASED, NOT NAME-BASED. Every pattern below was written against
the real job-description text of the companies the first live run actually
surfaced (fetched 2026-10-01 from the Greenhouse boards API), not guessed
from company names. A name-matching blocklist would have been quicker and
would have silently failed on the next 200 companies. The named examples
from that run, with the phrases that catch them:

  Caylent   "AI-first cloud services company", "AWS Premier Tier Services
            Partner", "helps organizations turn...", "managed services",
            "Managed Service Provider (MSP) team"
            -> consultancy + cloud_partner + msp
  Capco     "we're not just another consultancy", "global leader in
            technology and management consulting", "we help clients
            tackle", "ENGAGEMENT OVERVIEW", "scope of services"
            -> consultancy
  Tailscale "delivering software that makes it easy to...", "teams of
            every size use Tailscale each day", "backed by Accel, CRV,
            Insight, Heavybit and Uncork Capital"
            -> infra_vendor
  Rubrik    "RSC is the SaaS control plane for Rubrik", "build a highly
            scalable...cloud data management platform for the largest
            enterprises"
            -> infra_vendor

Every classification carries the matched phrase as evidence, so a dropped
company can always be explained -- same discipline as score_reasons.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# Classification tags.
CONSULTANCY = "consultancy"
MSP = "msp"
CLOUD_PARTNER = "cloud_partner"
RESELLER = "reseller"
STAFFING = "staffing"
INFRA_VENDOR = "infra_vendor"

ALL_TAGS = (CONSULTANCY, MSP, CLOUD_PARTNER, RESELLER, STAFFING, INFRA_VENDOR)

# ── Shared company-name normalisation ────────────────────────────────────
#
# Lives here because three modules now need the same comparison and this is
# the lowest one in the import graph: company_first_sourcing (same-company
# dedup, decision-maker association) and domain_resolution (does this host
# belong to this company) both import it rather than keeping a private
# copy. Two slightly-different normalisers would disagree about whether
# "Acme Corp" and "acme-corp" are the same company, which is exactly the
# kind of near-duplicate logic this codebase's DRY rule exists to stop.

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# Words that carry no identity: "Labs Inc" must not match every company
# with "Labs" in its name.
COMPANY_STOPWORDS = frozenset({
    "inc", "llc", "ltd", "corp", "corporation", "co", "gmbh", "bv", "plc",
    "limited", "holdings", "group", "the", "and",
})


def normalise_company_name(value: Optional[str]) -> str:
    """Lowercased, punctuation stripped. "Acme Corp." -> "acmecorp"."""
    return _NON_ALNUM_RE.sub("", (value or "").lower())


def distinctive_company_name(value: Optional[str]) -> str:
    """Normalised, with legal/filler words removed, so "Acme Technologies
    Inc" still matches text that says only "Acme Technologies"."""
    words = [w for w in _NON_ALNUM_RE.sub(" ", (value or "").lower()).split()
             if w and w not in COMPANY_STOPWORDS]
    return normalise_company_name("".join(words))


def text_mentions_company(text: Optional[str], company: Optional[str]) -> bool:
    """True when `text` plausibly names `company`.

    Compared with punctuation and casing removed, because an ATS token
    yields "Vector Labs" while a profile says "VectorLabs". A name that
    reduces to nothing but stopwords is unverifiable (False) rather than
    matching everything.
    """
    normalised = normalise_company_name(company)
    if not normalised:
        return False
    haystack = normalise_company_name(text)
    if normalised in haystack:
        return True
    distinctive = distinctive_company_name(company)
    return bool(distinctive) and distinctive in haystack


# Internal alias used by agents.marketing.domain_resolution.
_normalise_company_name = normalise_company_name

# Excluded for BOTH products -- these businesses run infrastructure for
# other people, so neither product's value proposition applies.
SERVICE_BUSINESS_TAGS = (CONSULTANCY, MSP, CLOUD_PARTNER, RESELLER, STAFFING)


@dataclass
class CompanyClassification:
    """Tags plus the phrase that justified each one."""

    tags: set[str] = field(default_factory=set)
    evidence: dict[str, str] = field(default_factory=dict)

    def has_any(self, tags: Iterable[str]) -> bool:
        return bool(self.tags & set(tags))

    def reasons(self) -> list[str]:
        return [f"{tag}:{self.evidence.get(tag, '?')}" for tag in sorted(self.tags)]


# Each pattern is \b-anchored and phrase-level rather than single-word.
# Single words would be hopeless here: almost every job description
# contains "customers", "platform" and "solutions" somewhere.
_PATTERNS: dict[str, list[re.Pattern]] = {
    CONSULTANCY: [
        re.compile(r"\b(?:management|technology|digital|IT|strategy)\s+consult(?:ing|ancy)\b", re.I),
        re.compile(r"\b(?:a|another|global|leading|boutique)\s+consult(?:ing|ancy)\s*(?:firm|company)?\b", re.I),
        re.compile(r"\bconsult(?:ing|ancy)\s+(?:firm|company|practice|services)\b", re.I),
        re.compile(r"\bwe\s+help\s+(?:our\s+)?clients\b", re.I),
        re.compile(r"\bclient\s+engagements?\b", re.I),
        re.compile(r"\bbillable\s+(?:hours|utilization)\b", re.I),
        re.compile(r"\bengagement\s+overview\b", re.I),
        re.compile(r"\bscope\s+of\s+services\b", re.I),
        re.compile(r"\bprofessional\s+services\s+(?:firm|organization|team)\b", re.I),
        re.compile(r"\b(?:cloud|technology|digital)\s+services\s+company\b", re.I),
        re.compile(r"\bhelps?\s+organi[sz]ations\b", re.I),
        re.compile(r"\bon\s+behalf\s+of\s+our\s+clients\b", re.I),
    ],
    MSP: [
        re.compile(r"\bmanaged\s+service\s+provider\b", re.I),
        re.compile(r"\bMSP\b"),  # case-sensitive: the acronym, not "msp" inside a word
        re.compile(r"\bmanaged\s+services\b", re.I),
        re.compile(r"\bNOC\b|\bnetwork\s+operations\s+cent(?:er|re)\s+for\s+clients\b"),
        re.compile(r"\bmanage\s+(?:our\s+)?clients'?\s+(?:infrastructure|cloud|environments?)\b", re.I),
    ],
    CLOUD_PARTNER: [
        re.compile(r"\b(?:AWS|Azure|Google\s+Cloud|GCP)\s+(?:premier\s+)?(?:tier\s+)?(?:services\s+)?partner\b", re.I),
        re.compile(r"\b(?:premier|advanced|select|gold|platinum)\s+(?:tier\s+)?(?:consulting\s+)?partner\b", re.I),
        re.compile(r"\bpartner\s+network\b", re.I),
        re.compile(r"\bsystems?\s+integrator\b", re.I),
        # Spelled out only. A bare \bVAR\b matched Rubrik's job description
        # (2026-10-01) -- "VAR" appears in JDs as an environment-variable
        # or code token, and that false positive excluded a legitimate
        # Cloud Decoded prospect outright.
        re.compile(r"\bvalue[- ]added\s+reseller\b", re.I),
    ],
    RESELLER: [
        re.compile(r"\breseller\b", re.I),
        re.compile(r"\bdistributor\s+of\b", re.I),
        re.compile(r"\bwe\s+resell\b", re.I),
    ],
    STAFFING: [
        re.compile(r"\bstaffing\s+(?:agency|firm|company|solutions)\b", re.I),
        re.compile(r"\b(?:IT|tech(?:nical)?)\s+staffing\b", re.I),
        re.compile(r"\brecruit(?:ing|ment)\s+(?:agency|firm)\b", re.I),
        re.compile(r"\bcontract[- ]to[- ]hire\b", re.I),
        re.compile(r"\bplace\s+candidates\s+(?:with|at)\b", re.I),
        re.compile(r"\bour\s+clients\s+are\s+(?:looking\s+for|hiring)\b", re.I),
        re.compile(r"\btalent\s+solutions\s+(?:firm|company|provider)\b", re.I),
    ],
    INFRA_VENDOR: [
        # "teams use <Product> to ...", "our customers use ..."
        re.compile(r"\b(?:teams|companies|developers|engineers|customers)\s+of\s+every\s+size\s+use\b", re.I),
        re.compile(r"\b(?:thousands|millions|hundreds)\s+of\s+(?:developers|engineers|teams|companies)\s+(?:use|rely\s+on|trust)\b", re.I),
        re.compile(r"\bwe\s+(?:build|are\s+building|deliver)\s+(?:the\s+)?(?:software|platform|product|tools?)\s+that\b", re.I),
        re.compile(r"\bdelivering\s+software\s+that\b", re.I),
        re.compile(r"\bour\s+(?:product|platform|SaaS)\s+(?:is|helps|enables|lets)\b", re.I),
        re.compile(r"\bSaaS\s+(?:control\s+plane|platform|product)\b", re.I),
        re.compile(r"\b(?:open[- ]source|developer)\s+(?:tool|platform|infrastructure)\s+compan(?:y|ies)\b", re.I),
        re.compile(r"\bbacked\s+by\s+[A-Z][\w&.]*(?:\s*,\s*[A-Z][\w&.]*)+", re.M),
        re.compile(r"\b(?:series\s+[A-F]|seed)\s+(?:round\s+)?(?:led\s+by|from)\b", re.I),
        re.compile(r"\bcloud\s+data\s+management\s+platform\b", re.I),
        # Grouped, and the category word is required. Written originally as
        # `\bobservability|APM|CI/CD\s+platform\b`, where the ungrouped
        # alternation made bare "observability" match on its own -- which
        # tagged Caylent as an infra vendor and would have tagged most
        # infrastructure job descriptions ever written.
        re.compile(r"\b(?:observability|APM|CI/CD|monitoring)\s+(?:platform|product|vendor|compan(?:y|ies))\b", re.I),
    ],
}

# Company-NAME signals. A weaker source than prose (a name alone proves
# little), so these only fire alongside the prose patterns above -- except
# the unambiguous ones, which are strong enough on their own.
_NAME_PATTERNS: dict[str, re.Pattern] = {
    # LEAK FIXED 2026-10-06. This matched "advisory" but not "advisors", so
    # "Security Risk Advisors" -- a security consulting firm -- cleared the
    # consultancy exclusion and reached the approval queue for the CONSULTING
    # product, i.e. we drafted consulting outreach to a competitor.
    #
    # The prose patterns in _PATTERNS are the primary net and are what caught
    # Caylent and Capco (neither matches by name either). This name check is
    # the backstop for when the JD itself never says "consulting firm" -- which
    # is common, because a Cloud Security Engineer posting describes the ROLE,
    # not the business model. Missing a plural noun form made the backstop
    # silently absent for a whole class of names.
    #
    # Agent-noun and plural forms included deliberately. "partners" is NOT:
    # PDT Partners is a quantitative fund and Pdtpartners already appears in
    # this pipeline's own lead list, so that word would exclude real
    # prospects.
    CONSULTANCY: re.compile(
        r"\b(?:consult(?:ing|ancy|ancies|ants?)|advisor(?:y|s)?|advisers?)\b", re.I),
    MSP: re.compile(r"\bmanaged\s+(?:services?|IT)\b", re.I),
    STAFFING: re.compile(r"\b(?:staffing|recruit(?:ing|ment)|talent\s+group)\b", re.I),
}


def classify_company(
    company: Optional[str],
    jd_text: Optional[str] = None,
    *,
    extra_text: Optional[str] = None,
) -> CompanyClassification:
    """
    Tags for one company, from its own job-description prose.

    `jd_text` must already be plain text -- scrapers.ats_boards._strip_html
    handles the HTML-escaped-HTML that Greenhouse actually returns. Passing
    raw escaped markup here would match almost nothing, which is precisely
    the bug fixed alongside this module.
    """
    result = CompanyClassification()
    haystack = " ".join(t for t in (jd_text, extra_text) if t)

    for tag, patterns in _PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(haystack)
            if match:
                result.tags.add(tag)
                result.evidence[tag] = match.group(0).strip()[:80]
                break

    for tag, pattern in _NAME_PATTERNS.items():
        if tag in result.tags:
            continue
        match = pattern.search(company or "")
        if match:
            result.tags.add(tag)
            result.evidence[tag] = f"name:{match.group(0).strip()}"

    return result


# ── Per-product policy ───────────────────────────────────────────────────

@dataclass
class ExclusionPolicy:
    """What a given product does with each tag. Separated from detection so
    the same classification serves both products -- Cloud Decoded
    downweights exactly what consulting excludes."""

    exclude_tags: tuple[str, ...]
    downweight_tags: tuple[str, ...] = ()
    downweight_factor: float = 0.6

    def decide(self, classification: CompanyClassification) -> tuple[bool, float, list[str]]:
        """(excluded, fit_multiplier, reasons)."""
        reasons: list[str] = []
        for tag in self.exclude_tags:
            if tag in classification.tags:
                reasons.append(f"excluded:{tag}={classification.evidence.get(tag, '?')}")
        if reasons:
            return True, 0.0, reasons

        multiplier = 1.0
        for tag in self.downweight_tags:
            if tag in classification.tags:
                multiplier = self.downweight_factor
                reasons.append(f"downweighted:{tag}={classification.evidence.get(tag, '?')}")
        return False, multiplier, reasons


# Consulting sells architecture/migration work. A consultancy, MSP, cloud
# partner, reseller or staffing firm competes with that offer; an
# infra/devtools vendor builds the tooling and does not buy the service.
CONSULTING_EXCLUSION_POLICY = ExclusionPolicy(
    exclude_tags=SERVICE_BUSINESS_TAGS + (INFRA_VENDOR,),
)

# Cloud Decoded sells an operational product. The service businesses are
# still out (they'd resell it, not run it), but an infra/devtools vendor is
# a genuine buyer of observability and cost control -- ranked lower, not
# removed (Kelvin, 2026-10-01: "downweight ... don't exclude").
CLOUD_DECODED_EXCLUSION_POLICY = ExclusionPolicy(
    exclude_tags=SERVICE_BUSINESS_TAGS,
    downweight_tags=(INFRA_VENDOR,),
)


def policy_for_product(product_id: str, cloud_decoded_product_id: str) -> ExclusionPolicy:
    return (CLOUD_DECODED_EXCLUSION_POLICY
            if product_id == cloud_decoded_product_id
            else CONSULTING_EXCLUSION_POLICY)
