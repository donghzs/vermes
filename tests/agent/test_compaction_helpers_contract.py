"""Contract tests for the three compaction helpers extracted in A2 step 2-4.

These tests lock the I/O contract of:
  * ``compaction_check_in_loop`` — in-loop single-pass compression check
  * ``scheduler_driven_compaction`` — scheduler-driven proactive compression
  * ``error_recovery_compaction`` — error-recovery compression (413/overflow)

The pattern follows ``test_compress_until_under_threshold_contract.py``:
we stub ``_compress_context`` as a deterministic collaborator and assert
the helper's observable contract — not the internals of compression.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.conversation_compression import (
    compaction_check_in_loop,
    scheduler_driven_compaction,
    error_recovery_compaction,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _msg(n: int) -> dict:
    return {"role": "assistant", "content": f"msg-{n}"}


def _make_agent(messages_after_compress=None, compressor_shrinks=True):
    """Minimal fake agent with the attributes the helpers touch."""
    agent = MagicMock()
    agent.compression_enabled = True
    agent.tools = []
    agent._safe_print = MagicMock()
    agent._emit_status = MagicMock()
    agent._empty_content_retries = 3
    agent._thinking_prefill_retries = 2
    agent._last_content_with_tools = "something"
    agent._last_content_tools_all_housekeeping = True
    agent._mute_post_response = True

    # _compress_context: shrink list by 1 each call (or return as-is)
    def _compress(messages, system_message, approx_tokens=None, task_id=None):
        if compressor_shrinks and len(messages) > 0:
            return messages[1:], "compressed-prompt"
        return messages, system_message
    agent._compress_context = _compress

    # context_compressor mock
    compressor = MagicMock()
    compressor.threshold_tokens = 1000
    compressor.last_prompt_tokens = 500
    compressor.context_length = 8000
    agent.context_compressor = compressor

    return agent


# ===========================================================================
# compaction_check_in_loop
# ===========================================================================

class TestCompactionCheckInLoop:

    def test_no_compression_when_disabled(self):
        agent = _make_agent()
        agent.compression_enabled = False
        original = [_msg(1), _msg(2)]
        result = compaction_check_in_loop(
            agent, list(original), "sys", "active", 500, "task-1", {"id": "hist"},
        )
        messages, active, hist = result
        assert messages == original
        assert active == "active"
        assert hist == {"id": "hist"}  # unchanged

    def test_no_compression_when_should_not_compress(self):
        agent = _make_agent()
        agent.context_compressor.should_compress.return_value = False
        original = [_msg(1), _msg(2)]
        result = compaction_check_in_loop(
            agent, list(original), "sys", "active", 500, "task-1", {"id": "hist"},
        )
        messages, active, hist = result
        assert messages == original
        assert hist == {"id": "hist"}

    def test_compression_resets_history(self):
        agent = _make_agent()
        agent.context_compressor.should_compress.return_value = True
        result = compaction_check_in_loop(
            agent, [_msg(1), _msg(2), _msg(3)], "sys", "active", 2000, "task-1", {"id": "hist"},
        )
        messages, active, hist = result
        assert len(messages) == 2  # shrunk by 1
        assert active == "compressed-prompt"
        assert hist is None

    def test_returns_three_tuple(self):
        agent = _make_agent()
        agent.context_compressor.should_compress.return_value = False
        result = compaction_check_in_loop(
            agent, [], "sys", "active", 100, "task-1", None,
        )
        assert isinstance(result, tuple) and len(result) == 3


# ===========================================================================
# scheduler_driven_compaction
# ===========================================================================

class TestSchedulerDrivenCompaction:

    def test_compresses_and_records(self):
        agent = _make_agent()
        scheduler = MagicMock()
        scheduler.record_compression = MagicMock()
        result = scheduler_driven_compaction(
            agent, [_msg(1), _msg(2), _msg(3)], "sys", "active",
            2000, "task-1", {"id": "hist"}, scheduler,
        )
        messages, active, hist = result
        assert len(messages) == 2  # shrunk
        assert active == "compressed-prompt"
        assert hist is None
        scheduler.record_compression.assert_called_once()

    def test_no_shrink_keeps_history(self):
        agent = _make_agent(compressor_shrinks=False)
        scheduler = MagicMock()
        result = scheduler_driven_compaction(
            agent, [_msg(1), _msg(2)], "sys", "active",
            2000, "task-1", {"id": "hist"}, scheduler,
        )
        messages, active, hist = result
        assert len(messages) == 2  # unchanged
        assert hist == {"id": "hist"}  # NOT reset
        scheduler.record_compression.assert_not_called()

    def test_state_reset_only_on_shrink(self):
        agent = _make_agent()
        scheduler = MagicMock()
        scheduler_driven_compaction(
            agent, [_msg(1)], "sys", "active", 2000, "task-1", None, scheduler,
        )
        # shrunk from 1 to 0
        assert agent._empty_content_retries == 0
        assert agent._thinking_prefill_retries == 0

    def test_emit_status_called(self):
        agent = _make_agent()
        scheduler = MagicMock()
        scheduler_driven_compaction(
            agent, [_msg(1), _msg(2)], "sys", "active", 5000, "task-1", None, scheduler,
        )
        agent._emit_status.assert_called_once()


# ===========================================================================
# error_recovery_compaction
# ===========================================================================

class TestErrorRecoveryCompaction:

    def test_shrunk_true_when_messages_decrease(self):
        agent = _make_agent()
        result = error_recovery_compaction(
            agent, [_msg(1), _msg(2), _msg(3)], "sys", "active",
            3000, "task-1", {"id": "hist"},
        )
        messages, active, hist, shrunk = result
        assert shrunk is True
        assert len(messages) == 2
        assert hist is None

    def test_shrunk_false_when_no_reduction(self):
        agent = _make_agent(compressor_shrinks=False)
        result = error_recovery_compaction(
            agent, [_msg(1), _msg(2)], "sys", "active",
            3000, "task-1", {"id": "hist"},
        )
        messages, active, hist, shrunk = result
        assert shrunk is False
        assert len(messages) == 2
        assert hist is None  # history STILL reset (new session created)

    def test_returns_four_tuple(self):
        agent = _make_agent()
        result = error_recovery_compaction(
            agent, [], "sys", "active", 100, "task-1", None,
        )
        assert isinstance(result, tuple) and len(result) == 4

    def test_state_not_reset(self):
        # error_recovery_compaction does NOT reset _empty_content_retries etc.
        # (original conversation_loop code didn't either at these sites)
        agent = _make_agent()
        error_recovery_compaction(
            agent, [_msg(1), _msg(2)], "sys", "active", 3000, "task-1", None,
        )
        # state remains as set by _make_agent
        assert agent._empty_content_retries == 3
        assert agent._thinking_prefill_retries == 2
