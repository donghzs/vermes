"""R5 反向验证：并发路径的 P4 verified 信号必须反映真实 self_validator 判定。

历史 bug：并发路径把 vr_ok 硬编码 True（fail-open），而串行路径用真实
``_vr.ok``。导致并发工具的 verified 永远乐观记 True，悄悄拉高 verified_rate。

本测试驱动**真实** ``execute_tool_calls_concurrent``（worker 线程跑完整逻辑），
断言：
- self_validator 判 False 时，并发工具记录的 ``__verified__`` 事件必须为 False。
- 若把并发路径退化成 ``vr_ok=True``（回归旧 bug），本测试必失败 → 证非真空通过。
- fail-open：self_validator 不可用时，verified 仍记 True 且不崩。

注意：P4 记录发生在 post-loop 的 :631（早于内容格式化/落盘），所以即便后处理
被 patch 成无副作用，verified 判定已被真实捕获——这正是我们要验的契约。
"""
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest


def _make_agent(monkeypatch):
    monkeypatch.setenv("VERMES_HOME", tempfile.mkdtemp(prefix="vermes_r5_"))
    import run_agent as _ra

    class _Stub:
        _interrupt_requested = False
        _interrupt_message = None
        _execution_thread_id = threading.current_thread().ident
        _interrupt_thread_signal_pending = False
        log_prefix = ""
        quiet_mode = True
        verbose_logging = False
        log_prefix_chars = 200
        _checkpoint_mgr = MagicMock(enabled=False)
        _subdirectory_hints = MagicMock()
        tool_progress_callback = None
        tool_start_callback = None
        tool_complete_callback = None
        _todo_store = MagicMock()
        _session_db = None
        valid_tool_names = set()
        _turns_since_memory = 0
        _iters_since_skill = 0
        _current_tool = None
        _last_activity = 0
        _print_fn = print
        _active_children: list = []
        iteration_budget = None
        disabled_toolsets = None

        def __init__(self):
            self._tool_worker_threads: set = set()
            self._tool_worker_threads_lock = threading.Lock()
            self._active_children_lock = threading.Lock()

        def _touch_activity(self, desc):
            self._last_activity = 1

        def _vprint(self, msg, force=False):
            pass

        def _safe_print(self, msg):
            pass

        def _should_emit_quiet_tool_messages(self):
            return False

        def _should_start_quiet_spinner(self):
            return False

        def _has_stream_consumers(self):
            return False

    stub = _Stub()
    # 后处理相关 stub：让 post-loop 不触碰真实磁盘 / 不抛
    stub._apply_pending_steer_to_tool_results = lambda *a, **kw: None
    stub._invoke_tool = MagicMock(side_effect=lambda *a, **kw: '{"ok": true}')
    stub._record_tool_signature = MagicMock()
    stub._record_file_mutation_result = MagicMock()
    # before_call 返回的 MagicMock 其 .allows_execution 默认 truthy → 不阻断
    stub._tool_guardrails = MagicMock()
    stub._append_guardrail_observation = MagicMock()
    stub._tool_result_content_for_active_model = lambda name, result: result
    stub._subdirectory_hints.check_tool_call.return_value = None
    return stub


class _FakeToolCall:
    def __init__(self, name, args="{}", call_id="tc_1"):
        self.function = MagicMock(name=name, arguments=args)
        self.function.name = name
        self.id = call_id


class _FakeAssistantMsg:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


class _FakeSelfValidator:
    """self_validator 桩：verify_tool_result 返回自身（带 .ok）。"""

    def __init__(self, ok: bool):
        self.ok = ok

    def verify_tool_result(self, *a, **k):
        return self

    def format_for_result(self, vr):
        return ""


def _capture_recorded(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "agent.raw_event.record_verification",
        lambda fn, verified, reason, ag: captured.append((fn, verified)),
    )
    return captured


def _drive(agent, monkeypatch, self_validator_factory, tool_name="tool_a"):
    """驱动真实并发执行器，返回该工具的 verified 判定列表（抓 __verified__ 事件）。

    ``self_validator_factory`` 在 worker 内被调用；传会抛异常的工厂即模拟
    self_validator 不可用（fail-open 分支）。
    """
    from agent.tool_executor import execute_tool_calls_concurrent

    captured = _capture_recorded(monkeypatch)
    ra_mock = MagicMock()
    ra_mock._set_interrupt = MagicMock()
    ra_mock._get_run_attr = MagicMock(return_value=False)
    with patch("agent.tool_executor._ra", ra_mock), \
         patch("agent.tool_executor._get_self_validator", self_validator_factory), \
         patch("agent.tool_executor.record_tool_outcome", lambda *a, **k: None), \
         patch("agent.tool_executor.maybe_persist_tool_result", lambda content, **k: content), \
         patch("agent.tool_executor.enforce_turn_budget", lambda *a, **k: None), \
         patch("agent.tool_executor._budget_for_agent", lambda ag: None):
        msg = _FakeAssistantMsg([_FakeToolCall(tool_name, call_id="tc_1")])
        execute_tool_calls_concurrent(agent, msg, [], "test_task")
    return [v for (fn, v) in captured if fn == tool_name]


class TestConcurrentVerifiedSignal:
    def test_false_when_self_validator_fails(self, monkeypatch):
        agent = _make_agent(monkeypatch)
        events = _drive(agent, monkeypatch, lambda: _FakeSelfValidator(ok=False))
        assert events, "并发路径应为 tool_a 记录 __verified__ 事件"
        assert events[0] is False, (
            "self_validator 判 False 必须使并发路径 verified=False；"
            "若此处为 True，说明回归成 vr_ok 硬编码 True 的旧 bug"
        )

    def test_fail_open_when_validator_unavailable(self, monkeypatch):
        agent = _make_agent(monkeypatch)

        def _boom(*a, **k):
            raise RuntimeError("self_validator 不可用")

        events = _drive(agent, monkeypatch, _boom, tool_name="tool_b")
        assert events, "并发路径应为 tool_b 记录 __verified__ 事件"
        assert events[0] is True, "self_validator 不可用必须 fail-open 记 True"
