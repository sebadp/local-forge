"""Tests for web_research composite tool (Plan 51)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("ddgs", reason="ddgs not installed")

from app.skills.models import ToolCall
from app.skills.registry import SkillRegistry
from app.skills.tools.search_tools import (
    _dedup_urls,
    _format_research_output,
    _generate_retry_variant,
    _generate_search_variant,
    register,
)
from app.skills.tools.web_extraction import _cosine_similarity, chunk_text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_ollama() -> MagicMock:
    client = MagicMock()
    client._model = "qwen3.5:9b"
    client.chat = AsyncMock(return_value="Extracted info")

    # Mock embed: return deterministic vectors based on input length
    async def _fake_embed(texts, model=None):
        vecs = []
        for i, _t in enumerate(texts):
            # First text (query) gets [1,0,0,...], chunks get increasingly different vectors
            vec = [0.0] * 10
            if i == 0:
                vec[0] = 1.0
                vec[1] = 0.8
            else:
                vec[0] = max(0.0, 1.0 - i * 0.1)
                vec[1] = max(0.0, 0.9 - i * 0.05)
                vec[2] = i * 0.1
            vecs.append(vec)
        return vecs

    client.embed = _fake_embed
    return client


def _mock_settings(**overrides):
    defaults = {
        "web_search_fetch_top_n": 3,
        "web_search_fetch_timeout": 8.0,
        "web_search_extract_page_limit": 2500,
        "web_research_max_pages": 8,
        "web_research_fetch_timeout": 8.0,
        "web_research_max_concurrent": 6,
        "web_research_chunk_size": 1500,
        "web_research_top_k": 8,
        "web_research_similarity_threshold": 0.0,
        "web_research_max_output_chars": 12000,
        "embedding_model": "nomic-embed-text",
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


def _make_registry(ollama_client=None, settings=None):
    reg = SkillRegistry(skills_dir="/nonexistent")
    register(reg, ollama_client=ollama_client, settings=settings)
    return reg


_MOCK_RESULTS = [
    {"title": "Result 1", "href": "http://example.com/1", "body": "Snippet 1"},
    {"title": "Result 2", "href": "http://example.com/2", "body": "Snippet 2"},
    {"title": "Result 3", "href": "http://example.com/3", "body": "Snippet 3"},
    {"title": "Result 4", "href": "http://example.com/4", "body": "Snippet 4"},
]


# ---------------------------------------------------------------------------
# Phase 2: chunk_text
# ---------------------------------------------------------------------------


class TestChunkText:
    def test_split_by_headings(self):
        text = (
            "Intro paragraph with enough content to exceed min chunk size easily.\n"
            "## Section A\n"
            "Content A with enough detail to be a standalone chunk in the output.\n"
            "## Section B\n"
            "Content B with enough detail to be a standalone chunk in the output."
        )
        chunks = chunk_text(text, max_chunk_chars=120)
        assert len(chunks) >= 2
        assert any("Section A" in c for c in chunks)
        assert any("Section B" in c for c in chunks)

    def test_fallback_to_paragraphs(self):
        text = "Paragraph one with enough text to pass the filter.\n\nParagraph two with enough text to pass the filter."
        chunks = chunk_text(text, max_chunk_chars=60)
        assert len(chunks) == 2

    def test_merges_small_chunks(self):
        text = "## A\nSmall A.\n## B\nSmall B.\n## C\nSmall C."
        chunks = chunk_text(text, max_chunk_chars=5000)
        # Small chunks should be merged into one
        assert len(chunks) <= 2

    def test_hard_split_oversized(self):
        text = "x" * 3000
        chunks = chunk_text(text, max_chunk_chars=1000)
        assert len(chunks) == 3
        assert all(len(c) <= 1000 for c in chunks)

    def test_filters_tiny_chunks(self):
        text = "Hi\n\nVery long paragraph that is meaningful and has enough content to pass filter."
        chunks = chunk_text(text, max_chunk_chars=5000)
        # "Hi" (2 chars) should be filtered out
        assert all(len(c) >= 50 for c in chunks)

    def test_empty_text(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []


# ---------------------------------------------------------------------------
# Phase 2: cosine similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert _cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_zero_vector(self):
        assert _cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0


# ---------------------------------------------------------------------------
# Phase 2: rank_chunks
# ---------------------------------------------------------------------------


class TestRankChunks:
    async def test_ranks_by_similarity(self):
        from app.skills.tools.web_extraction import rank_chunks

        client = _mock_ollama()
        chunks = [
            ("Very relevant content about the query topic.", "http://a.com"),
            ("Somewhat related content here.", "http://b.com"),
            ("Totally different topic content.", "http://c.com"),
        ]
        result = await rank_chunks("query topic", chunks, client, top_k=3, similarity_threshold=0.0)
        assert len(result) == 3
        # Results should be sorted by similarity descending
        sims = [sim for _, _, sim in result]
        assert sims == sorted(sims, reverse=True)

    async def test_filters_below_threshold(self):
        from app.skills.tools.web_extraction import rank_chunks

        client = MagicMock()

        async def _low_embed(texts, model=None):
            vecs = []
            for i, _ in enumerate(texts):
                vec = [0.0] * 10
                if i == 0:
                    vec[0] = 1.0  # query
                else:
                    vec[5] = 1.0  # chunks are orthogonal to query
                vecs.append(vec)
            return vecs

        client.embed = _low_embed
        chunks = [("Content A.", "http://a.com"), ("Content B.", "http://b.com")]
        result = await rank_chunks("query", chunks, client, top_k=5, similarity_threshold=0.9)
        # Orthogonal vectors → similarity ~0 → filtered out
        assert len(result) == 0

    async def test_empty_chunks(self):
        from app.skills.tools.web_extraction import rank_chunks

        client = _mock_ollama()
        result = await rank_chunks("query", [], client)
        assert result == []


# ---------------------------------------------------------------------------
# Phase 4: search variant + dedup
# ---------------------------------------------------------------------------


class TestSearchVariant:
    def test_adds_year_if_missing(self):
        variant = _generate_search_variant("fixture Rosario Central")
        # Should contain a 4-digit year
        assert any(w.isdigit() and len(w) == 4 for w in variant.split())

    def test_year_already_present(self):
        variant = _generate_search_variant("fixture 2026 Central")
        # Should NOT add another year
        years = [w for w in variant.split() if w.isdigit() and len(w) == 4]
        assert len(years) == 1

    def test_rotates_words(self):
        variant = _generate_search_variant("first second third")
        words = variant.split()
        # First word should now be somewhere else
        assert words[0] != "first" or len(words) > 3

    def test_different_from_original(self):
        original = "fixture Rosario Central"
        variant = _generate_search_variant(original)
        assert variant != original

    def test_retry_variant_different(self):
        query = "fixture Rosario Central"
        v1 = _generate_search_variant(query)
        v2 = _generate_retry_variant(query)
        assert v1 != v2


class TestDedupUrls:
    def test_removes_duplicates(self):
        results = [
            {"href": "http://example.com/page"},
            {"href": "http://example.com/page"},
            {"href": "http://other.com/page"},
        ]
        assert len(_dedup_urls(results)) == 2

    def test_normalizes_trailing_slash(self):
        results = [
            {"href": "http://example.com/page/"},
            {"href": "http://example.com/page"},
        ]
        assert len(_dedup_urls(results)) == 1

    def test_normalizes_query_params(self):
        results = [
            {"href": "http://example.com/page?utm_source=google"},
            {"href": "http://example.com/page"},
        ]
        assert len(_dedup_urls(results)) == 1

    def test_empty_results(self):
        assert _dedup_urls([]) == []

    def test_preserves_original_url(self):
        results = [{"href": "http://example.com/page?id=1"}]
        urls = _dedup_urls(results)
        # Original URL (with params) is preserved for fetching
        assert urls[0] == "http://example.com/page?id=1"


# ---------------------------------------------------------------------------
# Phase 7: output formatting
# ---------------------------------------------------------------------------


class TestFormatOutput:
    def test_basic_format(self):
        chunks = [
            ("Content A about the topic.", "http://a.com", 0.9),
            ("Content B related info.", "http://b.com", 0.7),
        ]
        output = _format_research_output("test query", chunks, 5)
        assert '## Results from web research: "test query"' in output
        assert "### Source: http://a.com" in output
        assert "### Source: http://b.com" in output
        assert "5 sources analyzed" in output
        assert "2 relevant sections found" in output

    def test_respects_char_limit(self):
        # Create chunks that exceed the limit
        chunks = [("x" * 500, f"http://example.com/{i}", 0.9 - i * 0.1) for i in range(20)]
        output = _format_research_output("q", chunks, 10, max_chars=2000)
        assert len(output) <= 2000

    def test_empty_chunks(self):
        output = _format_research_output("test", [], 0)
        assert "No relevant content found" in output


# ---------------------------------------------------------------------------
# Phase 3+: web_research tool registration
# ---------------------------------------------------------------------------


class TestWebResearchRegistration:
    def test_tool_registered(self):
        reg = _make_registry(ollama_client=_mock_ollama(), settings=_mock_settings())
        tool = reg.get_tool("web_research")
        assert tool is not None
        assert "query" in tool.parameters["properties"]

    def test_description_mentions_deep(self):
        reg = _make_registry(ollama_client=_mock_ollama(), settings=_mock_settings())
        tool = reg.get_tool("web_research")
        assert "Deep" in tool.description or "deep" in tool.description.lower()


# ---------------------------------------------------------------------------
# Phase 3+: web_research pipeline integration
# ---------------------------------------------------------------------------

_LONG_CONTENT = "This is page content with enough text to pass filters. " * 20


class TestWebResearchPipeline:
    async def test_full_pipeline(self):
        client = _mock_ollama()
        reg = _make_registry(ollama_client=client, settings=_mock_settings())

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=None),
            patch(
                "app.skills.tools.web_extraction.fetch_and_extract",
                side_effect=[
                    ("http://example.com/1", _LONG_CONTENT),
                    ("http://example.com/2", _LONG_CONTENT),
                    ("http://example.com/3", None),
                    ("http://example.com/4", _LONG_CONTENT),
                ],
            ),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(name="web_research", arguments={"query": "fixture Central"})
            )

        assert result.success
        assert "Results from web research" in result.content
        assert "Source:" in result.content

    async def test_no_search_results(self):
        reg = _make_registry(ollama_client=_mock_ollama(), settings=_mock_settings())

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=None),
        ):
            MockDDGS.return_value.text.return_value = []
            result = await reg.execute_tool(
                ToolCall(name="web_research", arguments={"query": "nothing"})
            )

        assert "No results found" in result.content

    async def test_no_successful_fetches(self):
        reg = _make_registry(ollama_client=_mock_ollama(), settings=_mock_settings())

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=None),
            patch(
                "app.skills.tools.web_extraction.fetch_and_extract",
                side_effect=[
                    ("http://example.com/1", None),
                    ("http://example.com/2", None),
                ],
            ),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS[:2]
            result = await reg.execute_tool(
                ToolCall(name="web_research", arguments={"query": "test"})
            )

        assert result.success
        assert "Could not fetch" in result.content

    async def test_search_error_handled(self):
        reg = _make_registry(ollama_client=_mock_ollama(), settings=_mock_settings())

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=None),
        ):
            MockDDGS.return_value.text.side_effect = Exception("Rate limited")
            result = await reg.execute_tool(
                ToolCall(name="web_research", arguments={"query": "fail"})
            )

        assert result.success  # tool doesn't raise
        # Either error message or "no results" — depends on whether both searches fail
        assert "No results found" in result.content or "Error" in result.content

    async def test_respects_max_pages(self):
        client = _mock_ollama()
        settings = _mock_settings(web_research_max_pages=2)
        reg = _make_registry(ollama_client=client, settings=settings)

        fetch_calls: list[str] = []

        async def mock_fae(url, timeout=8.0):
            fetch_calls.append(url)
            return (url, _LONG_CONTENT)

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=None),
            patch("app.skills.tools.web_extraction.fetch_and_extract", side_effect=mock_fae),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            await reg.execute_tool(ToolCall(name="web_research", arguments={"query": "test"}))

        # Should only fetch max_pages=2 URLs
        assert len(fetch_calls) <= 2

    async def test_without_ollama_uses_unranked(self):
        """Without ollama_client, chunks are returned unranked."""
        reg = _make_registry(ollama_client=None, settings=_mock_settings())

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=None),
            patch(
                "app.skills.tools.web_extraction.fetch_and_extract",
                side_effect=[
                    ("http://example.com/1", _LONG_CONTENT),
                    ("http://example.com/2", _LONG_CONTENT),
                ],
            ),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS[:2]
            result = await reg.execute_tool(
                ToolCall(name="web_research", arguments={"query": "test"})
            )

        assert result.success
        assert "Results from web research" in result.content


# ---------------------------------------------------------------------------
# Phase 9: Langfuse observability
# ---------------------------------------------------------------------------


class TestWebResearchObservability:
    async def test_creates_expected_spans(self):
        client = _mock_ollama()
        reg = _make_registry(ollama_client=client, settings=_mock_settings())

        mock_trace = MagicMock()
        span_names: list[str] = []
        span_kinds: list[str] = []

        class FakeSpan:
            def __init__(self, name, kind, parent_id=None):
                self.span_id = f"span_{name}"
                span_names.append(name)
                span_kinds.append(kind)

            def set_input(self, data):
                pass

            def set_output(self, data):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        def fake_span(name, kind="span", parent_id=None):
            return FakeSpan(name, kind, parent_id)

        mock_trace.span = fake_span

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=mock_trace),
            patch(
                "app.skills.tools.web_extraction.fetch_and_extract",
                side_effect=[
                    ("http://example.com/1", _LONG_CONTENT),
                    ("http://example.com/2", None),
                    ("http://example.com/3", _LONG_CONTENT),
                ],
            ),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS[:3]
            result = await reg.execute_tool(
                ToolCall(name="web_research", arguments={"query": "test"})
            )

        assert result.success
        assert "web_research:pipeline" in span_names
        assert "web_research:search" in span_names
        assert "web_research:fetch" in span_names
        assert "web_research:rank" in span_names
        # All should be kind="span"
        for name in [
            "web_research:pipeline",
            "web_research:search",
            "web_research:fetch",
            "web_research:rank",
        ]:
            idx = span_names.index(name)
            assert span_kinds[idx] == "span"


# ---------------------------------------------------------------------------
# Phase 1: Router auto-include fetch
# ---------------------------------------------------------------------------


class TestRouterAutoIncludeFetch:
    def test_search_auto_includes_fetch_when_available(self):
        from app.skills.router import TOOL_CATEGORIES, select_tools

        # Temporarily add a "fetch" category
        original = TOOL_CATEGORIES.get("fetch")
        TOOL_CATEGORIES["fetch"] = ["puppeteer_navigate"]
        try:
            all_tools = {
                "web_search": {"type": "function", "function": {"name": "web_search"}},
                "web_research": {"type": "function", "function": {"name": "web_research"}},
                "puppeteer_navigate": {
                    "type": "function",
                    "function": {"name": "puppeteer_navigate"},
                },
            }
            selected = select_tools(["search"], all_tools, max_tools=10)
            names = [t["function"]["name"] for t in selected]
            assert "puppeteer_navigate" in names
        finally:
            if original is None:
                TOOL_CATEGORIES.pop("fetch", None)
            else:
                TOOL_CATEGORIES["fetch"] = original

    def test_search_without_fetch_category_works(self):
        """When fetch category doesn't exist, no error."""
        from app.skills.router import TOOL_CATEGORIES, select_tools

        original = TOOL_CATEGORIES.pop("fetch", None)
        try:
            all_tools = {
                "web_search": {"type": "function", "function": {"name": "web_search"}},
                "web_research": {"type": "function", "function": {"name": "web_research"}},
            }
            selected = select_tools(["search"], all_tools, max_tools=10)
            names = [t["function"]["name"] for t in selected]
            assert "web_search" in names
        finally:
            if original is not None:
                TOOL_CATEGORIES["fetch"] = original

    def test_web_research_in_search_category(self):
        from app.skills.router import TOOL_CATEGORIES

        assert "web_research" in TOOL_CATEGORIES["search"]
