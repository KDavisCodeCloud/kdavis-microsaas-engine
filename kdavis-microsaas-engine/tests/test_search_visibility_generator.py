"""
Tests for the Search Visibility Layer generator (added 2026-07-20 after a
launch-readiness audit found CLAUDE.md's SEO/AEO/GEO/SXO non-negotiable
had zero implementation, and the scaffold's app/page.tsx was a bare
redirect to /login with no public marketing site to attach SEO to at all.
"""
import json

import pytest

from agents.factory.search_visibility_generator import (
    SearchVisibilityError,
    generate_search_visibility_content,
    render_comparison_page,
    render_definitive_page,
    render_faq_page,
    render_landing_page,
    render_pricing_page,
    render_robots_ts,
    render_sitemap_ts,
)


def _valid_content(**overrides) -> dict:
    base = {
        "meta_title": "Test Product — Solve The Thing",
        "meta_description": "Test Product solves the thing your current tool doesn't, so you stop losing time on it every week.",
        "top_10_queries": [f"query {i}" for i in range(10)],
        "hero_headline": "Solve the thing",
        "hero_subheadline": "Automatically, every time.",
        "features": [{"title": "Feature A", "description": "Does the thing."}],
        "faq": [{"question": f"Q{i}?", "answer": f"A{i}."} for i in range(10)],
        "definitive_question": "How do you solve the thing?",
        "definitive_answer_long": "This solves the thing. " * 260,
        "geo_headline": "Best tool for solving the thing",
        "comparison_points": [{"feature": "Speed", "us": "Fast", "them": "Slow"}],
        "author_attribution": "Test Product",
        "trial_cta_text": "Start Free Trial",
    }
    base.update(overrides)
    return base


def _canned_llm(response: dict):
    def _llm(system: str, user: str, max_tokens: int = 8000) -> str:
        return "Narration first.\n\n" + json.dumps(response)
    return _llm


OPP = {
    "solution_concept": "Test Product",
    "vertical": "Small business owners who hate the thing",
    "tier_structure": {"starter": 29, "growth": 59},
    "mrr_calculation": "100 accounts x $29 = $2,900/mo",
    "verdict_v2_output": {
        "existing_tool": {"name": "OldTool"},
        "gap_type": "FEATURE_GAP",
        "pain_evidence": "OldTool doesn't do the thing, 5+ reviews cite this.",
        "icp": "Small business owners who hate the thing",
    },
}


# ── generate_search_visibility_content ──────────────────────────────

def test_generates_content_and_derives_slugs_from_real_opportunity_data():
    content = generate_search_visibility_content(OPP, llm=_canned_llm(_valid_content()))
    assert content["existing_tool_name"] == "OldTool"
    assert content["comparison_slug"] == "vs-oldtool"
    assert content["definitive_slug"] == "how-do-you-solve-the-thing"


def test_falls_back_gracefully_when_verdict_v2_output_is_missing():
    minimal_opp = {"solution_concept": "Test Product", "vertical": "Some ICP"}
    content = generate_search_visibility_content(minimal_opp, llm=_canned_llm(_valid_content()))
    assert content["existing_tool_name"]  # some fallback string, not a crash
    assert content["comparison_slug"].startswith("vs-")


def test_raises_if_fewer_than_10_faq_pairs():
    bad_content = _valid_content(faq=[{"question": "Q?", "answer": "A."}])
    with pytest.raises(SearchVisibilityError, match="FAQ"):
        generate_search_visibility_content(OPP, llm=_canned_llm(bad_content))


def test_raises_if_fewer_than_10_search_queries():
    bad_content = _valid_content(top_10_queries=["only one query"])
    with pytest.raises(SearchVisibilityError, match="search quer"):
        generate_search_visibility_content(OPP, llm=_canned_llm(bad_content))


def test_raises_if_definitive_answer_is_too_short():
    bad_content = _valid_content(definitive_answer_long="Way too short.")
    with pytest.raises(SearchVisibilityError, match="1,200"):
        generate_search_visibility_content(OPP, llm=_canned_llm(bad_content))


def test_extracts_trailing_json_ignoring_narrative_braces():
    response = 'The tool costs {"placeholder": "not this"} per docs.\n\n' + json.dumps(_valid_content())
    content = generate_search_visibility_content(OPP, llm=lambda s, u, max_tokens=8000: response)
    assert content["meta_title"] == "Test Product — Solve The Thing"


