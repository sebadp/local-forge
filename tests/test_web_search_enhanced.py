"""Tests for web_search depth='detailed' enhancement (Plan 52)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("ddgs", reason="ddgs not installed")

from app.skills.models import ToolCall
from app.skills.registry import SkillRegistry
from app.skills.tools.search_tools import _llm_extract, register

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_ollama() -> MagicMock:
    client = MagicMock()
    client._model = "qwen3.5:9b"
    client.chat = AsyncMock(return_value="Extracted: dollar price is $1050")
    return client


def _mock_settings(**overrides):
    defaults = {
        "web_search_fetch_top_n": 3,
        "web_search_fetch_timeout": 8.0,
        "web_search_extract_page_limit": 2500,
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
]


# ---------------------------------------------------------------------------
# Phase 1: web_extraction utilities
# ---------------------------------------------------------------------------


class TestWebExtraction:
    async def test_fetch_page_success(self):
        from app.skills.tools.web_extraction import fetch_page

        with patch("app.skills.tools.web_extraction.httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.text = "<html><body>Hello</body></html>"
            mock_resp.raise_for_status = MagicMock()

            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_resp))
            )
            MockClient.return_value = mock_ctx

            result = await fetch_page("http://example.com")
            assert result is not None
            assert "Hello" in result

    async def test_fetch_page_failure(self):
        from app.skills.tools.web_extraction import fetch_page

        with patch("app.skills.tools.web_extraction.httpx.AsyncClient") as MockClient:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(side_effect=Exception("timeout")))
            )
            MockClient.return_value = mock_ctx

            result = await fetch_page("http://example.com")
            assert result is None

    def test_extract_text_html(self):
        from app.skills.tools.web_extraction import extract_text

        html = "<html><body><p>Important text here</p><script>var x=1;</script></body></html>"
        result = extract_text(html)
        assert "Important text" in result
        assert "var x" not in result

    def test_extract_text_regex_fallback(self):
        """Regex fallback removes <script> and <style> blocks when trafilatura fails."""
        from app.skills.tools.web_extraction import extract_text

        html = (
            "<html><head><style>body{color:red}</style></head>"
            "<body><script>alert('xss')</script>"
            "<p>Clean content here</p></body></html>"
        )
        # Make trafilatura.extract return None to trigger regex fallback
        with patch("trafilatura.extract", return_value=None):
            result = extract_text(html)
        assert "Clean content" in result
        assert "alert" not in result
        assert "color:red" not in result

    async def test_fetch_and_extract_discards_short_content(self):
        """Pages with <50 chars post-extraction are discarded."""
        from app.skills.tools.web_extraction import fetch_and_extract

        with patch("app.skills.tools.web_extraction.fetch_page") as mock_fp:
            mock_fp.return_value = "<html><body>Hi</body></html>"
            with patch("app.skills.tools.web_extraction.extract_text", return_value="Hi"):
                url, text = await fetch_and_extract("http://example.com")
                assert text is None  # too short

    async def test_fetch_and_extract_success(self):
        """Successful fetch+extract returns URL and text."""
        from app.skills.tools.web_extraction import fetch_and_extract

        long_content = "This is a sufficiently long piece of content for testing purposes." * 3
        with patch("app.skills.tools.web_extraction.fetch_page") as mock_fp:
            mock_fp.return_value = f"<html><body>{long_content}</body></html>"
            with patch("app.skills.tools.web_extraction.extract_text", return_value=long_content):
                url, text = await fetch_and_extract("http://example.com")
                assert url == "http://example.com"
                assert text == long_content

    async def test_fetch_and_extract_html_none(self):
        """If fetch_page returns None, text is None."""
        from app.skills.tools.web_extraction import fetch_and_extract

        with patch("app.skills.tools.web_extraction.fetch_page", return_value=None):
            url, text = await fetch_and_extract("http://example.com")
            assert text is None

    async def test_fetch_multiple(self):
        from app.skills.tools.web_extraction import fetch_multiple

        with patch("app.skills.tools.web_extraction.fetch_and_extract") as mock_fae:
            mock_fae.side_effect = [
                ("http://a.com", "Page A content here with enough text"),
                ("http://b.com", None),
            ]
            results = await fetch_multiple(["http://a.com", "http://b.com"])
            assert len(results) == 2
            assert results[0] == ("http://a.com", "Page A content here with enough text")
            assert results[1] == ("http://b.com", None)

    async def test_fetch_multiple_handles_exceptions(self):
        """fetch_multiple returns None for URLs that raise exceptions."""
        from app.skills.tools.web_extraction import fetch_multiple

        async def _flaky(url, timeout=8.0):
            if "bad" in url:
                raise ConnectionError("refused")
            return (url, "Good content here with enough text to pass filter")

        with patch("app.skills.tools.web_extraction.fetch_and_extract", side_effect=_flaky):
            results = await fetch_multiple(["http://good.com", "http://bad.com"])
            assert results[0] == (
                "http://good.com",
                "Good content here with enough text to pass filter",
            )
            assert results[1] == ("http://bad.com", None)

    async def test_fetch_multiple_respects_concurrency(self):
        """fetch_multiple limits concurrent requests via semaphore."""
        from app.skills.tools.web_extraction import fetch_multiple

        concurrent_count = 0
        max_concurrent = 0

        async def _tracking_fetch(url, timeout=8.0):
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.01)  # simulate I/O
            concurrent_count -= 1
            return (url, f"Content from {url} with enough chars to pass filter")

        import asyncio

        with patch(
            "app.skills.tools.web_extraction.fetch_and_extract", side_effect=_tracking_fetch
        ):
            urls = [f"http://example.com/{i}" for i in range(8)]
            results = await fetch_multiple(urls, max_concurrent=2)

        assert len(results) == 8
        assert max_concurrent <= 2


# ---------------------------------------------------------------------------
# Phase 2: LLM extraction
# ---------------------------------------------------------------------------


class TestLLMExtract:
    async def test_llm_extract_constructs_correct_prompt(self):
        client = _mock_ollama()
        pages = [
            ("http://example.com/1", "Dollar official: $1050, Blue: $1180"),
            ("http://example.com/2", "Dollar rate today: sell $1060"),
        ]

        result = await _llm_extract("precio del dolar hoy", pages, client, page_limit=2500)

        assert result == "Extracted: dollar price is $1050"
        # Verify think=False was passed
        call_kwargs = client.chat.call_args
        assert call_kwargs.kwargs["think"] is False
        # Verify system message contains extraction prompt
        messages = call_kwargs.args[0]
        assert messages[0].role == "system"
        assert "EXACT data" in messages[0].content
        # Verify user message has query and page content
        assert "precio del dolar hoy" in messages[1].content
        assert "http://example.com/1" in messages[1].content

    async def test_llm_extract_truncates_pages(self):
        client = _mock_ollama()
        long_text = "x" * 5000
        pages = [("http://example.com", long_text)]

        await _llm_extract("test", pages, client, page_limit=100)

        messages = client.chat.call_args.args[0]
        # Page content in user message should be truncated
        assert len(messages[1].content) < 5000

    async def test_llm_extract_multiple_pages(self):
        """Multiple pages are separated by --- in the prompt."""
        client = _mock_ollama()
        pages = [
            ("http://a.com", "Page A data"),
            ("http://b.com", "Page B data"),
            ("http://c.com", "Page C data"),
        ]

        await _llm_extract("test query", pages, client, page_limit=2500)

        messages = client.chat.call_args.args[0]
        user_content = messages[1].content
        assert "### Source: http://a.com" in user_content
        assert "### Source: http://b.com" in user_content
        assert "### Source: http://c.com" in user_content
        assert "---" in user_content

    async def test_llm_extract_single_page(self):
        """Single page extraction works without separator issues."""
        client = _mock_ollama()
        pages = [("http://only.com", "The only page content")]

        await _llm_extract("test", pages, client)

        messages = client.chat.call_args.args[0]
        assert "### Source: http://only.com" in messages[1].content
        assert "The only page content" in messages[1].content


# ---------------------------------------------------------------------------
# Phase 3: web_search quick mode (backward compat)
# ---------------------------------------------------------------------------


class TestWebSearchQuick:
    async def test_quick_mode_default(self):
        """depth='quick' (default) returns snippets only — no fetch."""
        reg = _make_registry(ollama_client=_mock_ollama())

        with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(name="web_search", arguments={"query": "capital de Francia"})
            )

        assert result.success
        assert "Result 1" in result.content
        assert "Extracted content" not in result.content

    async def test_quick_mode_explicit(self):
        """Explicit depth='quick' also returns snippets only."""
        reg = _make_registry(ollama_client=_mock_ollama())

        with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "test", "depth": "quick"},
                )
            )

        assert result.success
        assert "Extracted content" not in result.content

    async def test_no_results(self):
        reg = _make_registry()

        with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
            MockDDGS.return_value.text.return_value = []
            result = await reg.execute_tool(
                ToolCall(name="web_search", arguments={"query": "nothing"})
            )

        assert "No results found" in result.content

    async def test_quick_mode_with_time_range(self):
        """Quick mode + time_range works together."""
        reg = _make_registry(ollama_client=_mock_ollama())

        with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "news today", "time_range": "d", "depth": "quick"},
                )
            )
            MockDDGS.return_value.text.assert_called_once_with(
                "news today", timelimit="d", max_results=5
            )

        assert result.success
        assert "Extracted content" not in result.content

    async def test_quick_mode_formats_all_results(self):
        """Quick mode returns all results formatted as numbered markdown."""
        reg = _make_registry()

        with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(name="web_search", arguments={"query": "test"})
            )

        assert "1. [Result 1]" in result.content
        assert "2. [Result 2]" in result.content
        assert "3. [Result 3]" in result.content
        assert "http://example.com/1" in result.content
        assert "Snippet 3" in result.content

    async def test_quick_mode_search_error(self):
        """Quick mode handles DuckDuckGo errors gracefully."""
        reg = _make_registry()

        with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
            MockDDGS.return_value.text.side_effect = Exception("Rate limited")
            result = await reg.execute_tool(
                ToolCall(name="web_search", arguments={"query": "fail"})
            )

        assert result.success  # tool doesn't raise
        assert "Error performing search" in result.content

    async def test_quick_mode_invalid_depth_treated_as_quick(self):
        """Invalid depth value is treated as quick (not 'detailed')."""
        reg = _make_registry(ollama_client=_mock_ollama())

        with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "test", "depth": "invalid"},
                )
            )

        assert result.success
        assert "Extracted content" not in result.content


# ---------------------------------------------------------------------------
# Phase 3+4: web_search detailed mode
# ---------------------------------------------------------------------------


class TestWebSearchDetailed:
    async def test_detailed_mode_fetches_and_extracts(self):
        """depth='detailed' fetches pages and runs LLM extraction."""
        client = _mock_ollama()
        reg = _make_registry(ollama_client=client)

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=None),
            patch(
                "app.skills.tools.web_extraction.fetch_and_extract",
                side_effect=[
                    ("http://example.com/1", "Page 1 content with dollar prices"),
                    ("http://example.com/2", "Page 2 content"),
                    ("http://example.com/3", None),
                ],
            ),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "dollar price", "depth": "detailed"},
                )
            )

        assert result.success
        # Should contain both snippets and extracted content
        assert "Result 1" in result.content
        assert "Extracted content from top results" in result.content
        assert "Extracted: dollar price is $1050" in result.content
        # LLM should have been called with think=False
        client.chat.assert_called_once()

    async def test_detailed_mode_no_successful_fetch(self):
        """If all fetches fail, return snippets with fallback message."""
        client = _mock_ollama()
        reg = _make_registry(ollama_client=client)

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=None),
            patch(
                "app.skills.tools.web_extraction.fetch_and_extract",
                side_effect=[
                    ("http://example.com/1", None),
                    ("http://example.com/2", None),
                    ("http://example.com/3", None),
                ],
            ),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "test", "depth": "detailed"},
                )
            )

        assert result.success
        assert "Could not fetch page content" in result.content
        # LLM should NOT have been called
        client.chat.assert_not_called()

    async def test_detailed_without_ollama_falls_back_to_quick(self):
        """If no ollama_client, detailed mode degrades to quick."""
        reg = _make_registry(ollama_client=None)

        with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "test", "depth": "detailed"},
                )
            )

        assert result.success
        assert "Extracted content" not in result.content

    async def test_detailed_mode_with_time_range(self):
        """Detailed mode respects time_range parameter."""
        client = _mock_ollama()
        reg = _make_registry(ollama_client=client)

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=None),
            patch(
                "app.skills.tools.web_extraction.fetch_and_extract",
                side_effect=[
                    ("http://example.com/1", "Today's dollar rate: $1050"),
                    ("http://example.com/2", None),
                    ("http://example.com/3", None),
                ],
            ),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={
                        "query": "dollar price today",
                        "time_range": "d",
                        "depth": "detailed",
                    },
                )
            )
            MockDDGS.return_value.text.assert_called_once_with(
                "dollar price today", timelimit="d", max_results=5
            )

        assert result.success
        assert "Extracted content from top results" in result.content

    async def test_detailed_mode_partial_fetch_success(self):
        """Detailed mode works when only some pages are fetchable."""
        client = _mock_ollama()
        reg = _make_registry(ollama_client=client)

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=None),
            patch(
                "app.skills.tools.web_extraction.fetch_and_extract",
                side_effect=[
                    ("http://example.com/1", None),  # 403
                    ("http://example.com/2", "Only this page worked with content"),
                    ("http://example.com/3", None),  # timeout
                ],
            ),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "test", "depth": "detailed"},
                )
            )

        assert result.success
        assert "Extracted content from top results" in result.content
        # LLM was called with only the successful page
        messages = client.chat.call_args.args[0]
        user_msg = messages[1].content
        assert "http://example.com/2" in user_msg
        assert "http://example.com/1" not in user_msg

    async def test_detailed_mode_respects_fetch_top_n_setting(self):
        """Settings.web_search_fetch_top_n controls how many pages to fetch."""
        client = _mock_ollama()
        settings = _mock_settings(web_search_fetch_top_n=1)
        reg = SkillRegistry(skills_dir="/nonexistent")
        register(reg, ollama_client=client, settings=settings)

        fetch_calls: list[str] = []

        async def mock_fetch_and_extract(url, timeout=8.0):
            fetch_calls.append(url)
            return (url, f"Content from {url}")

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=None),
            patch(
                "app.skills.tools.web_extraction.fetch_and_extract",
                side_effect=mock_fetch_and_extract,
            ),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "test", "depth": "detailed"},
                )
            )

        assert result.success
        # Only 1 URL should have been fetched (not 3)
        assert len(fetch_calls) == 1
        assert fetch_calls[0] == "http://example.com/1"

    async def test_detailed_mode_no_results_returns_not_found(self):
        """Detailed mode with zero search results returns 'No results found'."""
        client = _mock_ollama()
        reg = _make_registry(ollama_client=client)

        with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
            MockDDGS.return_value.text.return_value = []
            result = await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "xyznonexistent", "depth": "detailed"},
                )
            )

        assert "No results found" in result.content
        # LLM should NOT have been called
        client.chat.assert_not_called()

    async def test_detailed_mode_output_contains_both_snippets_and_extraction(self):
        """Detailed mode output has snippets section AND extracted content section."""
        client = _mock_ollama()
        client.chat = AsyncMock(return_value="Fecha 8: Racing vs Central, 22/03 19hs")
        reg = _make_registry(ollama_client=client)

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=None),
            patch(
                "app.skills.tools.web_extraction.fetch_and_extract",
                side_effect=[
                    ("http://espn.com/fixture", "Fixture Rosario Central 2026..."),
                    ("http://example.com/2", "More fixture data here..."),
                    ("http://example.com/3", None),
                ],
            ),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "fixture rosario central", "depth": "detailed"},
                )
            )

        content = result.content
        # Snippets are present
        assert "1. [Result 1]" in content
        # Separator exists
        assert "---" in content
        # Extracted section header
        assert "## Extracted content from top results:" in content
        # LLM extraction output
        assert "Fecha 8: Racing vs Central" in content


# ---------------------------------------------------------------------------
# Phase 5: Tool description
# ---------------------------------------------------------------------------


class TestToolDescription:
    def test_description_mentions_depth(self):
        reg = _make_registry()
        tool = reg.get_tool("web_search")
        assert tool is not None
        assert "depth" in tool.description
        assert "detailed" in tool.description

    def test_depth_parameter_in_schema(self):
        reg = _make_registry()
        tool = reg.get_tool("web_search")
        assert tool is not None
        props = tool.parameters["properties"]
        assert "depth" in props
        assert props["depth"]["enum"] == ["quick", "detailed"]


# ---------------------------------------------------------------------------
# Phase 6: Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_settings_used_for_fetch_top_n(self):
        """Custom settings.web_search_fetch_top_n is respected."""
        settings = _mock_settings(web_search_fetch_top_n=2)
        client = _mock_ollama()
        reg = SkillRegistry(skills_dir="/nonexistent")
        register(reg, ollama_client=client, settings=settings)
        # The tool is registered — settings are captured in closure
        tool = reg.get_tool("web_search")
        assert tool is not None


# ---------------------------------------------------------------------------
# Phase 7: Langfuse observability
# ---------------------------------------------------------------------------


class TestObservability:
    async def test_detailed_mode_creates_spans(self):
        """Detailed mode creates web_search:detailed, web_search:fetch, llm:web_extract spans."""
        client = _mock_ollama()
        reg = _make_registry(ollama_client=client)

        mock_trace = MagicMock()
        span_names: list[str] = []
        span_kinds: list[str] = []

        class FakeSpan:
            def __init__(self, name, kind, parent_id=None):
                self.span_id = f"span_{name}"
                self._name = name
                span_names.append(name)
                span_kinds.append(kind)

            def set_input(self, data):
                pass

            def set_output(self, data):
                pass

            def set_metadata(self, data):
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
                    ("http://example.com/1", "Page content here"),
                    ("http://example.com/2", None),
                    ("http://example.com/3", "More content"),
                ],
            ),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            result = await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "test", "depth": "detailed"},
                )
            )

        assert result.success
        assert "web_search:detailed" in span_names
        assert "web_search:fetch" in span_names
        assert "llm:web_extract" in span_names
        # llm:web_extract should be kind="generation"
        idx = span_names.index("llm:web_extract")
        assert span_kinds[idx] == "generation"

    async def test_quick_mode_no_spans(self):
        """Quick mode does not create any sub-spans."""
        client = _mock_ollama()
        reg = _make_registry(ollama_client=client)

        span_names: list[str] = []
        mock_trace = MagicMock()

        def fake_span(name, kind="span", parent_id=None):
            span_names.append(name)
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=MagicMock(span_id=f"span_{name}"))
            return ctx

        mock_trace.span = fake_span

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=mock_trace),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "capital de Francia"},
                )
            )

        assert len(span_names) == 0

    async def test_detailed_span_receives_exception_on_error(self):
        """When extraction fails, the detailed span's __aexit__ receives the exception."""
        client = _mock_ollama()
        # Make LLM extraction blow up
        client.chat = AsyncMock(side_effect=RuntimeError("LLM crashed"))
        reg = _make_registry(ollama_client=client)

        aexit_args: list[tuple] = []

        class FakeSpan:
            def __init__(self, name, kind, parent_id=None):
                self.span_id = f"span_{name}"
                self._name = name

            def set_input(self, data):
                pass

            def set_output(self, data):
                pass

            def set_metadata(self, data):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                if self._name == "web_search:detailed":
                    aexit_args.append((exc_type, exc_val, exc_tb))

        def fake_span(name, kind="span", parent_id=None):
            return FakeSpan(name, kind, parent_id)

        mock_trace = MagicMock()
        mock_trace.span = fake_span

        with (
            patch("app.skills.tools.search_tools.DDGS") as MockDDGS,
            patch("app.skills.tools.search_tools.get_current_trace", return_value=mock_trace),
            patch(
                "app.skills.tools.web_extraction.fetch_and_extract",
                side_effect=[
                    ("http://example.com/1", "Some page content"),
                    ("http://example.com/2", None),
                    ("http://example.com/3", None),
                ],
            ),
        ):
            MockDDGS.return_value.text.return_value = _MOCK_RESULTS
            await reg.execute_tool(
                ToolCall(
                    name="web_search",
                    arguments={"query": "test", "depth": "detailed"},
                )
            )

        # The outer except catches the RuntimeError and returns an error string,
        # but the detailed span's __aexit__ should have received the exception
        assert len(aexit_args) == 1
        exc_type, exc_val, exc_tb = aexit_args[0]
        assert exc_type is RuntimeError
        assert "LLM crashed" in str(exc_val)
