"""Contract tests for ``agent.conversation_loop._run_post_llm_hooks``.

A2 stage-1 prerequisite (a): lock the I/O shape of the post-LLM plugin hook
before any orchestration refactor touches it.  The function's contract is
narrow: fire ``post_llm_call`` exactly once per turn, only when there is a
final response and the turn was not interrupted, forwarding a snapshot of the
conversation (not the live list) plus session/model/platform metadata, and
never raise if the hook blows up (fail-open).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from agent.conversation_loop import _run_post_llm_hooks


def _make_agent():
    agent = MagicMock()
    agent.session_id = "sess-123"
    agent.model = "test-model"
    agent.platform = "cli"
    return agent


class TestFiresOnce:
    def test_fires_with_correct_kwargs(self):
        agent = _make_agent()
        messages = [{"role": "user", "content": "hi"}]
        with patch("vermes_cli.plugins.invoke_hook") as invoke_hook:
            _run_post_llm_hooks(agent, "final answer", False, messages, "hi")

        invoke_hook.assert_called_once()
        args, kwargs = invoke_hook.call_args
        assert args == ("post_llm_call",)
        assert kwargs["session_id"] == "sess-123"
        assert kwargs["user_message"] == "hi"
        assert kwargs["assistant_response"] == "final answer"
        assert kwargs["model"] == "test-model"
        assert kwargs["platform"] == "cli"

    def test_forwards_conversation_snapshot_not_live_list(self):
        """The hook must receive a copy, so later mutation of ``messages``
        cannot corrupt what the plugin persisted."""
        agent = _make_agent()
        messages = [{"role": "user", "content": "hi"}]
        with patch("vermes_cli.plugins.invoke_hook") as invoke_hook:
            _run_post_llm_hooks(agent, "final answer", False, messages, "hi")

        snapshot = invoke_hook.call_args.kwargs["conversation_history"]
        assert snapshot == messages
        assert snapshot is not messages  # a copy, not the same object


class TestGating:
    def test_no_final_response_skips_hook(self):
        agent = _make_agent()
        with patch("vermes_cli.plugins.invoke_hook") as invoke_hook:
            _run_post_llm_hooks(agent, None, False, [], "hi")
        invoke_hook.assert_not_called()

    def test_empty_final_response_skips_hook(self):
        agent = _make_agent()
        with patch("vermes_cli.plugins.invoke_hook") as invoke_hook:
            _run_post_llm_hooks(agent, "", False, [], "hi")
        invoke_hook.assert_not_called()

    def test_interrupted_skips_hook(self):
        agent = _make_agent()
        with patch("vermes_cli.plugins.invoke_hook") as invoke_hook:
            _run_post_llm_hooks(agent, "final answer", True, [], "hi")
        invoke_hook.assert_not_called()


class TestFailOpen:
    def test_hook_exception_does_not_propagate(self, caplog):
        agent = _make_agent()

        def _boom(*args, **kwargs):
            raise RuntimeError("plugin exploded")

        with patch("vermes_cli.plugins.invoke_hook", side_effect=_boom):
            with caplog.at_level(logging.WARNING, logger="agent.conversation_loop"):
                # Must not raise.
                _run_post_llm_hooks(agent, "final answer", False, [], "hi")

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("post_llm_call hook failed" in r.getMessage() for r in warnings)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
