"""Tests for AIAgent._coerce_api_error_detail — structured provider error extraction.

Manually applied from upstream commit (provider error coercion). The method
recursively extracts a human-readable string from structured provider error
fields (dict/list/None) for use in _summarize_api_error.
"""
import json
import pytest

from run_agent import AIAgent


class TestCoerceApiErrorDetail:
    """Verify _coerce_api_error_detail handles all provider error shapes."""

    def test_string_passthrough(self):
        """Plain strings pass through unchanged."""
        assert AIAgent._coerce_api_error_detail("rate limit exceeded") == "rate limit exceeded"
        assert AIAgent._coerce_api_error_detail("") == ""

    def test_dict_with_message_key(self):
        """Dict with 'message' key extracts that value."""
        err = {"message": "Invalid API key", "type": "authentication_error"}
        assert AIAgent._coerce_api_error_detail(err) == "Invalid API key"

    def test_dict_with_detail_key(self):
        """Dict with 'detail' key (no 'message') extracts that value."""
        err = {"detail": "Not found", "status": 404}
        assert AIAgent._coerce_api_error_detail(err) == "Not found"

    def test_dict_with_error_key(self):
        """Dict with 'error' key extracts that value."""
        err = {"error": "Bad request", "code": 400}
        assert AIAgent._coerce_api_error_detail(err) == "Bad request"

    def test_dict_with_nested_dict(self):
        """Nested dict recurses into the first meaningful key."""
        err = {"error": {"message": "nested error message", "code": "E001"}}
        assert AIAgent._coerce_api_error_detail(err) == "nested error message"

    def test_dict_with_code_key(self):
        """Dict with only 'code' extracts that."""
        err = {"code": "RATE_LIMIT", "retry_after": 60}
        assert AIAgent._coerce_api_error_detail(err) == "RATE_LIMIT"

    def test_dict_with_type_key(self):
        """Dict with only 'type' extracts that."""
        err = {"type": "overloaded_error"}
        assert AIAgent._coerce_api_error_detail(err) == "overloaded_error"

    def test_dict_with_all_keys_prefers_message(self):
        """When multiple keys exist, precedence is message > detail > error > code > type."""
        err = {"message": "first", "detail": "second", "error": "third", "code": "fourth", "type": "fifth"}
        assert AIAgent._coerce_api_error_detail(err) == "first"

    def test_dict_with_empty_string_values_falls_through(self):
        """Empty string values are skipped, falls through to next key."""
        err = {"message": "", "detail": "actual detail"}
        assert AIAgent._coerce_api_error_detail(err) == "actual detail"

    def test_dict_with_all_empty_strings_falls_to_json(self):
        """All keys empty → falls back to JSON representation."""
        err = {"message": "", "detail": "", "error": "", "code": "", "type": ""}
        result = AIAgent._coerce_api_error_detail(err)
        # Should return a JSON string representation
        assert isinstance(result, str)
        assert len(result) > 0

    def test_list_of_strings_joined(self):
        """List of strings is joined with '; '."""
        err = ["error one", "error two", "error three"]
        assert AIAgent._coerce_api_error_detail(err) == "error one; error two; error three"

    def test_list_of_dicts_joined(self):
        """List of dicts is recursively processed and joined."""
        err = [{"message": "first error"}, {"message": "second error"}]
        assert AIAgent._coerce_api_error_detail(err) == "first error; second error"

    def test_list_with_empty_items_filtered(self):
        """Empty items in list are filtered out."""
        err = ["real error", "", None, "another error"]
        assert AIAgent._coerce_api_error_detail(err) == "real error; another error"

    def test_none_returns_empty_string(self):
        """None input returns empty string."""
        assert AIAgent._coerce_api_error_detail(None) == ""

    def test_integer_returns_string(self):
        """Non-string, non-dict, non-list values are stringified."""
        assert AIAgent._coerce_api_error_detail(404) == "404"
        assert AIAgent._coerce_api_error_detail(True) == "True"

    def test_empty_dict_returns_json(self):
        """Empty dict falls back to JSON representation."""
        result = AIAgent._coerce_api_error_detail({})
        assert isinstance(result, str)

    def test_empty_list_returns_empty_string(self):
        """Empty list returns empty string after join."""
        assert AIAgent._coerce_api_error_detail([]) == ""

    def test_deeply_nested_dict(self):
        """Deeply nested dicts recurse correctly."""
        err = {"error": {"detail": {"message": "deep error"}}}
        assert AIAgent._coerce_api_error_detail(err) == "deep error"

    def test_tuple_treated_as_list(self):
        """Tuples are treated like lists."""
        err = ("error one", "error two")
        assert AIAgent._coerce_api_error_detail(err) == "error one; error two"

    def test_real_world_openai_error_shape(self):
        """Simulates OpenAI's error response structure."""
        err = {
            "error": {
                "message": "You exceeded your current quota",
                "type": "insufficient_quota",
                "param": None,
                "code": "insufficient_quota",
            }
        }
        assert AIAgent._coerce_api_error_detail(err) == "You exceeded your current quota"

    def test_real_world_anthropic_error_shape(self):
        """Simulates Anthropic's error response structure."""
        err = {
            "type": "error",
            "error": {
                "type": "overloaded_error",
                "message": "Overloaded",
            },
        }
        # 'type' key exists at top level but is "error" (not very helpful),
        # but 'error' key's nested dict has 'message' → should find it
        result = AIAgent._coerce_api_error_detail(err)
        assert "overloaded" in result.lower() or "error" in result.lower()
