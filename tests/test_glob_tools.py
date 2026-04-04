"""Tests for glob_tools.py — file discovery by glob pattern."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.skills.tools.glob_tools import _DEFAULT_EXCLUDES, _MAX_RESULTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry_and_register(tmp_path: Path):
    """Create a mock registry, register glob_files, and return the handler."""
    from unittest.mock import MagicMock

    from app.skills.tools.glob_tools import register

    registry = MagicMock()
    register(registry, get_root=lambda: tmp_path)

    # The handler is passed as keyword arg to register_tool
    call_args = registry.register_tool.call_args
    return call_args.kwargs["handler"] if "handler" in (call_args.kwargs or {}) else call_args[1]["handler"]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGlobPyFiles:
    def test_finds_only_py_files(self, tmp_path: Path):
        (tmp_path / "main.py").write_text("x")
        (tmp_path / "app.js").write_text("y")
        (tmp_path / "readme.md").write_text("z")

        handler = _make_registry_and_register(tmp_path)
        result = _run(handler(pattern="*.py"))

        assert "main.py" in result
        assert "app.js" not in result
        assert "readme.md" not in result


class TestGlobExcludesDefault:
    def test_excludes_pycache_and_node_modules(self, tmp_path: Path):
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "mod.cpython-311.pyc").write_text("x")

        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()
        (node_modules / "pkg.js").write_text("x")

        (tmp_path / "real.py").write_text("x")

        handler = _make_registry_and_register(tmp_path)
        result = _run(handler(pattern="*"))

        assert "real.py" in result
        assert "__pycache__" not in result
        assert "node_modules" not in result


class TestGlobCustomExclude:
    def test_user_excludes_applied(self, tmp_path: Path):
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "lib.py").write_text("x")
        (tmp_path / "app.py").write_text("x")

        handler = _make_registry_and_register(tmp_path)
        result = _run(handler(pattern="*.py", exclude="vendor"))

        assert "app.py" in result
        assert "vendor" not in result


class TestGlobMaxResults:
    def test_caps_at_max(self, tmp_path: Path):
        for i in range(_MAX_RESULTS + 10):
            (tmp_path / f"file_{i:04d}.txt").write_text("x")

        handler = _make_registry_and_register(tmp_path)
        result = _run(handler(pattern="*.txt"))

        assert f"limited to {_MAX_RESULTS}" in result


class TestGlobDirectoryScoped:
    def test_subdirectory_restricts(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("x")
        (tmp_path / "setup.py").write_text("x")

        handler = _make_registry_and_register(tmp_path)
        result = _run(handler(pattern="*.py", directory="src"))

        assert "app.py" in result
        assert "setup.py" not in result


class TestGlobPathTraversal:
    def test_parent_directory_blocked(self, tmp_path: Path):
        handler = _make_registry_and_register(tmp_path)
        result = _run(handler(pattern="*.py", directory="../"))

        assert "Access denied" in result
