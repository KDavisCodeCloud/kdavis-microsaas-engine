"""
tests/test_company_classification.py

Exclusion rules (Kelvin's decision 3, 2026-10-01).

The named requirement: "Caylent, Capco, and Tailscale must fail these rules
for consulting." Each of those is pinned below with the ACTUAL phrasing from
its real job descriptions, fetched from the Greenhouse boards API on
2026-10-01 -- not paraphrased, so the test fails if the detector stops
recognising the real text rather than a tidied-up version of it.

Also pinned: the two false positives found while building this, both of
which would have excluded legitimate prospects.
"""

import pytest

from agents.marketing.company_classification import (
    CLOUD_DECODED_EXCLUSION_POLICY,
    CLOUD_PARTNER,
    CONSULTANCY,
    CONSULTING_EXCLUSION_POLICY,
    INFRA_VENDOR,
    MSP,
    SERVICE_BUSINESS_TAGS,
    STAFFING,
    classify_company,
    distinctive_company_name,
    normalise_company_name,
    policy_for_product,
    text_mentions_company,
)

# Real excerpts, as returned by boards-api.greenhouse.io on 2026-10-01.
CAYLENT_JD = (
    "Caylent is an AI-first cloud services company that helps organizations turn ambitious "
    "ideas into meaningful business impact. As an AWS Premier Tier Services Partner and a "
    "charter member of Anthropic's Claude Partner Network, we combine deep expertise in AWS... "
    "Our capabilities span generative and agentic AI, cloud migration and modernization, "
    "cloud-native application development, data and analytics, DevOps, managed services, "
    "security and compliance... We are seeking a talented Senior Cloud Engineer to join our "
    "growing Managed Service Provider (MSP) team."
)

CAPCO_JD = (
    "CAPCO POLAND. We offer a flexible collaboration model based on a B2B contract... "
    "At Capco Poland, we're not just another consultancy - we're the spark behind digital "
    "transformation in the financial world. As a global leader in technology and management "
    "consulting, we help clients tackle complex challenges across banking, payments, capital "
    "markets, wealth, and asset management. ENGAGEMENT OVERVIEW... The scope of services "
    "includes designing, developing, and deploying intelligent AI agents."
)

TAILSCALE_JD = (
    "About Tailscale. Tailscale is making safe connection effortless by delivering software "
    "that makes it easy to securely interconnect people and their devices, no matter where "
    "they are. From hobbyists to multinational corporations, teams of every size use Tailscale "
    "each day to protect their networks, share access to internal tools, and more. Founded in "
    "2019 and fully distributed, we're backed by Accel, CRV, Insight, Heavybit, and Uncork Capital."
)

RUBRIK_JD = (
    "About the Team. RSC (Rubrik Security Cloud) is the SaaS control plane for Rubrik. Our "
    "mission is to build a highly scalable and reliable cloud data management platform for the "
    "largest enterprises in the world. We build fundamental infrastructure components that all "
    "other RSC product teams at Rubrik build upon, including our microservices architecture, "
    "Kubernetes deployment system, distributed job workflow engine, and database instances."
)

# A real prospect: runs its own infrastructure, sells something else.
CLEAN_JD = (
    "We are hiring a Senior Platform Engineer to own our Azure estate. You will work with "
    "Terraform, Kubernetes and GitHub Actions to support our product engineering teams. "
    "A team of 60 employees, remote-first."
)


class TestNamedCompaniesMustFailForConsulting:
    """Kelvin, 2026-10-01: "Caylent, Capco, and Tailscale must fail these
    rules for consulting.\""""

    @pytest.mark.parametrize("company,jd", [
        ("Caylent", CAYLENT_JD),
        ("Capco", CAPCO_JD),
        ("Tailscale", TAILSCALE_JD),
    ])
    def test_excluded_for_consulting(self, company, jd):
        excluded, multiplier, reasons = CONSULTING_EXCLUSION_POLICY.decide(
            classify_company(company, jd))
        assert excluded is True, f"{company} must not reach consulting outreach"
        assert multiplier == 0.0
        assert reasons, "an exclusion must always name its evidence"

    def test_caylent_is_recognised_as_consultancy_msp_and_cloud_partner(self):
        tags = classify_company("Caylent", CAYLENT_JD).tags
        assert CONSULTANCY in tags
        assert MSP in tags
        assert CLOUD_PARTNER in tags

    def test_capco_is_recognised_as_a_consultancy(self):
        result = classify_company("Capco", CAPCO_JD)
        assert CONSULTANCY in result.tags
        assert "consulting" in result.evidence[CONSULTANCY].lower()

    def test_tailscale_is_recognised_as_an_infra_vendor(self):
        result = classify_company("Tailscale", TAILSCALE_JD)
        assert INFRA_VENDOR in result.tags
        assert result.tags == {INFRA_VENDOR}, (
            "Tailscale is a product company, not a consultancy or MSP -- "
            "mis-tagging it would also exclude it from Cloud Decoded"
        )


