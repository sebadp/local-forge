"""Tests for code security warning trace score persistence (Plan 61 Phase 1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_security_warning_returns_empty_for_non_code():
    from app.skills.tools.selfcode_tools import _security_warning

    result = _security_warning("hello world", "readme.txt", ".txt")
    assert result == ""


def test_security_warning_returns_empty_for_safe_code():
    from app.skills.tools.selfcode_tools import _security_warning

    safe_code = "def hello():\n    return 'world'\n"
    result = _security_warning(safe_code, "main.py", ".py")
    assert result == ""


def test_security_warning_detects_unsafe_pattern():
    from app.skills.tools.selfcode_tools import _security_warning

    unsafe_code = "import subprocess\nsubprocess.call(user_input, shell=True)\n"
    result = _security_warning(unsafe_code, "main.py", ".py")
    assert "Security warning" in result


def test_security_warning_persists_trace_score():
    """When a security pattern is detected and a trace exists, score is persisted."""
    from app.skills.tools.selfcode_tools import _security_warning

    mock_trace = MagicMock()
    mock_trace.add_score = AsyncMock()

    unsafe_code = "import subprocess\nsubprocess.call(user_input, shell=True)\n"

    with patch("app.tracing.context.get_current_trace", return_value=mock_trace):
        with patch("asyncio.ensure_future") as mock_ensure:
            result = _security_warning(unsafe_code, "main.py", ".py")

    assert "Security warning" in result
    mock_ensure.assert_called_once()


def test_security_warning_no_trace_still_returns_warning():
    """When no trace context exists, warning text is still returned."""
    from app.skills.tools.selfcode_tools import _security_warning

    unsafe_code = "import subprocess\nsubprocess.call(user_input, shell=True)\n"

    with patch("app.tracing.context.get_current_trace", return_value=None):
        result = _security_warning(unsafe_code, "main.py", ".py")
    assert "Security warning" in result
