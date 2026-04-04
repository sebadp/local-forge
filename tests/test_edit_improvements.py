"""Tests for Plan 59 improvements: apply_patch uniqueness, replace_all, write guard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_selfcode_tools(tmp_path: Path):
    """Set up selfcode_tools with a fake project root in tmp_path."""
    import app.skills.tools.selfcode_tools as mod

    original_root = mod._PROJECT_ROOT
    mod._PROJECT_ROOT = tmp_path

    registry = MagicMock()
    settings = MagicMock()
    settings.agent_write_enabled = True

    # We need to call register() to get the handler functions
    handlers: dict = {}
    def capture_register(**kwargs):
        handlers[kwargs["name"]] = kwargs["handler"]

    registry.register_tool = capture_register
    mod.register(registry, settings)
    mod._PROJECT_ROOT = tmp_path  # re-set after register (it uses module-level)

    return handlers, mod, original_root


@pytest.fixture
def selfcode_env(tmp_path):
    handlers, mod, original_root = _make_selfcode_tools(tmp_path)
    yield tmp_path, handlers
    mod._PROJECT_ROOT = original_root


# ---------------------------------------------------------------------------
# apply_patch — uniqueness validation
# ---------------------------------------------------------------------------


class TestApplyPatchUnique:
    async def test_unique_match_succeeds(self, selfcode_env):
        root, handlers = selfcode_env
        f = root / "test.py"
        f.write_text("line1\nreturn None\nline3\n")

        result = await handlers["apply_patch"](
            path="test.py", search="return None", replace="return result"
        )
        assert "Patched" in result
        assert "return result" in f.read_text()

    async def test_ambiguous_match_returns_error(self, selfcode_env):
        root, handlers = selfcode_env
        f = root / "test.py"
        f.write_text("return None\nstuff\nreturn None\nmore\nreturn None\n")

        result = await handlers["apply_patch"](
            path="test.py", search="return None", replace="return x"
        )
        assert "found 3 times" in result
        assert "lines" in result
        # File should NOT be modified
        assert f.read_text().count("return None") == 3

    async def test_replace_all_replaces_all(self, selfcode_env):
        root, handlers = selfcode_env
        f = root / "test.py"
        f.write_text("old_name = 1\nold_name = 2\nold_name = 3\n")

        result = await handlers["apply_patch"](
            path="test.py", search="old_name", replace="new_name", replace_all=True
        )
        assert "all 3 occurrence" in result
        assert f.read_text().count("new_name") == 3
        assert f.read_text().count("old_name") == 0

    async def test_replace_all_reports_count(self, selfcode_env):
        root, handlers = selfcode_env
        f = root / "test.py"
        f.write_text("x\nx\n")

        result = await handlers["apply_patch"](
            path="test.py", search="x", replace="y", replace_all=True
        )
        assert "all 2 occurrence" in result

    async def test_not_found_preserved(self, selfcode_env):
        root, handlers = selfcode_env
        f = root / "test.py"
        f.write_text("hello world\n")

        result = await handlers["apply_patch"](
            path="test.py", search="not_here", replace="x"
        )
        assert "not found" in result

    async def test_single_match_no_replace_all(self, selfcode_env):
        """Single occurrence with replace_all=false should succeed (backward compat)."""
        root, handlers = selfcode_env
        f = root / "test.py"
        f.write_text("unique_string = True\n")

        result = await handlers["apply_patch"](
            path="test.py", search="unique_string", replace="new_string"
        )
        assert "Patched" in result


# ---------------------------------------------------------------------------
# write_source_file — overwrite guard
# ---------------------------------------------------------------------------


class TestWriteGuard:
    async def test_new_file_creates(self, selfcode_env):
        root, handlers = selfcode_env
        result = await handlers["write_source_file"](
            path="new_file.py", content="print('hello')\n"
        )
        assert "Written" in result
        assert (root / "new_file.py").exists()

    async def test_existing_file_blocked(self, selfcode_env):
        root, handlers = selfcode_env
        f = root / "existing.py"
        f.write_text("original content\nline2\nline3\n")

        result = await handlers["write_source_file"](
            path="existing.py", content="new content"
        )
        assert "already exists" in result
        assert "3 lines" in result
        assert "overwrite=true" in result
        # File should NOT be modified
        assert f.read_text() == "original content\nline2\nline3\n"

    async def test_existing_file_overwrite(self, selfcode_env):
        root, handlers = selfcode_env
        f = root / "existing.py"
        f.write_text("original content\n")

        result = await handlers["write_source_file"](
            path="existing.py", content="new content\n", overwrite=True
        )
        assert "Written" in result
        assert f.read_text() == "new content\n"


# ---------------------------------------------------------------------------
# read_source_file — offset/limit
# ---------------------------------------------------------------------------


class TestReadFileUnified:
    async def test_full_read(self, selfcode_env):
        root, handlers = selfcode_env
        f = root / "test.py"
        f.write_text("line0\nline1\nline2\nline3\nline4\n")

        result = await handlers["read_source_file"](path="test.py")
        assert "line0" in result
        assert "line4" in result

    async def test_offset_limit(self, selfcode_env):
        root, handlers = selfcode_env
        f = root / "test.py"
        lines = [f"line{i}" for i in range(20)]
        f.write_text("\n".join(lines) + "\n")

        result = await handlers["read_source_file"](
            path="test.py", offset=5, limit=3
        )
        assert "line5" in result
        assert "line7" in result
        assert "line8" not in result
        assert "Lines 6-8" in result  # 0-based offset 5 = line 6

    async def test_read_lines_backward_compat(self, selfcode_env):
        root, handlers = selfcode_env
        f = root / "test.py"
        lines = [f"line{i}" for i in range(10)]
        f.write_text("\n".join(lines) + "\n")

        result = await handlers["read_lines"](path="test.py", start=3, end=5)
        assert "line2" in result  # 1-indexed: line 3 = index 2
        assert "line4" in result
