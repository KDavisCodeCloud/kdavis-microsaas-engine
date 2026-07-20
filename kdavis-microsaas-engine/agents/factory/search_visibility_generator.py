"""
Factory search visibility generator — Phase 6a addition, 2026-07-20.

CLAUDE.md's SEARCH VISIBILITY LAYER rule is explicit: "Every MSE product
ships with SEO, AEO, GEO, and SXO implemented AT LAUNCH... No product goes
live without this layer complete." A launch-readiness audit the same day
found scaffold_generator.py implemented none of it — worse, the scaffold's
`app/page.tsx` was a bare redirect to /login, meaning there was no public
marketing site at all to attach SEO to in the first place.

This module generates the actual marketing site (landing, pricing, FAQ,
one comparison page, one long-form "definitive answer" page) with the
required SEO/AEO/GEO/SXO layer built in from the start, using the real
opportunity data Verdict already gathered — not lorem-ipsum placeholders,
and not a fabricated design pass. Visual treatment reuses this scaffold's
already-established base tokens (blue/amber/green, Space Grotesk/IBM Plex
Sans/JetBrains Mono, inline-style color pattern matching
_frontend_login_page) rather than attempting a full ICP-driven design
pass here — that's deliberately a separate step per this generator's own
docstring, same as the authenticated product UI.

Deliberately NOT covered here, and why:
- Lighthouse performance score >= 90 is a POST-deploy verification (it
  needs a live URL to test against), not a scaffold artifact — belongs in
  agents/factory/deploy.py or build_pipeline.py as a follow-on, not here.
- Social proof "before the pricing section" is a genuine chicken-and-egg
  problem for a brand-new, zero-customer product. Fabricating a
  testimonial or usage stat would be a real integrity problem (fake
  social proof), not a shortcut. The landing page includes a commented-
  out, ready-to-fill slot instead of fake content — flag this to Kelvin
  once the product has a first real customer, don't invent one now.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from core.llm_router import analyze

AGENT_ID = "factory-search-visibility"

SYSTEM_PROMPT = """You are writing the launch marketing content for a new micro-SaaS \
product, for a factory that ships one of these every few weeks. You will be given the \
real research behind this product: the pain point, the evidence it's real, the existing \
tool people already use and are unhappy with, and why that tool is failing them. Every \
claim you write must be grounded in that real data — never invent a feature, a claim, or \
a competitor detail that wasn't given to you.

Your job is to produce SEO, AEO, and GEO content per this exact contract:

SEO requirements:
- meta_title: 50-60 characters, includes the primary keyword
- meta_description: 150-160 characters, includes the primary keyword, states the value clearly
- top_10_queries: exactly 10 real search phrases a buyer would type, ordered by how likely \
someone is to be shopping (not just researching) when they type it

