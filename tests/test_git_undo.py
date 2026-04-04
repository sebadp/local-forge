"""Tests for git_undo and git_stash tools in git_tools.py (Plan 58)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry_and_register():
    """Register git tools on a mock registry, return dict of tool handlers by name."""
    from app.skills.tools.git_tools import register

    registry = MagicMock()
    handlers: dict[str, object] = {}

    def capture_register(**kwargs):
        handlers[kwargs["name"]] = kwargs["handler"]

    registry.register_tool = MagicMock(side_effect=capture_register)
    register(registry, settings=None)
    return handlers


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# git_undo tests
# ---------------------------------------------------------------------------


class TestGitUndoFile:
    @patch("app.skills.tools.git_tools._run_git", return_value=(0, "", ""))
    def test_restore_file(self, mock_git):
        handlers = _make_registry_and_register()
        result = _run(handlers["git_undo"](scope="file", file_path="main.py"))
        assert "Restored" in result
        assert "main.py" in result

    def test_missing_path_error(self):
        handlers = _make_registry_and_register()
        result = _run(handlers["git_undo"](scope="file", file_path=""))
        assert "Error" in result
        assert "file_path is required" in result


class TestGitUndoCommit:
    @patch("app.skills.tools.git_tools._run_git", return_value=(0, "Reverted commit abc123", ""))
    def test_revert_last_commit(self, mock_git):
        handlers = _make_registry_and_register()
        result = _run(handlers["git_undo"](scope="commit"))
        assert "Reverted" in result


class TestGitUndoFlagInjection:
    def test_flag_path_blocked(self):
        handlers = _make_registry_and_register()
        result = _run(handlers["git_undo"](scope="file", file_path="-rf"))
        assert "Error" in result
        assert "invalid file path" in result


class TestGitUndoUnknownScope:
    def test_unknown_scope_rejected(self):
        handlers = _make_registry_and_register()
        result = _run(handlers["git_undo"](scope="branch"))
        assert "Error" in result
        assert "unknown scope" in result


# ---------------------------------------------------------------------------
# git_stash tests
# ---------------------------------------------------------------------------


class TestGitStashSavePop:
    @patch("app.skills.tools.git_tools._run_git", return_value=(0, "Saved working directory", ""))
    def test_stash_save(self, mock_git):
        handlers = _make_registry_and_register()
        result = _run(handlers["git_stash"](action="save", message="wip"))
        assert "Saved" in result or "stashed" in result.lower()

    @patch("app.skills.tools.git_tools._run_git", return_value=(0, "Applied stash", ""))
    def test_stash_pop(self, mock_git):
        handlers = _make_registry_and_register()
        result = _run(handlers["git_stash"](action="pop"))
        assert "Applied" in result or "applied" in result.lower()


class TestGitStashListEmpty:
    @patch("app.skills.tools.git_tools._run_git", return_value=(0, "", ""))
    def test_empty_stash(self, mock_git):
        handlers = _make_registry_and_register()
        result = _run(handlers["git_stash"](action="list"))
        assert "empty" in result.lower()


class TestGitStashUnknownAction:
    def test_unknown_action_rejected(self):
        handlers = _make_registry_and_register()
        result = _run(handlers["git_stash"](action="drop"))
        assert "Error" in result
        assert "unknown action" in result
