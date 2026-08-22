"""Contract tests for ``agent.conversation_loop._record_turn_metrics``.

A2 stage-1 prerequisite (a): lock the I/O shape before orchestration refactor.
The function is best-effort (wrapped in ``try/except``): its contract is that,
given a turn's messages + scheduler, it (1) extracts tool names from assistant
``tool_calls``, (2) records a ``TurnMetrics`` to the scheduler with the right
turn number / counts / names, (3) mirrors the tool-name list onto the agent,
and (4) never raises — if the scheduler blows up it skips the rest silently.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.compression_scheduler import TurnMetrics
from agent.conversation_loop import _record_turn_metrics


def _assistant_with_tools(*names):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"function": {"name": n, "arguments": "{}"}} for n in names
        ],
    }


def _make_agent(turn_count: int = 5):
    agent = MagicMock()
    agent._user_turn_count = turn_count
    return agent


class TestRecordsTurnMetrics:
    def test_extracts_tool_names_and_records_correct_metrics(self):
        agent = _make_agent(turn_count=5)
        scheduler = MagicMock()
        messages = [
            {"role": "user", "content": "search for things"},
            _assistant_with_tools("web_search", "browser_navigate"),
            {"role": "tool", "content": "result"},
            _assistant_with_tools("memory_search"),
        ]

        _record_turn_metrics(agent, messages, scheduler, api_start_time=0.0, approx_tokens=1234)

        scheduler.record_turn.assert_called_once()
        (metrics,) = scheduler.record_turn.call_args[0]
        assert isinstance(metrics, TurnMetrics)
        assert metrics.turn_number == 5
        assert metrics.tool_calls_this_turn == 3  # 2 + 1, dedup NOT applied
        assert metrics.tool_names_this_turn == ["web_search", "browser_navigate", "memory_search"]
        assert metrics.approx_tokens == 1234

    def test_mirrors_tool_names_onto_agent(self):
        agent = _make_agent(turn_count=2)
        scheduler = MagicMock()
        messages = [_assistant_with_tools("web_search")]

        _record_turn_metrics(agent, messages, scheduler, api_start_time=0.0, approx_tokens=10)

        assert agent._current_turn_tool_names == ["web_search"]
        assert agent._last_turn_tool_names == ["web_search"]

    def test_no_tool_calls_yields_empty_names(self):
        agent = _make_agent(turn_count=1)
        scheduler = MagicMock()
        messages = [{"role": "user", "content": "just chat"}]

        _record_turn_metrics(agent, messages, scheduler, api_start_time=0.0, approx_tokens=10)

        (metrics,) = scheduler.record_turn.call_args[0]
        assert metrics.tool_calls_this_turn == 0
        assert metrics.tool_names_this_turn == []
        assert agent._current_turn_tool_names == []
        assert agent._last_turn_tool_names == []


class TestFailOpen:
    def test_scheduler_exception_does_not_propagate(self):
        """If the scheduler raises, the turn must still complete — no raise,
        and the agent state is left untouched (the except swallows it)."""
        agent = _make_agent(turn_count=3)
        scheduler = MagicMock()
        scheduler.record_turn.side_effect = RuntimeError("scheduler down")
        messages = [_assistant_with_tools("web_search")]

        # Must not raise.
        _record_turn_metrics(agent, messages, scheduler, api_start_time=0.0, approx_tokens=10)

        scheduler.record_turn.assert_called_once()
        # State assignment is after record_turn -> skipped on failure.
        assert not hasattr(agent, "_current_turn_tool_names") or agent._current_turn_tool_names != ["web_search"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