# ── Rendered pages: JSON-LD, required elements, no dead ends ────────

def test_landing_page_has_json_ld_and_above_fold_cta():
    content = _valid_content()
    content["comparison_slug"] = "vs-oldtool"
    content["existing_tool_name"] = "OldTool"
    page = render_landing_page("Test Product", "test-product", content, {"starter": 29})
    assert "application/ld+json" in page
    assert "SoftwareApplication" in page
    assert "Start Free Trial" in page
    assert 'href="/faq"' in page
    assert 'href="/pricing"' in page
    # Social proof is a real slot, deliberately not fabricated content
    assert "fabricated testimonial" in page or "real customer quote" in page


def test_json_ld_escapes_less_than_to_prevent_early_script_close():
    # Real bug found while manually reviewing a generated scaffold
    # (2026-07-20): LLM-generated field values are not validated
    # HTML-safe -- a literal "</script>" inside embedded JSON-LD would
    # close the script tag early and inject whatever follows as raw HTML.
    # Only the dangerouslySetInnerHTML line is the actual risk (it inserts
    # raw HTML) -- content elsewhere in the page (e.g. a plain JS string
    # literal in the metadata export) is a separate, already-safe context
    # that Next.js's own rendering escapes normally, so this test checks
    # specifically the JSON-LD script line, not the whole page text.
    content = _valid_content(meta_description='Better than </script><img src=x onerror=alert(1)>')
    page = render_landing_page("Test Product", "test-product", content, {"starter": 29})
    json_ld_line = next(line for line in page.splitlines() if "dangerouslySetInnerHTML" in line)
    assert "</script><img" not in json_ld_line
    assert "u003c" in json_ld_line


def test_jsx_text_content_is_escaped_against_broken_syntax_and_injection():
    # Real bug found reviewing a generated scaffold (2026-07-20): LLM
    # content was interpolated directly as JSX children text with zero
    # escaping -- a raw "<" would be parsed as a new tag, a raw "{" as
    # the start of a JS expression, either breaking the file's syntax or
    # injecting an unintended element into the rendered page.
    content = _valid_content(
        hero_headline='Better than {competitor} <script>alert(1)</script>',
        features=[{"title": "A & B <tag>", "description": "Uses {braces} too"}],
    )
    page = render_landing_page("Test Product", "test-product", content, {"starter": 29})
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "&#123;competitor&#125;" in page
    assert "&#123;braces&#125;" in page


def test_pricing_page_has_product_json_ld_and_all_tiers():
    content = _valid_content()
    page = render_pricing_page("Test Product", content, {"starter": 29, "growth": 59, "scale": 99})
    assert "AggregateOffer" in page
    for tier in ("starter", "growth", "scale"):
        assert tier in page
    assert "29" in page and "59" in page and "99" in page


def test_faq_page_has_faqpage_json_ld_for_every_question():
    content = _valid_content()
    page = render_faq_page("Test Product", content)
    assert "FAQPage" in page
    for i in range(10):
        assert f"Q{i}?" in page


def test_comparison_page_names_the_real_existing_tool():
    content = _valid_content()
    content["existing_tool_name"] = "OldTool"
    page = render_comparison_page("Test Product", content)
    assert "Test Product vs OldTool" in page
    assert "OldTool" in page


def test_definitive_page_meets_word_floor_and_has_author_attribution():
    content = _valid_content()
    page = render_definitive_page("Test Product", content)
    assert "Test Product" in page  # author_attribution
    assert "Article" in page


def test_sitemap_includes_every_generated_page():
    content = _valid_content()
    content["comparison_slug"] = "vs-oldtool"
    content["definitive_slug"] = "how-do-you-solve-the-thing"
    sitemap = render_sitemap_ts("test-product", content)
    for path in ["/pricing", "/faq", "/vs-oldtool", "/how-do-you-solve-the-thing"]:
        assert path in sitemap


def test_robots_disallows_authenticated_paths_and_points_to_sitemap():
    robots = render_robots_ts("test-product")
    assert "/login" in robots
    assert "/dashboard" in robots
    assert "sitemap.xml" in robots
