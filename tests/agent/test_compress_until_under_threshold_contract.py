"""Contract tests for ``agent.conversation_loop._compress_until_under_threshold``.

This is the FIRST function A2 will extract into ``CompactionEngine``.  The
contract being locked is the *loop's* I/O shape — NOT the internals of
``agent._compress_context`` (that is a separate collaborator with its own
test surface in ``tests/run_agent/test_413_compression.py`` and the
trajectory/compression suites).

Why we stub ``_compress_context`` here (deliberately, with a comment):
  * The function under test is the up-to-3-pass loop: break-on-no-reduction,
    ``conversation_history -> None`` on actual shrink (the "new session" flag),
    agent state reset, and re-estimating ``approx_tokens`` from the *shrunk*
    list.  All of that runs for real below (including the real
    ``estimate_request_tokens_rough``).
  * ``_compress_context`` is an injected collaborator whose real body is a
    forwarder to ``conversation_compression.compress_context``.  Replacing it
    with a deterministic shrink lets us assert the loop's contract precisely
    instead of depending on a heavy compressor engine.

The fake agent mirrors the established pattern in
``tests/agent/test_system_prompt_restore.py`` (MagicMock + explicit attrs).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.conversation_loop import _compress_until_under_threshold
from agent.model_metadata import estimate_request_tokens_rough


def _msg(i: int) -> dict:
    """A single deterministic message."""
    return {"role": "user", "content": f"message number {i}"}


def _make_agent(compress_side_effect, threshold_tokens: int = 0):
    """Minimal agent fake with the attributes the loop reads/writes.

    ``_compress_context`` is a deterministic stand-in (see module docstring);
    ``context_compressor.threshold_tokens`` and ``tools`` are explicit so the
    loop's reads are controlled, and the reset target attrs are pre-set to
    sentinel values so we can prove the reset actually happens.
    """
    agent = MagicMock()
    agent._compress_context = MagicMock(side_effect=compress_side_effect)
    agent.context_compressor = SimpleNamespace(threshold_tokens=threshold_tokens)
    agent.tools = None
    # Sentinels that MUST be reset whenever a pass actually shrinks.
    agent._empty_content_retries = 7
    agent._thinking_prefill_retries = 3
    agent._last_content_with_tools = "sentinel"
    agent._last_content_tools_all_housekeeping = True
    agent._mute_post_response = True
    return agent


class TestReturnsTupleShape:
    def test_returns_four_tuple(self):
        agent = _make_agent(lambda messages, system_message, **kw: (messages, "active"))
        result = _compress_until_under_threshold(
            agent,
            [_msg(0)],
            "system",
            "active",
            approx_tokens=10,
            effective_task_id="t1",
            conversation_history=[{"role": "user", "content": "hi"}],
        )
        assert isinstance(result, tuple) and len(result) == 4
        messages, active_system_prompt, approx_tokens, conversation_history = result
        assert isinstance(messages, list)
        assert isinstance(active_system_prompt, str)
        assert isinstance(approx_tokens, int)
        # No reduction -> history preserved (the caller's object, unchanged)
        assert conversation_history == [{"role": "user", "content": "hi"}]


class TestReductionContract:
    def test_shrink_sets_history_none_and_resets_state(self):
        """A real shrink must signal a new session (history=None) and reset
        the retry/tool-call state — this is the "压缩标记" the caller relies on
        to flush ALL compressed messages rather than the delta."""
        # Drop the 2 oldest messages on the FIRST pass only, then no further
        # reduction -> deterministic 6 -> 4 -> break.
        def _shrink_once(messages, system_message, *, approx_tokens=None, task_id="default"):
            if len(messages) == 6:
                return messages[2:], "active"
            return messages, "active"

        original = [_msg(i) for i in range(6)]
        agent = _make_agent(_shrink_once)
        messages, active_system_prompt, approx_tokens, conversation_history = (
            _compress_until_under_threshold(
                agent,
                original,
                "system",
                "active",
                approx_tokens=100,
                effective_task_id="t1",
                conversation_history=original,
            )
        )

        assert conversation_history is None
        assert messages == [_msg(2), _msg(3), _msg(4), _msg(5)]
        # State reset happened (sentinels cleared).
        assert agent._empty_content_retries == 0
        assert agent._thinking_prefill_retries == 0
        assert agent._last_content_with_tools is None
        assert agent._last_content_tools_all_housekeeping is False
        assert agent._mute_post_response is False

    def test_approx_tokens_recomputed_from_shrunk_list(self):
        """The re-estimated token count must come from the *shrunk* messages,
        not the stale pre-compression list.  This is the exact line a naive
        extraction could regress (recompute on the original list)."""
        def _shrink_once(messages, system_message, *, approx_tokens=None, task_id="default"):
            # Shrink only on the first pass; then no further reduction.
            if len(messages) == 4:
                return messages[2:], "active"
            return messages, "active"

        original = [_msg(i) for i in range(4)]
        agent = _make_agent(_shrink_once)
        messages, active_system_prompt, approx_tokens, conversation_history = (
            _compress_until_under_threshold(
                agent,
                original,
                "system",
                "active",
                approx_tokens=100,
                effective_task_id="t1",
                conversation_history=original,
            )
        )

        expected = estimate_request_tokens_rough(
            messages,
            system_prompt=active_system_prompt or "",
            tools=None,
        )
        assert approx_tokens == expected
        # And it must NOT equal the stale estimate of the original list.
        assert approx_tokens != estimate_request_tokens_rough(
            original, system_prompt="active", tools=None
        )


class TestNoReductionContract:
    def test_no_reduction_preserves_history_and_calls_once(self):
        """No shrink -> break immediately; history passed through unchanged;
        exactly one _compress_context call."""
        original = [_msg(i) for i in range(3)]
        agent = _make_agent(
            lambda messages, system_message, **kw: (messages, "active")
        )
        messages, active_system_prompt, approx_tokens, conversation_history = (
            _compress_until_under_threshold(
                agent,
                original,
                "system",
                "active",
                approx_tokens=50,
                effective_task_id="t1",
                conversation_history=original,
            )
        )
        assert agent._compress_context.call_count == 1
        assert conversation_history is original
        assert messages is original


class TestMaxPassesContract:
    def test_never_more_than_three_passes(self):
        """Even when every pass shrinks, the loop caps at 3 passes."""
        def _always_shrink(messages, system_message, *, approx_tokens=None, task_id="default"):
            return messages[1:], "active"

        agent = _make_agent(_always_shrink)
        _compress_until_under_threshold(
            agent,
            [_msg(i) for i in range(6)],
            "system",
            "active",
            approx_tokens=100,
            effective_task_id="t1",
            conversation_history=None,
        )
        assert agent._compress_context.call_count == 3


class TestThresholdBreakContract:
    def test_breaks_early_when_under_threshold(self):
        """After a shrink, if the re-estimated tokens drop under the
        compressor threshold, stop — do not burn the remaining passes."""
        def _shrink_to_empty(messages, system_message, *, approx_tokens=None, task_id="default"):
            return [], "active"

        # threshold 10, and empty-list estimate is 0 < 10 -> break after pass 1.
        agent = _make_agent(_shrink_to_empty, threshold_tokens=10)
        messages, active_system_prompt, approx_tokens, conversation_history = (
            _compress_until_under_threshold(
                agent,
                [_msg(i) for i in range(4)],
                "system",
                "",  # empty active prompt -> empty-list estimate == 0
                approx_tokens=100,
                effective_task_id="t1",
                conversation_history=None,
            )
        )
        assert agent._compress_context.call_count == 1
        assert messages == []
        assert conversation_history is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
