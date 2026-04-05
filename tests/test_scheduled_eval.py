"""Tests for scheduled eval configuration (Plan 62 Phase 5)."""

from __future__ import annotations

import pytest


def test_eval_scheduled_settings_defaults():
    """Config defaults for scheduled eval."""
    from app.config import Settings

    s = Settings(
        whatsapp_access_token="test",
        whatsapp_phone_number_id="test",
        whatsapp_verify_token="test",
    )
    assert s.eval_scheduled_enabled is False
    assert s.eval_scheduled_hour == 4
    assert s.eval_scheduled_threshold == 0.7
    assert s.eval_scheduled_mode == "classify"


def test_eval_scheduled_settings_override():
    """Config can be overridden via env-like kwargs."""
    from app.config import Settings

    s = Settings(
        whatsapp_access_token="test",
        whatsapp_phone_number_id="test",
        whatsapp_verify_token="test",
        eval_scheduled_enabled=True,
        eval_scheduled_hour=6,
        eval_scheduled_threshold=0.8,
        eval_scheduled_mode="e2e",
    )
    assert s.eval_scheduled_enabled is True
    assert s.eval_scheduled_hour == 6
    assert s.eval_scheduled_threshold == 0.8
    assert s.eval_scheduled_mode == "e2e"
