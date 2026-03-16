"""Tests for runtime token calibration (Plan 45)."""

from __future__ import annotations

from app.context.token_estimator import (
    _DEFAULT_RATIO,
    _EMA_ALPHA,
    _token_ratios,
    calibrate,
    estimate_tokens,
    get_calibration_info,
)


def _reset_ratios():
    """Clear calibration state between tests."""
    _token_ratios.clear()


class TestCalibrate:
    def setup_method(self):
        _reset_ratios()

    def test_first_call_sets_ratio_directly(self):
        calibrate("test-model", char_count=3200, actual_tokens=1000)
        assert "test-model" in _token_ratios
        assert _token_ratios["test-model"] == 3200 / 1000  # 3.2

    def test_second_call_applies_ema(self):
        calibrate("m", char_count=4000, actual_tokens=1000)
        first_ratio = _token_ratios["m"]  # 4.0

        calibrate("m", char_count=3000, actual_tokens=1000)
        expected = _EMA_ALPHA * 3.0 + (1 - _EMA_ALPHA) * first_ratio
        assert abs(_token_ratios["m"] - expected) < 1e-6

    def test_ignores_zero_actual_tokens(self):
        calibrate("m", char_count=1000, actual_tokens=0)
        assert "m" not in _token_ratios

    def test_ignores_negative_actual_tokens(self):
        calibrate("m", char_count=1000, actual_tokens=-5)
        assert "m" not in _token_ratios

    def test_ignores_zero_char_count(self):
        calibrate("m", char_count=0, actual_tokens=500)
        assert "m" not in _token_ratios

    def test_ignores_negative_char_count(self):
        calibrate("m", char_count=-10, actual_tokens=500)
        assert "m" not in _token_ratios

    def test_per_model_separate_ratios(self):
        calibrate("qwen3.5:9b", char_count=3200, actual_tokens=1000)
        calibrate("llava:7b", char_count=5000, actual_tokens=1000)
        assert abs(_token_ratios["qwen3.5:9b"] - 3.2) < 1e-6
        assert abs(_token_ratios["llava:7b"] - 5.0) < 1e-6


class TestEstimateTokens:
    def setup_method(self):
        _reset_ratios()

    def test_default_uses_chars_div_4(self):
        result = estimate_tokens("a" * 400)
        assert result == 100  # 400 / 4.0

    def test_calibrated_model(self):
        calibrate("qwen3.5:9b", char_count=3200, actual_tokens=1000)
        # ratio = 3.2, so 320 chars → 100 tokens
        result = estimate_tokens("a" * 320, model="qwen3.5:9b")
        assert result == 100

    def test_unknown_model_uses_default(self):
        result = estimate_tokens("a" * 400, model="unknown")
        assert result == 400 // int(_DEFAULT_RATIO)

    def test_minimum_one_token(self):
        result = estimate_tokens("")
        assert result == 1


class TestGetCalibrationInfo:
    def setup_method(self):
        _reset_ratios()

    def test_uncalibrated(self):
        info = get_calibration_info("qwen3.5:9b")
        assert info["calibrated"] is False
        assert info["chars_per_token"] == _DEFAULT_RATIO
        assert info["known_models"] == []

    def test_calibrated(self):
        calibrate("qwen3.5:9b", char_count=3200, actual_tokens=1000)
        info = get_calibration_info("qwen3.5:9b")
        assert info["calibrated"] is True
        assert info["chars_per_token"] == 3.2
        assert "qwen3.5:9b" in info["known_models"]
