from core.naming import derive_product_name


def test_takes_first_line_when_llm_ignores_instructions():
    # Real 2026-07-17 failure: Sonnet returned reasoning after the name
    # despite being told to respond with nothing else.
    raw = "1099 Autotrack\n\nWait, that's too long, let me reconsider..."
    assert derive_product_name("some concept", llm_analyze=lambda s, u: raw) == "1099 Autotrack"


def test_strips_surrounding_quotes():
    assert derive_product_name("x", llm_analyze=lambda s, u: '"Freight Audit Copilot"') == "Freight Audit Copilot"


def test_caps_length_even_on_a_single_verbose_line():
    long_line = "A Very Long Product Name That Somehow Ignored The Two To Four Word Instruction Entirely"
    name = derive_product_name("x", llm_analyze=lambda s, u: long_line)
    assert len(name) <= 60


def test_empty_response_falls_back():
    assert derive_product_name("x", llm_analyze=lambda s, u: "   \n  ") == "Untitled Product"
