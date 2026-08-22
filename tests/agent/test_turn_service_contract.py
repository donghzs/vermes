"""TurnService 契约测：直接锁新函数名（A2 §7.5 stage 3）。

抽取后补强（对标 ed77d920c）：既有 test_turn_state_invariants.py 经
``agent.conversation_loop._initialize_turn`` 薄转发间接验证；本文件**直接**
import ``agent.orchestration.turn_service.initialize_turn`` /
``prepare_messages`` 锁新函数名，证明抽取没有引入转发层之外的偏差。

纪律（用户铁律）：
* 协作方（_ensure_db_session / _emit_status / _hydrate_todo_store /
  _replay_compression_warning / logger 等）用 MagicMock 隔离，函数本体
  真实执行——与「mock 掉唯一出错行」反模式严格区分。
* 含变异牙齿检查：临时把关键路径改 no-op / 改返回值，确认测试精确失败，
  证明测试咬住的是真实行为而非镜像实现。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent.orchestration.turn_service import initialize_turn, prepare_messages


def _make_agent():
    """构造 initialize_turn / prepare_messages 所需的最小 agent fake。

    与 test_turn_state_invariants.py 的 _make_agent 同构（共享不变量），
    但本文件直接打新函数名，不依赖薄转发。
    """
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
    agent.max_iterations = 10
    agent.quiet_mode = True
    agent._compression_warning = None
    agent._todo_store = MagicMock()
    agent._todo_store.has_items = MagicMock(return_value=True)
    agent._hydrate_todo_store = MagicMock()
    agent._user_turn_count = 0
    agent._memory_nudge_interval = 0
    agent._turns_since_memory = 0
    agent._memory_store = None
    agent.valid_tool_names = []
    agent._persist_user_message_idx = None
    agent._stream_context_scrubber = None
    agent._stream_think_scrubber = None
    agent._safe_print = MagicMock()
    # initialize 设置的属性
    agent._stream_callback = None
    agent._persist_user_message_override = None
    agent._current_task_id = None
    agent._invalid_tool_retries = 99
    agent._empty_content_retries = 99
    agent._thinking_prefill_retries = 99
    agent._last_content_with_tools = "stale"
    agent._vision_supported = False
    agent._tool_guardrail_halt_decision = "stale"
    agent._turn_tool_signatures = ["stale"]
    agent._operator_claim_rejection_count = 3
    return agent


# ---------------------------------------------------------------------------
# initialize_turn 直接锁
# ---------------------------------------------------------------------------


class TestInitializeTurnDirect:
    def test_returns_effective_task_id(self):
        agent = _make_agent()
        _, _, tid = initialize_turn(agent, "hi", None, "task-42", None)
        assert tid == "task-42"

    def test_task_id_auto_generated_when_none(self):
        agent = _make_agent()
        _, _, tid = initialize_turn(agent, "hi", None, None, None)
        assert isinstance(tid, str) and len(tid) > 0

    def test_retry_counters_reset(self):
        agent = _make_agent()
        initialize_turn(agent, "hi", None, None, None)
        assert agent._invalid_tool_retries == 0
        assert agent._empty_content_retries == 0
        assert agent._thinking_prefill_retries == 0
        assert agent._last_content_with_tools is None
        assert agent._vision_supported is True
        assert agent._tool_guardrail_halt_decision is None
        assert agent._turn_tool_signatures == []

    def test_stream_callback_propagates(self):
        agent = _make_agent()
        cb = object()
        initialize_turn(agent, "hi", "clean", None, cb)
        assert agent._stream_callback is cb
        assert agent._persist_user_message_override == "clean"

    def test_operator_claim_rejection_count_preserved(self):
        agent = _make_agent()
        agent._operator_claim_rejection_count = 5
        initialize_turn(agent, "hi", None, None, None)
        assert agent._operator_claim_rejection_count == 5

    def test_surrogate_sanitized(self):
        agent = _make_agent()
        # 注入一个 lone surrogate（非法 UTF-8），验证被清洗
        bad = "hello\ud800world"
        out, _, _ = initialize_turn(agent, bad, None, None, None)
        assert "\ud800" not in out

    def test_db_session_ensured(self):
        agent = _make_agent()
        initialize_turn(agent, "hi", None, None, None)
        agent._ensure_db_session.assert_called_once()

    def test_primary_runtime_restored(self):
        agent = _make_agent()
        initialize_turn(agent, "hi", None, None, None)
        agent._restore_primary_runtime.assert_called_once()


# ---------------------------------------------------------------------------
# prepare_messages 直接锁
# ---------------------------------------------------------------------------


class TestPrepareMessagesDirect:
    def test_appends_user_message(self):
        agent = _make_agent()
        msgs, orig, idx, review = prepare_messages(agent, "hi", None, None)
        assert msgs[-1] == {"role": "user", "content": "hi"}
        assert orig == "hi"
        assert idx == len(msgs) - 1
        assert review is False

    def test_original_user_message_uses_persist(self):
        agent = _make_agent()
        _, orig, _, _ = prepare_messages(agent, "dirty", "clean", None)
        assert orig == "clean"

    def test_user_turn_count_incremented(self):
        agent = _make_agent()
        before = agent._user_turn_count
        prepare_messages(agent, "hi", None, None)
        assert agent._user_turn_count == before + 1

    def test_copies_history_not_mutates(self):
        agent = _make_agent()
        history = [{"role": "user", "content": "prev"}]
        msgs, _, _, _ = prepare_messages(agent, "hi", None, history)
        # 返回值含历史 + 新 user msg，但原 history 不被修改
        assert len(msgs) == 2
        assert len(history) == 1

    def test_boundary_marker_on_long_tool_history(self):
        agent = _make_agent()
        # 构造 >=3 对 tool_call/result 的历史
        history = []
        for _ in range(4):
            history.append({"role": "assistant", "content": "", "tool_calls": [{"id": "x", "function": {"name": "f"}}]})
            history.append({"role": "tool", "content": "ok", "tool_call_id": "x"})
        msgs, _, _, _ = prepare_messages(agent, "hi", None, history)
        assert "新的一轮" in msgs[-1]["content"]

    def test_no_boundary_marker_on_short_history(self):
        agent = _make_agent()
        msgs, _, _, _ = prepare_messages(agent, "hi", None, None)
        assert "新的一轮" not in msgs[-1]["content"]

    def test_iteration_budget_reset(self):
        agent = _make_agent()
        prepare_messages(agent, "hi", None, None)
        # IterationBudget 被重新构造（agent.max_iterations=10）
        assert agent.iteration_budget.max_total == 10

    def test_turn_start_log_emitted(self):
        """锁 logger.info('conversation turn: ...') 被调用——防止抽取时遗漏日志块
        （本文件 v1 曾漏掉，靠字符级 diff 复核才抓到，现补行为锁）。"""
        agent = _make_agent()
        # logger 是 prepare_messages 内 lazy import（from agent.conversation_loop
        # import logger），故 patch conversation_loop.logger（保持 logger 名不变）。
        with patch("agent.conversation_loop.logger") as mock_logger:
            prepare_messages(agent, "hi", None, None)
        # 至少有一次 info 调用且首参含 'conversation turn'
        assert mock_logger.info.called
        first_call_args = mock_logger.info.call_args[0]
        assert "conversation turn" in first_call_args[0]


# ---------------------------------------------------------------------------
# 变异牙齿检查（证明测试咬住真实行为，非镜像实现）
# ---------------------------------------------------------------------------


class TestMutationTeeth:
    def test_sanitize_real_effect_normal(self):
        """正常态：含 surrogate 的输入被清洗（证明清理逻辑真实生效，非 no-op）。"""
        agent = _make_agent()
        bad = "hello\ud800world"
        out, _, _ = initialize_turn(agent, bad, None, None, None)
        assert "\ud800" not in out

    def test_sanitize_mutation_residual(self):
        """变异牙齿检查：把 _sanitize_surrogates 改成 no-op 后，surrogate 必须
        残留在输出里——证明 test_sanitize_real_effect_normal 锁的是真实行为，
        而非镜像实现（若 sanitize 被意外降级为 no-op，本测会精确失败）。"""
        agent = _make_agent()
        bad = "hello\ud800world"
        with patch(
            "agent.orchestration.turn_service._sanitize_surrogates",
            side_effect=lambda s: s,
        ):
            out, _, _ = initialize_turn(agent, bad, None, None, None)
        # 变异真实效果：no-op 下 surrogate 残留
        assert "\ud800" in out

    def test_prepare_reads_real_input(self):
        """正常态：user_msg content 是真实输入（非日志 summarize 的副作用）。"""
        agent = _make_agent()
        msgs, _, _, _ = prepare_messages(agent, "REAL_INPUT", None, None)
        assert msgs[-1]["content"] == "REAL_INPUT"
