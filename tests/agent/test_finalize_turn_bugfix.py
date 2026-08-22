"""Regression tests for two pre-existing bugs in finalize_turn (fixed in 344e2e9ff,
extracted to agent.turn_finalizer.finalize_turn in A2 §7.5 stage-3).

Bug #1: approx_tokens always 0 — _finalize_turn used ``approx_tokens if 'approx_tokens' in dir() else 0``
        but approx_tokens was never a local variable, so dir() never contained it.
        Fix: added approx_tokens as an explicit parameter (default 0), caller passes real value.

Bug #2: auto-retry NameError — the auto-retry block referenced system_message/task_id/
        stream_callback/persist_user_message which are run_conversation parameters, not
        _finalize_turn parameters. Any trigger would NameError into a swallowed warning.
        Fix: moved auto-retry logic from _finalize_turn to run_conversation where those
        variables are in scope.  The run_conversation-level recursion is covered in
        tests/run_agent/test_run_agent.py::TestRunConversation.

本文件只含行为测试（不读源码文本）——凡 inspect.getsource 断言一律视为无效测试。
"""

import inspect
from unittest.mock import MagicMock, patch

import pytest

from agent.turn_finalizer import finalize_turn


def _make_finalize_agent():
    """构造 finalize_turn 所需的最小 agent fake（MagicMock + 显式属性）。

    对齐 tests/agent/test_system_prompt_restore.py 的 MagicMock 模式：只设被测函数
    读取/写入的属性，协作方（_save_trajectory/_persist_session 等）用 MagicMock。
    """
    agent = MagicMock()
    agent.max_iterations = 10
    agent.iteration_budget.remaining = 5
    agent.quiet_mode = True
    agent._save_trajectory = MagicMock()
    agent._cleanup_task_resources = MagicMock()
    agent._drop_trailing_empty_response_scaffolding = MagicMock()
    agent._persist_session = MagicMock()
    agent.session_id = "test-session"
    agent.model = "test-model"
    agent.provider = "test-provider"
    agent.base_url = "http://test"
    agent.session_input_tokens = 100
    agent.session_output_tokens = 50
    agent.session_cache_read_tokens = 0
    agent.session_cache_write_tokens = 0
    agent.session_reasoning_tokens = 0
    agent.session_prompt_tokens = 0
    agent.session_completion_tokens = 0
    agent.session_total_tokens = 150
    agent.session_estimated_cost_usd = 0.0
    agent.session_cost_status = "ok"
    agent.session_cost_source = "test"
    agent._tool_guardrail_halt_decision = None
    agent._drain_pending_steer = MagicMock(return_value=None)
    agent._response_was_previewed = False
    agent._skill_nudge_interval = 0
    agent._iters_since_skill = 0
    agent._sync_external_memory_for_turn = MagicMock()
    agent._spawn_background_review = MagicMock()
    agent.clear_interrupt = MagicMock()
    agent._stream_callback = None
    agent.platform = "test"
    agent._current_turn_tool_names = []
    agent._last_turn_tool_names = []
    agent.context_compressor = MagicMock()
    agent.context_compressor.last_prompt_tokens = 200
    return agent


def _call_finalize_turn(agent, *, final_response="done", approx_tokens=None):
    """调 finalize_turn，返回结果。"""
    scheduler = MagicMock()
    scheduler.evaluate = MagicMock(return_value=MagicMock(mode="none"))
    kwargs = dict(
        agent=agent,
        final_response=final_response,
        api_call_count=1,
        interrupted=False,
        failed=False,
        messages=[{"role": "user", "content": "hi"}],
        conversation_history=None,
        effective_task_id="task-1",
        turn_id=None,
        user_message="hi",
        original_user_message="hi",
        _should_review_memory=False,
        _turn_exit_reason="normal",
        _scheduler=scheduler,
        api_start_time=0,
    )
    if approx_tokens is not None:
        kwargs["approx_tokens"] = approx_tokens
    return finalize_turn(**kwargs)


class TestBug1ApproxTokensParam:
    """Bug #1: approx_tokens 是显式形参，且值正确传播到 _record_turn_metrics。"""

    def test_approx_tokens_is_function_parameter(self):
        sig = inspect.signature(finalize_turn)
        assert "approx_tokens" in sig.parameters, \
            "approx_tokens must be an explicit parameter of finalize_turn"
        assert sig.parameters["approx_tokens"].default == 0, \
            "approx_tokens should default to 0 for backward compatibility"

    def test_nonzero_approx_tokens_propagates_to_metrics(self):
        """When approx_tokens > 0, _record_turn_metrics must receive it."""
        agent = _make_finalize_agent()
        captured = {}

        def _mock_metrics(ag, msgs, sched, start_time, approx_tokens):
            captured["approx_tokens"] = approx_tokens

        with patch("agent.conversation_loop._record_turn_metrics", side_effect=_mock_metrics):
            _call_finalize_turn(agent, final_response="done", approx_tokens=42)

        assert captured["approx_tokens"] == 42, \
            "approx_tokens=42 must propagate to _record_turn_metrics, not be 0"

    def test_approx_tokens_defaults_to_zero(self):
        """不传 approx_tokens 时，_record_turn_metrics 收到默认值 0（向后兼容）。"""
        agent = _make_finalize_agent()
        captured = {}

        def _mock_metrics(ag, msgs, sched, start_time, approx_tokens):
            captured["approx_tokens"] = approx_tokens

        with patch("agent.conversation_loop._record_turn_metrics", side_effect=_mock_metrics):
            _call_finalize_turn(agent, final_response="done")

        assert captured["approx_tokens"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