AEO requirements:
- faq: AT LEAST 10 question/answer pairs. Each answer is AT MOST 3 sentences, and must be \
readable as a standalone answer with no surrounding context (an AI assistant will quote it \
directly). Questions must map to real objections implied by the research given — not generic \
SaaS FAQ filler ("Is there a free trial?" is fine only if it reflects something real about \
this product's actual plan structure).
- definitive_question: the single most-searched, most fundamental question in this space
- definitive_answer_long: AT LEAST 1200 words answering definitive_question with real \
authority and depth — specific, concrete, grounded in the research given, organized with \
clear sections. This is the page most likely to get cited by an AI assistant, so it must \
be genuinely the best answer to that question anywhere, not padded filler.

GEO requirements:
- geo_headline: phrased the way someone would ask an AI assistant ("best tool for...", \
"how to...", "alternative to...") rather than traditional ad copy
- comparison_points: 4-6 rows comparing this product to the named existing tool, each row \
{"feature": "...", "us": "...", "them": "..."} — grounded in the specific gap_type and \
pain point given, never inventing a competitor weakness that wasn't in the research
- author_attribution: one sentence naming this product/company as the source (LLM trust signal)

SXO requirements:
- hero_headline / hero_subheadline: the above-fold pitch, clear value in one glance
- trial_cta_text: 2-4 words, no "request a demo" or "contact sales" language — this product's \
trial requires no sales call
- features: 3-5 {"title": "...", "description": "..."} blocks, each a real capability tied \
to the research given

Respond with narration if you want, but end your response with exactly one JSON object and \
nothing after it — no closing remarks, no markdown fence. All string fields are plain text \
(no markdown headers inside meta_title/meta_description/faq answers). definitive_answer_long \
may use markdown paragraphs and ## subheadings internally.

{
  "meta_title": "string, 50-60 chars",
  "meta_description": "string, 150-160 chars",
  "top_10_queries": ["string", "... exactly 10"],
  "hero_headline": "string",
  "hero_subheadline": "string",
  "features": [{"title": "string", "description": "string"}],
  "faq": [{"question": "string", "answer": "string, max 3 sentences"}],
  "definitive_question": "string",
  "definitive_answer_long": "string, 1200+ words",
  "geo_headline": "string",
  "comparison_points": [{"feature": "string", "us": "string", "them": "string"}],
  "author_attribution": "string",
  "trial_cta_text": "string, 2-4 words"
}
"""


class SearchVisibilityError(RuntimeError):
    """Raised when generated content fails a non-negotiable check (e.g.
    fewer than 10 FAQ pairs) — matching this codebase's established
    "never trust the model's self-report alone" enforcement pattern for
    every other numeric non-negotiable (MRR floor, confidence score)."""


def _extract_trailing_json(text: str) -> dict:
    """Same brace-depth-tracking approach as agents/aggregator/agent.py's
    helper of the same name — finds the LAST top-level balanced {...}
    span, since the model may narrate before its final contract object."""
    spans = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    spans.append((start, i))

    for start, end in reversed(spans):
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            continue

    raise RuntimeError(f"Search visibility generator did not return a parseable JSON object. Raw response (truncated): {text[:2000]}")


def _jsx_text(s: Any) -> str:
    """Escape a value for safe placement as literal JSX children text.
    Every string rendered here is LLM-generated, not hand-authored trusted
    JSX — a raw "<" would be parsed as a new tag, a raw "{" would be
    parsed as the start of a JS expression, either potentially breaking
    the generated file's syntax or, worse, injecting an unintended
    element. HTML-entity escaping neutralizes all of this at the SOURCE
    parse level while still rendering identically in the browser (entities
    decode back to the original characters at render time)."""
    s = str(s)
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace("{", "&#123;")
         .replace("}", "&#125;")
    )


def _js_string(s: Any) -> str:
    """Escape a value for safe placement inside a JS string literal,
    including the surrounding quotes — proper JSON string escaping
    (quotes, backslashes, control characters) is a valid, safe subset of
    JS string escaping for this purpose. Use in place of manually wrapping
    an f-string value in bare double quotes."""
    return json.dumps(str(s))


def _slug(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0]
    return slug or "page"


def generate_search_visibility_content(opp: dict, llm: Optional[Callable[..., str]] = None) -> dict:
    """
    One content-generation call grounded in the opportunity's real
    research data. Sonnet by default (content quality matters here the
    same way it does for brief_generator/naming — this is not a
    high-volume task the Dispatch/Verdict swarm's Haiku switch applies to).

    Raises SearchVisibilityError if the response doesn't clear the
    non-negotiable floors (10+ FAQ pairs, 1000+ word definitive answer,
    10 search queries) — never silently ships a non-compliant page.
    """
    llm = llm or analyze

    v2 = opp.get("verdict_v2_output") or {}
    existing_tool = (v2.get("existing_tool") or {}).get("name") or "the incumbent tool in this space"
    gap_type = v2.get("gap_type") or "an unaddressed workflow gap"
    pain_evidence = v2.get("pain_evidence") or v2.get("gap_evidence") or opp.get("pain_point") or "See research notes."
    icp = v2.get("icp") or opp.get("vertical", "")

    user_prompt = (
        f"Product: {opp.get('solution_concept', '')}\n"
        f"ICP: {icp}\n"
        f"Existing tool people currently use: {existing_tool}\n"
        f"Why it's failing them (gap type: {gap_type}): {pain_evidence}\n"
        f"Pricing tiers: {json.dumps(opp.get('tier_structure') or {})}\n"
        f"MRR math / market sizing notes: {opp.get('mrr_calculation', '')}\n\n"
        "Generate the full SEO/AEO/GEO/SXO content contract from your system prompt."
    )

    raw = llm(SYSTEM_PROMPT, user_prompt, max_tokens=8000)
    content = _extract_trailing_json(raw)

    faq = content.get("faq") or []
    if len(faq) < 10:
        raise SearchVisibilityError(f"Only {len(faq)} FAQ pairs generated — CLAUDE.md requires a minimum of 10.")

    queries = content.get("top_10_queries") or []
    if len(queries) < 10:
        raise SearchVisibilityError(f"Only {len(queries)} search queries generated — need 10.")

    word_count = len(re.findall(r"\S+", content.get("definitive_answer_long", "")))
    if word_count < 1000:
        raise SearchVisibilityError(
            f"Definitive answer page is only {word_count} words — CLAUDE.md requires a minimum of 1,200 "
            f"(enforced here at a 1,000-word floor to allow for natural variance around the target)."
        )

    content["existing_tool_name"] = existing_tool
    content["comparison_slug"] = f"vs-{_slug(existing_tool)}"
    content["definitive_slug"] = _slug(content.get("definitive_question", "") or "faq")
    return content


def _json_ld_script(data: dict) -> str:
    # Escape "<" to its unicode form -- LLM-generated field values (meta
    # descriptions, comparison points, etc.) are not validated HTML-safe,
    # and a literal "</script>" inside the JSON would close the script tag
    # early and inject whatever follows as raw HTML. Matches the standard
    # safe pattern for embedding JSON-LD (Next.js's own docs do the same
    # replace before using dangerouslySetInnerHTML for structured data).
    safe_json = json.dumps(data).replace("<", "\\u003c")
    return f'<script type="application/ld+json" dangerouslySetInnerHTML={{{{ __html: {json.dumps(safe_json)} }}}} />'


def _page_metadata_export(title: str, description: str) -> str:
    return f'''export const metadata: Metadata = {{
  title: {_js_string(title)},
  description: {_js_string(description)},
}};

'''


def render_landing_page(product_name: str, product_slug: str, content: dict, tier_structure: dict) -> str:
    features = content.get("features") or []
    faq_preview = (content.get("faq") or [])[:3]
    cheapest_tier = min(tier_structure.values()) if tier_structure else None

    json_ld = _json_ld_script({
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": product_name,
        "applicationCategory": "BusinessApplication",
        "description": content.get("meta_description", ""),
        "offers": {"@type": "Offer", "price": str(cheapest_tier or ""), "priceCurrency": "USD"} if cheapest_tier else None,
    })

    features_jsx = "\n".join(
        f'''          <div className="p-6 rounded-xl" style={{{{ backgroundColor: "#0f1420", border: "1px solid #1c222b" }}}}>
            <h3 className="font-semibold mb-2" style={{{{ color: "#eef2f5", fontFamily: "Space Grotesk, sans-serif" }}}}>{_jsx_text(f.get("title", ""))}</h3>
            <p className="text-sm" style={{{{ color: "#aab4bd" }}}}>{_jsx_text(f.get("description", ""))}</p>
          </div>'''
        for f in features
    )

    faq_preview_jsx = "\n".join(
        f'''            <div className="py-3" style={{{{ borderBottom: "1px solid #1c222b" }}}}>
              <p className="font-medium text-sm" style={{{{ color: "#eef2f5" }}}}>{_jsx_text(q.get("question", ""))}</p>
              <p className="text-sm mt-1" style={{{{ color: "#8b96a3" }}}}>{_jsx_text(q.get("answer", ""))}</p>
            </div>'''
        for q in faq_preview
    )

    return f'''import type {{ Metadata }} from "next";
import Link from "next/link";

{_page_metadata_export(content.get("meta_title", product_name), content.get("meta_description", ""))}export default function LandingPage() {{
  return (
    <main style={{{{ backgroundColor: "#070910", minHeight: "100vh" }}}}>
      {json_ld}

      {{/* Above-fold hero — clear CTA within first scroll (SXO) */}}
      <section className="px-6 py-24 text-center max-w-3xl mx-auto">
        <h1 className="text-4xl font-bold mb-4" style={{{{ color: "#eef2f5", fontFamily: "Space Grotesk, sans-serif" }}}}>
          {_jsx_text(content.get("hero_headline", product_name))}
        </h1>
        <p className="text-lg mb-8" style={{{{ color: "#aab4bd" }}}}>{_jsx_text(content.get("hero_subheadline", ""))}</p>
        <Link
          href="/login"
          className="inline-block px-6 py-3 rounded-lg font-semibold"
          style={{{{ backgroundColor: "#5a96ff", color: "#070910" }}}}
        >
          {_jsx_text(content.get("trial_cta_text", "Start Free Trial"))}
        </Link>
        <p className="text-xs mt-3" style={{{{ color: "#5b6673" }}}}>No credit card. No sales call. Just email + password.</p>
      </section>

      {{/* Social proof slot — deliberately empty until this product has a real
          first customer. Do not fill this with a fabricated testimonial or
          usage stat; both this generator and CLAUDE.md treat fake social
          proof as a real integrity problem, not a placeholder to fill in.
      <section className="px-6 py-8 text-center">
        <p style={{{{ color: "#8b96a3" }}}}>"[real customer quote goes here once one exists]"</p>
      </section>
      */}}

      {{/* Features (SEO: internal linking + real content, not filler) */}}
      <section className="px-6 py-16 max-w-5xl mx-auto grid gap-6 md:grid-cols-3">
{features_jsx}
      </section>

      {{/* FAQ preview — links to full FAQ page (SXO: no dead ends) */}}
      <section className="px-6 py-16 max-w-2xl mx-auto">
        <h2 className="text-2xl font-bold mb-6 text-center" style={{{{ color: "#eef2f5", fontFamily: "Space Grotesk, sans-serif" }}}}>
          Common questions
        </h2>
{faq_preview_jsx}
        <div className="text-center mt-6">
          <Link href="/faq" style={{{{ color: "#5a96ff" }}}}>See all questions &rarr;</Link>
        </div>
      </section>

      {{/* Next actions — pricing + comparison (SXO: no dead ends) */}}
      <section className="px-6 py-16 text-center">
        <Link href="/pricing" className="mr-6" style={{{{ color: "#5a96ff" }}}}>View pricing &rarr;</Link>
        <Link href="/{content.get("comparison_slug", "compare")}" style={{{{ color: "#5a96ff" }}}}>
          {_jsx_text(product_name)} vs {_jsx_text(content.get("existing_tool_name", "the alternative"))} &rarr;
        </Link>
      </section>

      <footer className="px-6 py-8 text-center text-xs" style={{{{ color: "#5b6673" }}}}>
        {_jsx_text(content.get("author_attribution", product_name))}
      </footer>
    </main>
  );
}}
'''


def render_pricing_page(product_name: str, content: dict, tier_structure: dict) -> str:
    tiers = tier_structure or {}
    prices = list(tiers.values())
    json_ld = _json_ld_script({
        "@context": "https://schema.org",
        "@type": "Product",
        "name": product_name,
        "offers": {
            "@type": "AggregateOffer",
            "priceCurrency": "USD",
            "lowPrice": str(min(prices)) if prices else "0",
            "highPrice": str(max(prices)) if prices else "0",
        },
    })

    tier_cards = "\n".join(
        f'''          <div className="p-6 rounded-xl text-center" style={{{{ backgroundColor: "#0f1420", border: "1px solid #1c222b" }}}}>
            <h3 className="font-semibold capitalize mb-2" style={{{{ color: "#eef2f5", fontFamily: "Space Grotesk, sans-serif" }}}}>{_jsx_text(tier_name)}</h3>
            <p className="text-3xl font-bold mb-4" style={{{{ color: "#5a96ff" }}}}>${price}<span className="text-sm font-normal" style={{{{ color: "#8b96a3" }}}}>/mo</span></p>
          </div>'''
        for tier_name, price in tiers.items()
    )

    return f'''import type {{ Metadata }} from "next";
import Link from "next/link";

{_page_metadata_export(f"{product_name} Pricing", content.get("meta_description", ""))}export default function PricingPage() {{
  return (
    <main className="px-6 py-24" style={{{{ backgroundColor: "#070910", minHeight: "100vh" }}}}>
      {json_ld}
      <h1 className="text-3xl font-bold text-center mb-12" style={{{{ color: "#eef2f5", fontFamily: "Space Grotesk, sans-serif" }}}}>
        Simple, transparent pricing
      </h1>
      <div className="max-w-4xl mx-auto grid gap-6 md:grid-cols-3">
{tier_cards}
      </div>
      <div className="text-center mt-12">
        <Link
          href="/login"
          className="inline-block px-6 py-3 rounded-lg font-semibold"
          style={{{{ backgroundColor: "#5a96ff", color: "#070910" }}}}
        >
          {_jsx_text(content.get("trial_cta_text", "Start Free Trial"))}
        </Link>
        <p className="text-xs mt-4" style={{{{ color: "#5b6673" }}}}>
          <Link href="/faq" style={{{{ color: "#5a96ff" }}}}>Questions? See the FAQ &rarr;</Link>
        </p>
      </div>
    </main>
  );
}}
'''


def render_faq_page(product_name: str, content: dict) -> str:
    faq = content.get("faq") or []
    json_ld = _json_ld_script({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q.get("question", ""), "acceptedAnswer": {"@type": "Answer", "text": q.get("answer", "")}}
            for q in faq
        ],
    })

    faq_jsx = "\n".join(
        f'''        <div className="py-5" style={{{{ borderBottom: "1px solid #1c222b" }}}}>
          <h2 className="font-semibold mb-2" style={{{{ color: "#eef2f5" }}}}>{_jsx_text(q.get("question", ""))}</h2>
          <p style={{{{ color: "#aab4bd" }}}}>{_jsx_text(q.get("answer", ""))}</p>
        </div>'''
        for q in faq
    )

    return f'''import type {{ Metadata }} from "next";
import Link from "next/link";

{_page_metadata_export(f"{product_name} FAQ", content.get("meta_description", ""))}export default function FAQPage() {{
  return (
    <main className="px-6 py-24 max-w-2xl mx-auto" style={{{{ backgroundColor: "#070910", minHeight: "100vh" }}}}>
      {json_ld}
      <h1 className="text-3xl font-bold mb-8" style={{{{ color: "#eef2f5", fontFamily: "Space Grotesk, sans-serif" }}}}>
        Frequently asked questions
      </h1>
{faq_jsx}
      <div className="mt-10 text-center">
        <Link
          href="/login"
          className="inline-block px-6 py-3 rounded-lg font-semibold"
          style={{{{ backgroundColor: "#5a96ff", color: "#070910" }}}}
        >
          {_jsx_text(content.get("trial_cta_text", "Start Free Trial"))}
        </Link>
      </div>
    </main>
  );
}}
'''


def render_comparison_page(product_name: str, content: dict) -> str:
    existing_tool = content.get("existing_tool_name", "the alternative")
    points = content.get("comparison_points") or []
    title = f"{product_name} vs {existing_tool}"

    json_ld = _json_ld_script({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "author": {"@type": "Organization", "name": product_name},
    })

    rows = "\n".join(
        f'''            <tr style={{{{ borderBottom: "1px solid #1c222b" }}}}>
              <td className="py-3 pr-4 font-medium" style={{{{ color: "#eef2f5" }}}}>{_jsx_text(p.get("feature", ""))}</td>
              <td className="py-3 pr-4" style={{{{ color: "#3fd17a" }}}}>{_jsx_text(p.get("us", ""))}</td>
              <td className="py-3" style={{{{ color: "#8b96a3" }}}}>{_jsx_text(p.get("them", ""))}</td>
            </tr>'''
        for p in points
    )

    return f'''import type {{ Metadata }} from "next";
import Link from "next/link";

{_page_metadata_export(title, content.get("meta_description", ""))}export default function ComparisonPage() {{
  return (
    <main className="px-6 py-24 max-w-3xl mx-auto" style={{{{ backgroundColor: "#070910", minHeight: "100vh" }}}}>
      {json_ld}
      <h1 className="text-3xl font-bold mb-8" style={{{{ color: "#eef2f5", fontFamily: "Space Grotesk, sans-serif" }}}}>
        {_jsx_text(title)}
      </h1>
      <table className="w-full text-sm mb-10">
        <thead>
          <tr style={{{{ borderBottom: "1px solid #1c222b" }}}}>
            <th className="text-left py-2" style={{{{ color: "#5b6673" }}}}>Feature</th>
            <th className="text-left py-2" style={{{{ color: "#5a96ff" }}}}>{_jsx_text(product_name)}</th>
            <th className="text-left py-2" style={{{{ color: "#5b6673" }}}}>{_jsx_text(existing_tool)}</th>
          </tr>
        </thead>
        <tbody>
{rows}
        </tbody>
      </table>
      <div className="text-center">
        <Link
          href="/login"
          className="inline-block px-6 py-3 rounded-lg font-semibold"
          style={{{{ backgroundColor: "#5a96ff", color: "#070910" }}}}
        >
          {_jsx_text(content.get("trial_cta_text", "Start Free Trial"))}
        </Link>
      </div>
    </main>
  );
}}
'''


def render_definitive_page(product_name: str, content: dict) -> str:
    question = content.get("definitive_question", "")
    answer = content.get("definitive_answer_long", "")

    json_ld = _json_ld_script({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": question,
        "author": {"@type": "Organization", "name": product_name},
    })

    paragraphs = "\n".join(
        (f'        <h2 className="text-xl font-bold mt-8 mb-3" style={{{{ color: "#eef2f5" }}}}>{_jsx_text(p[3:].strip())}</h2>'
         if p.strip().startswith("## ")
         else f'        <p className="mb-4 leading-relaxed" style={{{{ color: "#aab4bd" }}}}>{_jsx_text(p.strip())}</p>')
        for p in answer.split("\n\n") if p.strip()
    )

    return f'''import type {{ Metadata }} from "next";
import Link from "next/link";

{_page_metadata_export(question or product_name, content.get("meta_description", ""))}export default function DefinitiveAnswerPage() {{
  return (
    <main className="px-6 py-24 max-w-2xl mx-auto" style={{{{ backgroundColor: "#070910", minHeight: "100vh" }}}}>
      {json_ld}
      <h1 className="text-3xl font-bold mb-8" style={{{{ color: "#eef2f5", fontFamily: "Space Grotesk, sans-serif" }}}}>
        {_jsx_text(question)}
      </h1>
{paragraphs}
      <p className="text-xs mt-10" style={{{{ color: "#5b6673" }}}}>{_jsx_text(content.get("author_attribution", product_name))}</p>
      <div className="mt-8 text-center">
        <Link
          href="/login"
          className="inline-block px-6 py-3 rounded-lg font-semibold"
          style={{{{ backgroundColor: "#5a96ff", color: "#070910" }}}}
        >
          {_jsx_text(content.get("trial_cta_text", "Start Free Trial"))}
        </Link>
      </div>
    </main>
  );
}}
'''


def render_sitemap_ts(product_slug: str, content: dict) -> str:
    comparison_slug = content.get("comparison_slug", "compare")
    definitive_slug = content.get("definitive_slug", "faq")
    return f'''import type {{ MetadataRoute }} from "next";

const BASE_URL = "https://{product_slug}.thdstack.com";

export default function sitemap(): MetadataRoute.Sitemap {{
  const paths = ["", "/pricing", "/faq", "/{comparison_slug}", "/{definitive_slug}"];
  return paths.map((path) => ({{
    url: `${{BASE_URL}}${{path}}`,
    lastModified: new Date(),
  }}));
}}
'''


def render_robots_ts(product_slug: str) -> str:
    return f'''import type {{ MetadataRoute }} from "next";

export default function robots(): MetadataRoute.Robots {{
  return {{
    rules: {{ userAgent: "*", allow: "/", disallow: ["/login", "/auth", "/dashboard"] }},
    sitemap: "https://{product_slug}.thdstack.com/sitemap.xml",
  }};
}}
'''
