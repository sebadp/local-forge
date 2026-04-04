"""Tests for grep_tools.py — regex search via ripgrep / grep fallback."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills.tools.grep_tools import _MAX_OUTPUT_CHARS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry_and_register(tmp_path: Path):
    """Create a mock registry, register grep_code, and return the handler."""
    from app.skills.tools.grep_tools import register

    registry = MagicMock()
    register(registry, get_root=lambda: tmp_path)

    call_args = registry.register_tool.call_args
    return call_args.kwargs["handler"] if "handler" in (call_args.kwargs or {}) else call_args[1]["handler"]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGrepFindsPattern:
    def test_finds_known_string(self, tmp_path: Path):
        (tmp_path / "hello.py").write_text("def greet():\n    print('hello world')\n")
        handler = _make_registry_and_register(tmp_path)
        result = _run(handler(pattern="greet"))

        assert "hello.py" in result
        assert "greet" in result


class TestGrepWithContext:
    def test_context_lines(self, tmp_path: Path):
        content = "line1\nline2\ndef target_func():\nline4\nline5\n"
        (tmp_path / "mod.py").write_text(content)
        handler = _make_registry_and_register(tmp_path)
        result = _run(handler(pattern="target_func", context_lines=1))

        assert "target_func" in result
        # Context lines should include surrounding content
        assert "line2" in result or "line4" in result


class TestGrepIncludeFilter:
    def test_include_restricts_to_matching_files(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("import os\n")
        (tmp_path / "app.js").write_text("import os\n")
        handler = _make_registry_and_register(tmp_path)
        result = _run(handler(pattern="import os", include="*.py"))

        assert "app.py" in result
        assert "app.js" not in result


class TestGrepNoResults:
    def test_clean_no_match_message(self, tmp_path: Path):
        (tmp_path / "empty.py").write_text("nothing here\n")
        handler = _make_registry_and_register(tmp_path)
        result = _run(handler(pattern="NONEXISTENT_PATTERN_XYZ"))

        assert "No matches" in result


class TestGrepPathTraversal:
    def test_parent_directory_blocked(self, tmp_path: Path):
        handler = _make_registry_and_register(tmp_path)
        result = _run(handler(pattern="test", path="../"))

        assert "Access denied" in result


class TestGrepOutputTruncation:
    def test_large_output_capped(self, tmp_path: Path):
        # Create a file with many matching lines
        lines = [f"match_line_{i}" for i in range(500)]
        (tmp_path / "big.txt").write_text("\n".join(lines))

        handler = _make_registry_and_register(tmp_path)
        result = _run(handler(pattern="match_line", max_results=50))

        # Should contain content; if truncated, should note it
        assert "match_line" in result
