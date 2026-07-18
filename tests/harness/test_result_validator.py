"""Tests for harness.result_validator — H3.1 runtime result structure validation."""

from __future__ import annotations

import pytest

from harness.result_validator import validate_result


class TestValidateResult:
    def test_none_result_for_non_empty_tool_warns(self):
        result = validate_result("write_file", None, {}, is_error=False)
        assert result is not None
        assert "None" in result

    def test_empty_string_result_for_non_empty_tool_warns(self):
        result = validate_result("terminal_tool", "   ", {}, is_error=False)
        assert result is not None
        assert "empty" in result

    def test_valid_string_result_passes(self):
        result = validate_result("write_file", "File written successfully", {}, is_error=False)
        assert result is None

    def test_pure_read_tool_none_result_passes(self):
        """Tools not in _NON_EMPTY_RESULT_TOOLS don't get checked for emptiness."""
        result = validate_result("get_config", None, {}, is_error=False)
        assert result is None

    def test_is_error_skips_validation(self):
        """If is_error is True, don't double-report."""
        result = validate_result("write_file", None, {}, is_error=True)
        assert result is None

    def test_bytes_result_warns(self):
        result = validate_result("browser_tool", b"raw bytes", {}, is_error=False)
        assert result is not None
        assert "bytes" in result

    def test_set_result_warns(self):
        result = validate_result("web_search", {"a", "b"}, {}, is_error=False)
        assert result is not None
        assert "set" in result

    def test_traceback_pattern_detected(self):
        result = validate_result(
            "terminal_tool",
            "Traceback (most recent call last):\n  File ...\nValueError: bad",
            {},
            is_error=False,
        )
        assert result is not None
        assert "uncaught error" in result

    def test_error_prefix_pattern_detected(self):
        result = validate_result(
            "terminal_tool",
            "ConnectionError: failed to connect",
            {},
            is_error=False,
        )
        assert result is not None
        assert "uncaught error" in result

    def test_segfault_pattern_detected(self):
        result = validate_result(
            "code_execution",
            "Segmentation fault (core dumped)",
            {},
            is_error=False,
        )
        assert result is not None

    def test_normal_error_in_result_not_flagged(self):
        """Normal text containing 'error' as a word should NOT trigger."""
        result = validate_result(
            "write_file",
            "No errors occurred. File written successfully.",
            {},
            is_error=False,
        )
        assert result is None

    def test_very_short_result_warns(self):
        result = validate_result("write_file", "ok", {}, is_error=False)
        assert result is not None
        assert "short" in result

    def test_dict_result_passes(self):
        result = validate_result("browser_tool", {"status": "ok"}, {}, is_error=False)
        assert result is None

    def test_list_result_passes(self):
        result = validate_result("web_search", ["result1", "result2"], {}, is_error=False)
        assert result is None

    def test_validator_never_raises(self):
        """Even with bad input, validate_result must not raise."""
        # None function_name, None args — should handle gracefully.
        result = validate_result(None, None, None, is_error=False)
        assert result is None  # None function_name not in _NON_EMPTY_RESULT_TOOLS

    def test_long_valid_result_passes(self):
        result = validate_result(
            "write_file",
            "x" * 10000,
            {},
            is_error=False,
        )
        assert result is None
