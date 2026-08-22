"""状态交接不变量测试：_initialize_turn → finalize_turn（A2 §7.5 stage-3 收口后）。

锁定的不变量（§7.4 (b)）：
1. _initialize_turn 设置的状态字段有正确初始值（task_id 一致 / retry 归零 /
   stream_callback+persist_override 透传 / rejection_count 保留）。
2. finalize_turn（agent.turn_finalizer，原内联 _finalize_turn 已删）正确消费
   initialize 设置的状态（effective_task_id → _cleanup_task_resources；
   conversation_history → _persist_session；completed 语义正确）。

拆 turn/step/stream Service 时，这些经 agent 对象隐式传递的状态交接不能被破坏。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.conversation_loop import _initialize_turn
from agent.turn_finalizer import finalize_turn


def _make_agent():
    """构造 _initialize_turn / _finalize_turn 所需的最小 agent fake。"""
    agent = MagicMock()
    agent.session_id = "sess-1"
    agent.provider = "openrouter"
    agent.model = "test-model"
    agent.base_url = "http://test"
    agent.api_mode = "chat_completions"
    agent.platform = "cli"
    agent._ensure_db_session = MagicMock()
    agent._restore_primary_runtime = MagicMock()
    agent._cleanup_dead_connections = MagicMock(return_value=False)
    agent._emit_status = MagicMock()
    agent._tool_guardrails = MagicMock()
    agent._tool_guardrails.reset_for_turn = MagicMock()
    agent._tool_guardrails.after_call = MagicMock()
    # finalize 需要的方法/属性
    agent.max_iterations = 10
    agent.iteration_budget = MagicMock()
    agent.iteration_budget.remaining = 5
    agent.quiet_mode = True
    agent._save_trajectory = MagicMock()
    agent._cleanup_task_resources = MagicMock()
    agent._drop_trailing_empty_response_scaffolding = MagicMock()
    agent._persist_session = MagicMock()
    agent._handle_max_iterations = MagicMock()
    agent.session_input_tokens = 0
    agent.session_output_tokens = 0
    agent.session_cache_read_tokens = 0
    agent.session_cache_write_tokens = 0
    agent.session_reasoning_tokens = 0
    agent.session_prompt_tokens = 0
    agent.session_completion_tokens = 0
    agent.session_total_tokens = 0
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
    agent._current_turn_tool_names = []
    agent._last_turn_tool_names = []
    agent.context_compressor = MagicMock()
    agent.context_compressor.last_prompt_tokens = 0
    agent.valid_tool_names = []
    agent._interrupt_message = None
    agent._operator_claim_verifier_enabled = MagicMock(return_value=False)
    agent._detect_operation_claims = MagicMock(return_value=[])
    agent._operator_claim_rejection_count = 2  # 预置非 0，验证 initialize 保留
    return agent


# ---------------------------------------------------------------------------
# _initialize_turn 状态初始化不变量
# ---------------------------------------------------------------------------


class TestInitializeTurnState:
    def test_current_task_id_matches_returned_effective_task_id(self):
        agent = _make_agent()
        _, _, effective_task_id = _initialize_turn(agent, "hi", None, "task-42", None)
        assert effective_task_id == "task-42"
        assert agent._current_task_id == "task-42"

    def test_retry_counters_reset_to_zero(self):
        agent = _make_agent()
        agent._empty_content_retries = 5
        agent._thinking_prefill_retries = 3
        agent._invalid_tool_retries = 7
        agent._last_content_with_tools = "stale"
        _initialize_turn(agent, "hi", None, None, None)
        assert agent._empty_content_retries == 0
        assert agent._thinking_prefill_retries == 0
        assert agent._invalid_tool_retries == 0
        assert agent._last_content_with_tools is None
        assert agent._vision_supported is True
        assert agent._tool_guardrail_halt_decision is None
        assert agent._turn_tool_signatures == []

    def test_stream_callback_and_persist_override_propagate(self):
        agent = _make_agent()
        cb = object()
        _initialize_turn(agent, "hi", "clean hi", None, cb)
        assert agent._stream_callback is cb
        assert agent._persist_user_message_override == "clean hi"
        assert agent._persist_user_message_idx is None

    def test_operator_claim_rejection_count_preserved_across_turns(self):
        """initialize 不得重置拒绝计数器（跨回合保留，供 auto-retry 判定）。"""
        agent = _make_agent()
        agent._operator_claim_rejection_count = 3
        _initialize_turn(agent, "hi", None, None, None)
        assert agent._operator_claim_rejection_count == 3


# ---------------------------------------------------------------------------
# _finalize_turn 状态消费不变量
# ---------------------------------------------------------------------------


def _call_finalize_turn(agent, **overrides):
    scheduler = MagicMock()
    scheduler.evaluate = MagicMock(return_value=MagicMock(mode="none"))
    kwargs = dict(
        agent=agent,
        final_response="done",
        api_call_count=1,
        interrupted=False,
        failed=False,
        messages=[{"role": "user", "content": "hi"}],
        conversation_history=None,
        effective_task_id="task-42",
        turn_id=None,
        user_message="hi",
        original_user_message="hi",
        _should_review_memory=False,
        _turn_exit_reason="normal",
        _scheduler=scheduler,
        api_start_time=0,
        approx_tokens=100,
    )
    kwargs.update(overrides)
    return finalize_turn(**kwargs)


class TestFinalizeTurnConsumesState:
    def test_effective_task_id_passed_to_cleanup(self):
        agent = _make_agent()
        _call_finalize_turn(agent, effective_task_id="task-42")
        agent._cleanup_task_resources.assert_called_once_with("task-42")

    def test_conversation_history_passed_to_persist(self):
        agent = _make_agent()
        history = [{"role": "user", "content": "earlier"}]
        _call_finalize_turn(agent, conversation_history=history)
        # _persist_session 第二参是 conversation_history（位置参数）
        agent._persist_session.assert_called_once()
        _args, _kwargs = agent._persist_session.call_args
        assert _args[1] is history

    def test_completed_semantics(self):
        """completed = final_response 非空 且 api_call_count < max_iterations。"""
        agent = _make_agent()
        agent.max_iterations = 10
        result = _call_finalize_turn(agent, final_response="done", api_call_count=5)
        assert result["completed"] is True

        # api_call_count >= max_iterations → completed False
        agent2 = _make_agent()
        agent2.max_iterations = 10
        result2 = _call_finalize_turn(agent2, final_response="done", api_call_count=10)
        assert result2["completed"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
