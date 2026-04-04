"""Tests for credential scrubbing in subprocess environments (Plan 60, Phase 1)."""

from __future__ import annotations

import os
from unittest.mock import patch

from app.skills.tools.shell_tools import _scrubbed_env

# ---------------------------------------------------------------------------
# _scrubbed_env unit tests
# ---------------------------------------------------------------------------


def test_scrub_removes_exact_matches():
    """Variables in _SCRUB_EXACT must be removed."""
    env = {"PATH": "/usr/bin", "WHATSAPP_ACCESS_TOKEN": "secret", "GITHUB_TOKEN": "gh-tok"}
    with patch.dict(os.environ, env, clear=True):
        result = _scrubbed_env()
    assert "WHATSAPP_ACCESS_TOKEN" not in result
    assert "GITHUB_TOKEN" not in result


def test_scrub_removes_suffix_matches():
    """Variables ending with sensitive suffixes must be removed."""
    env = {"PATH": "/usr/bin", "MY_CUSTOM_SECRET_KEY": "val", "DB_PASSWORD": "pass"}
    with patch.dict(os.environ, env, clear=True):
        result = _scrubbed_env()
    assert "MY_CUSTOM_SECRET_KEY" not in result
    assert "DB_PASSWORD" not in result


def test_scrub_preserves_safe_vars():
    """PATH, HOME, LANG and similar must survive."""
    env = {"PATH": "/usr/bin", "HOME": "/home/user", "LANG": "en_US.UTF-8"}
    with patch.dict(os.environ, env, clear=True):
        result = _scrubbed_env()
    assert result["PATH"] == "/usr/bin"
    assert result["HOME"] == "/home/user"
    assert result["LANG"] == "en_US.UTF-8"


def test_scrub_preserves_ollama_vars():
    """OLLAMA_HOST and OLLAMA_NUM_PARALLEL must survive."""
    env = {"OLLAMA_HOST": "http://localhost:11434", "OLLAMA_NUM_PARALLEL": "4"}
    with patch.dict(os.environ, env, clear=True):
        result = _scrubbed_env()
    assert result["OLLAMA_HOST"] == "http://localhost:11434"
    assert result["OLLAMA_NUM_PARALLEL"] == "4"


def test_scrub_preserves_keep_list():
    """TERM_SESSION_ID and COLORTERM end with _TOKEN/_KEY suffixes but are in _SCRUB_KEEP."""
    env = {"TERM_SESSION_ID": "abc", "COLORTERM": "truecolor"}
    with patch.dict(os.environ, env, clear=True):
        result = _scrubbed_env()
    assert "TERM_SESSION_ID" in result
    assert "COLORTERM" in result


def test_scrub_removes_all_exact_entries():
    """All entries in _SCRUB_EXACT must be removed."""
    from app.skills.tools.shell_tools import _SCRUB_EXACT

    env = dict.fromkeys(_SCRUB_EXACT, "val")
    env["PATH"] = "/usr/bin"
    with patch.dict(os.environ, env, clear=True):
        result = _scrubbed_env()
    for k in _SCRUB_EXACT:
        assert k not in result
    assert result["PATH"] == "/usr/bin"


def test_scrubbed_env_returns_dict_without_secrets():
    """Integration: with real-ish env, scrubbed result has no secrets."""
    env = {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "WHATSAPP_ACCESS_TOKEN": "wa-tok",
        "GITHUB_TOKEN": "gh-tok",
        "LANGFUSE_SECRET_KEY": "lf-key",
        "MY_DB_PASSWORD": "dbpass",
        "OLLAMA_HOST": "http://localhost:11434",
    }
    with patch.dict(os.environ, env, clear=True):
        result = _scrubbed_env()
    # Secrets removed
    assert "WHATSAPP_ACCESS_TOKEN" not in result
    assert "GITHUB_TOKEN" not in result
    assert "LANGFUSE_SECRET_KEY" not in result
    assert "MY_DB_PASSWORD" not in result
    # Safe vars preserved
    assert result["PATH"] == "/usr/bin"
    assert result["OLLAMA_HOST"] == "http://localhost:11434"