class TestCloudDecodedDownweightsRatherThanExcludes:
    """Decision 3: "Cloud Decoded: downweight infra/devtools vendors, don't
    exclude." They genuinely buy observability and cost tooling."""

    def test_tailscale_survives_for_cloud_decoded_at_a_lower_weight(self):
        excluded, multiplier, reasons = CLOUD_DECODED_EXCLUSION_POLICY.decide(
            classify_company("Tailscale", TAILSCALE_JD))
        assert excluded is False
        assert 0.0 < multiplier < 1.0
        assert any("downweighted:infra_vendor" in r for r in reasons)

    def test_rubrik_survives_for_cloud_decoded(self):
        excluded, multiplier, _ = CLOUD_DECODED_EXCLUSION_POLICY.decide(
            classify_company("Rubrik", RUBRIK_JD))
        assert excluded is False
        assert multiplier < 1.0

    def test_service_businesses_are_still_excluded_for_cloud_decoded(self):
        """An MSP would resell Cloud Decoded, not run it."""
        for company, jd in (("Caylent", CAYLENT_JD), ("Capco", CAPCO_JD)):
            excluded, _, _ = CLOUD_DECODED_EXCLUSION_POLICY.decide(classify_company(company, jd))
            assert excluded is True, f"{company} is a service business"

    def test_both_policies_exclude_every_service_business_tag(self):
        for tag in SERVICE_BUSINESS_TAGS:
            assert tag in CONSULTING_EXCLUSION_POLICY.exclude_tags
            assert tag in CLOUD_DECODED_EXCLUSION_POLICY.exclude_tags

    def test_infra_vendor_is_the_only_difference_between_the_policies(self):
        consulting = set(CONSULTING_EXCLUSION_POLICY.exclude_tags)
        cd = set(CLOUD_DECODED_EXCLUSION_POLICY.exclude_tags)
        assert consulting - cd == {INFRA_VENDOR}
        assert cd - consulting == set()


class TestRealProspectsSurvive:
    """An exclusion list that drops real prospects is worse than none."""

    def test_clean_company_is_not_excluded_by_either_policy(self):
        result = classify_company("Northwind Systems", CLEAN_JD)
        assert result.tags == set(), f"unexpected tags: {result.reasons()}"
        for policy in (CONSULTING_EXCLUSION_POLICY, CLOUD_DECODED_EXCLUSION_POLICY):
            excluded, multiplier, _ = policy.decide(result)
            assert excluded is False
            assert multiplier == 1.0

    def test_bare_observability_does_not_make_a_company_a_vendor(self):
        """False positive found 2026-10-01: the pattern was written as
        `\\bobservability|APM|CI/CD\\s+platform\\b`, an ungrouped alternation,
        so bare "observability" matched on its own. It tagged Caylent as an
        infra vendor and would have tagged most infrastructure job
        descriptions ever written."""
        jd = "You will improve our observability and on-call rotation using Datadog."
        assert INFRA_VENDOR not in classify_company("Northwind", jd).tags

    def test_the_word_var_does_not_make_a_company_a_reseller(self):
        """False positive found 2026-10-01: a bare `\\bVAR\\b` matched
        Rubrik's job description, where VAR appears as a code/environment
        token, and excluded a legitimate Cloud Decoded prospect."""
        jd = "Set the VAR in your environment, then export VAR=1 before running the job."
        assert CLOUD_PARTNER not in classify_company("Northwind", jd).tags

    def test_observability_platform_vendor_still_matches(self):
        jd = "We are the leading observability platform for distributed systems."
        assert INFRA_VENDOR in classify_company("Northwind", jd).tags


class TestStaffingAndStandalonePatterns:
    @pytest.mark.parametrize("jd,tag", [
        ("We are an IT staffing firm placing candidates with Fortune 500 clients.", STAFFING),
        ("A contract-to-hire position with one of our clients.", STAFFING),
        ("We are a managed service provider for mid-market healthcare.", MSP),
        ("As an AWS Advanced Consulting Partner we deliver migrations.", CLOUD_PARTNER),
        ("We are a value-added reseller of networking hardware.", CLOUD_PARTNER),
    ])
    def test_tagged(self, jd, tag):
        assert tag in classify_company("Acme", jd).tags

    def test_company_name_alone_can_tag_a_consultancy(self):
        assert CONSULTANCY in classify_company("Smith & Jones Consulting", "").tags

    def test_evidence_is_recorded_for_every_tag(self):
        result = classify_company("Caylent", CAYLENT_JD)
        for tag in result.tags:
            assert result.evidence.get(tag), f"{tag} has no evidence phrase"

    def test_empty_input_tags_nothing(self):
        assert classify_company(None, None).tags == set()
        assert classify_company("", "").tags == set()


class TestPolicyForProduct:
    def test_cloud_decoded_id_gets_the_downweighting_policy(self):
        assert policy_for_product("cd-id", "cd-id") is CLOUD_DECODED_EXCLUSION_POLICY

    def test_anything_else_gets_the_consulting_policy(self):
        assert policy_for_product("consulting-id", "cd-id") is CONSULTING_EXCLUSION_POLICY


class TestSharedNameNormalisation:
    """One normaliser, shared by dedup, decision-maker association and
    domain resolution. Two that disagreed about "Acme Corp" vs "acme-corp"
    is the near-duplicate logic the DRY rule exists to prevent."""

    def test_punctuation_and_casing_removed(self):
        assert normalise_company_name("Acme Corp.") == "acmecorp"
        assert normalise_company_name("acme-corp") == "acmecorp"
        assert normalise_company_name(None) == ""

    def test_distinctive_name_drops_legal_filler(self):
        assert distinctive_company_name("Acme Technologies Inc") == "acmetechnologies"

    def test_mentions_company_matches_both_forms(self):
        assert text_mentions_company("Welcome to VectorLabs", "Vector Labs") is True
        assert text_mentions_company("Welcome to Globex", "Vector Labs") is False

    def test_a_name_of_only_stopwords_matches_nothing(self):
        assert text_mentions_company("anything at all", "Inc") is False
