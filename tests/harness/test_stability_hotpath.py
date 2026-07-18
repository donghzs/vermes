"""Tests for harness.stability_hotpath — H3.2 stability probe for hot-path tools."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from harness.stability_hotpath import (
    probe_tool_stability,
    is_probe_enabled,
    _default_score_fn,
    _HOT_PATH_TOOLS,
)


# --------------------------------------------------------------------------- #
# Test doubles                                                                 #
# --------------------------------------------------------------------------- #


class FakeAgent:
    """Minimal agent stub for stability probe tests."""

    def __init__(
        self,
        enable_probe: bool = False,
        tool_result: str = "some result",
        _current_task_id: str = "test-task",
    ):
        self._enable_stability_probe = enable_probe
        self._current_task_id = _current_task_id
        self._tool_result = tool_result

    def _invoke_tool(self, name, args, task_id, call_id):
        return self._tool_result


# --------------------------------------------------------------------------- #
# _default_score_fn                                                            #
# --------------------------------------------------------------------------- #


class TestDefaultScoreFn:
    def test_non_empty_string_scores_1(self):
        assert _default_score_fn("hello world") == 1.0

    def test_empty_string_scores_0(self):
        assert _default_score_fn("") == 0.0

    def test_whitespace_only_scores_0(self):
        assert _default_score_fn("   \n  ") == 0.0

    def test_none_scores_0(self):
        assert _default_score_fn(None) == 0.0

    def test_very_short_string_scores_half(self):
        assert _default_score_fn("hi") == 0.5

    def test_non_empty_dict_scores_1(self):
        assert _default_score_fn({"key": "val"}) == 1.0

    def test_empty_dict_scores_0(self):
        assert _default_score_fn({}) == 0.0

    def test_non_empty_list_scores_1(self):
        assert _default_score_fn([1, 2, 3]) == 1.0

    def test_empty_list_scores_0(self):
        assert _default_score_fn([]) == 0.0

    def test_unknown_type_scores_1(self):
        assert _default_score_fn(42) == 1.0


# --------------------------------------------------------------------------- #
# is_probe_enabled                                                             #
# --------------------------------------------------------------------------- #


class TestIsProbeEnabled:
    def test_disabled_agent_returns_false(self):
        agent = FakeAgent(enable_probe=False)
        assert is_probe_enabled(agent, "web_search") is False

    def test_enabled_agent_hot_path_tool_returns_true(self):
        agent = FakeAgent(enable_probe=True)
        assert is_probe_enabled(agent, "web_search") is True

    def test_enabled_agent_non_hot_path_tool_returns_false(self):
        agent = FakeAgent(enable_probe=True)
        assert is_probe_enabled(agent, "write_file") is False

    def test_agent_without_attribute_returns_false(self):
        class NoAttr:
            pass

        assert is_probe_enabled(NoAttr(), "web_search") is False

    def test_hot_path_tools_not_empty(self):
        assert len(_HOT_PATH_TOOLS) > 0
        assert "web_search" in _HOT_PATH_TOOLS


# --------------------------------------------------------------------------- #
# probe_tool_stability                                                         #
# --------------------------------------------------------------------------- #


class TestProbeToolStability:
    def test_disabled_agent_returns_none(self):
        agent = FakeAgent(enable_probe=False)
        result = probe_tool_stability(agent, "web_search", {"query": "test"})
        assert result is None

    def test_non_hot_path_tool_returns_none(self):
        agent = FakeAgent(enable_probe=True)
        result = probe_tool_stability(agent, "write_file", {"path": "/tmp/test"})
        assert result is None

    def test_enabled_agent_hot_path_tool_runs_probe(self):
        """With a consistent tool, the probe should return None (stable)."""
        agent = FakeAgent(enable_probe=True, tool_result="consistent result here")
        result = probe_tool_stability(agent, "web_search", {"query": "test"}, n=3)
        # Stable tool → no warning
        assert result is None

    def test_never_raises_on_exception(self):
        """If the probe raises, should return None (fail-open)."""

        class BadAgent:
            _enable_stability_probe = True
            _current_task_id = "test"

            def _invoke_tool(self, *args):
                raise RuntimeError("boom")

        result = probe_tool_stability(BadAgent(), "web_search", {"query": "test"}, n=2)
        # Fail-open → None (no warning)
        assert result is None

    def test_agent_without_invoke_tool(self):
        """Agent without _invoke_tool should still fail-open."""

        class MinimalAgent:
            _enable_stability_probe = True
            _current_task_id = "test"

        result = probe_tool_stability(MinimalAgent(), "web_search", {"query": "test"}, n=2)
        # Fail-open → None
        assert result is None

    def test_none_agent_returns_none(self):
        result = probe_tool_stability(None, "web_search", {"query": "test"})
        assert result is None

    def test_none_tool_args_handled(self):
        agent = FakeAgent(enable_probe=True, tool_result="result")
        # None args should not crash — fail-open.
        result = probe_tool_stability(agent, "web_search", None, n=2)  # type: ignore
        # Either None (stable/fail-open) or a warning — but must not raise.
        assert result is None or isinstance(result, str)

    def test_all_hot_path_tools_are_probed(self):
        """Every tool in _HOT_PATH_TOOLS should be a valid probe target."""
        agent = FakeAgent(enable_probe=True, tool_result="stable result")
        for tool_name in _HOT_PATH_TOOLS:
            result = probe_tool_stability(agent, tool_name, {"arg": "val"}, n=2)
            # Should not raise; stable tool → None
            assert result is None
