"""G1b 增量 (b-2) 反向可验证测试：并发执行 + 每步隔离 AIAgent（§0.9.6）。

不依赖真实 LLM：用记录型 fake ``AIAgent``（monkeypatch ``run_agent.AIAgent``）模拟 B1 每步执行，
验证三件事：
  1. 并发真并行：同屏障就绪步经 asyncio.gather 在独立线程重叠执行（墙钟 < 串行和）。
  2. 每步隔离：每步由 _make_step_agent 构造**全新** AIAgent 实例，session_id 唯一（{parent}__wf_{step}），
     且传入的 conversation_history 是父 history 的 **deepcopy**（独立对象，父不被污染）。
  3. 承重墙（T4）：把工厂降级为「共享单 agent」变体 → T1 的隔离断言**必红** → 证明隔离是承重墙、
     非自愈假绿。T4 与 T1–T3 同在 b-2 提交（用户约束：承重墙测试必须随码落地，不后置）。

测试分两层：
  - TestKernelBarrier：纯内核（仅 import agent.workflow_scheduler），证明调度器屏障并发语义。
  - TestRealFactoryIsolation：走**真实** _make_workflow_step_executor + _make_step_agent（chat 蓝图），
    仅把底层 AIAgent 替换为 fake，验证真实工厂的隔离/深拷贝契约。
"""

import asyncio
import copy
import threading
import time

import pytest

from agent.workflow_scheduler import StepExecResult, WorkflowScheduler

# ── 全局记录器：fake agent 每次 run_conversation 追加一条记录 ──────────────────
_RECORDS = []
_SLEEP = 0.05


class _FakeAgent:
    """记录型 fake：构造参数 + run_conversation 的线程/history/session 信息全部落盘到 _RECORDS。"""

    def __init__(self, **kwargs):
        self.session_id = kwargs.get("session_id")
        self._kwargs = kwargs

    def run_conversation(self, user_message, conversation_history=None, stream_callback=None, **kw):
        _start = time.monotonic()
        time.sleep(_SLEEP)
        rec = {
            "session_id": self.session_id,
            "agent_id": id(self),
            "thread_id": threading.get_ident(),
            "history_id": id(conversation_history),
            "history_obj": conversation_history,
            # 注入前快照（T2 内容等价用）；conversation_history 可能为 None（父对话仅 1 条时）
            "history_pre": list(conversation_history) if conversation_history is not None else None,
            "start": _start,  # 必须在 sleep 前记录，否则量不到真实并发区间
        }
        # 注入哨兵：若 history 是父引用（未 deepcopy），父会被污染 → T2 可检出。
        try:
            if conversation_history is not None:
                conversation_history.append({"role": "injected", "sid": self.session_id})
        except Exception:
            pass
        rec["end"] = time.monotonic()
        _RECORDS.append(rec)
        return {"final_response": f"ok-{self.session_id}"}


class _MemBackend:
    """内存版 PlanBackend：避免 SQLite，纯内核验证用。"""

    def __init__(self, plan, todo):
        self._plan = copy.deepcopy(plan)
        self._todo = dict(todo)

    async def load(self, session_id):
        return (self._plan, dict(self._todo))

    async def save(self, session_id, plan_json, todo_states):
        self._plan = copy.deepcopy(plan_json)
        self._todo = dict(todo_states)


def _make_plan(n=2, deps=None):
    deps = deps or {}
    return {
        "steps": [
            {
                "id": f"s{i}",
                "title": f"step{i}",
                "description": f"d{i}",
                "dependencies": deps.get(f"s{i}", []),
                "status": "pending",
            }
            for i in range(1, n + 1)
        ]
    }


def _todo_for(plan):
    return {s["id"]: "pending" for s in plan["steps"]}


def _assert_isolated(records):
    """T1 隔离不变量：两就绪步必须实例不同 / session 不同 / 线程不同（真并行）。"""
    assert len(records) >= 2
    assert records[0]["agent_id"] != records[1]["agent_id"]
    assert records[0]["session_id"] != records[1]["session_id"]
    assert records[0]["thread_id"] != records[1]["thread_id"]


# ─────────────────────────────────────────────────────────────────────────────
# 内核层：屏障并发语义（不依赖 chat 蓝图）
# ─────────────────────────────────────────────────────────────────────────────


class TestKernelBarrier:
    def test_concurrent_overlap(self, monkeypatch):
        global _RECORDS, _SLEEP
        _RECORDS = []
        _SLEEP = 0.2
        plan = _make_plan(2)
        backend = _MemBackend(plan, _todo_for(plan))

        async def _exec(step, ctx):
            loop = asyncio.get_running_loop()

            def _block():
                _FakeAgent().run_conversation("x", conversation_history=[], stream_callback=None)
                return StepExecResult(status="completed")

            # 模拟真实 executor：阻塞体丢线程池，事件循环不阻塞
            return await loop.run_in_executor(None, _block)

        sched = WorkflowScheduler(backend=backend, step_executor=_exec, max_concurrency=4)
        t0 = time.monotonic()
        asyncio.run(sched.execute("s", concurrent=True))
        dur = time.monotonic() - t0
        assert len(_RECORDS) == 2
        r0, r1 = _RECORDS
        # 真并行：两段墙钟区间交叉
        assert r0["start"] < r1["end"] and r1["start"] < r0["end"]
        assert dur < 0.5  # 并行 ≈ 0.2，而非串行 0.4

    def test_serial_no_overlap(self, monkeypatch):
        global _RECORDS, _SLEEP
        _RECORDS = []
        _SLEEP = 0.2
        plan = _make_plan(2)
        backend = _MemBackend(plan, _todo_for(plan))

        async def _exec(step, ctx):
            loop = asyncio.get_running_loop()

            def _block():
                _FakeAgent().run_conversation("x", conversation_history=[], stream_callback=None)
                return StepExecResult(status="completed")

            return await loop.run_in_executor(None, _block)

        sched = WorkflowScheduler(backend=backend, step_executor=_exec, max_concurrency=4)
        t0 = time.monotonic()
        asyncio.run(sched.execute("s", concurrent=False))
        dur = time.monotonic() - t0
        assert len(_RECORDS) == 2
        assert dur > 0.35  # 串行 ≈ 0.4


# ─────────────────────────────────────────────────────────────────────────────
# 真实工厂层：走 chat._make_workflow_step_executor + _make_step_agent（仅底层 AIAgent 替换）
# ─────────────────────────────────────────────────────────────────────────────

try:
    import vermes_cli.blueprints.chat as _chat

    _HAVE_CHAT = True
except Exception:  # pragma: no cover - 受限环境下跳过，不影响内核层
    _HAVE_CHAT = False


@pytest.mark.skipif(not _HAVE_CHAT, reason="chat blueprint not importable in this env")
class TestRealFactoryIsolation:
    def _run(self, concurrent, monkeypatch, sleep=0.05, n=2):
        global _RECORDS, _SLEEP
        _RECORDS = []
        _SLEEP = sleep
        monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)
        parent = _FakeParent()
        conv = [{"role": "user", "content": "hi"}]
        plan = _make_plan(n)
        backend = _MemBackend(plan, _todo_for(plan))
        executor = _chat._make_workflow_step_executor(parent, conv, "msg", "sess-p")
        sched = WorkflowScheduler(backend=backend, step_executor=executor, max_concurrency=4)
        t0 = time.monotonic()
        asyncio.run(sched.execute("sess-p", concurrent=concurrent))
        return time.monotonic() - t0

    def test_t1_concurrent_isolated(self, monkeypatch):
        """T1：并发 + 隔离。同屏障两步 → 实例/线程/session 全不同，session 含父前缀。"""
        self._run(True, monkeypatch)
        assert len(_RECORDS) == 2
        _assert_isolated(_RECORDS)
        # 每步唯一 session_id = {parent}__wf_{step_id}
        assert sorted(r["session_id"] for r in _RECORDS) == [
            "sess-p__wf_s1",
            "sess-p__wf_s2",
        ]

    def test_t2_deepcopy_independent(self, monkeypatch):
        """T2：只读共享态。step 收到的 history 是父 history 的 deepcopy（独立对象、父不被污染）。"""
        global _RECORDS
        _RECORDS = []
        monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)
        parent = _FakeParent()
        conv = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok"},
        ]
        plan = _make_plan(2)
        backend = _MemBackend(plan, _todo_for(plan))
        executor = _chat._make_workflow_step_executor(parent, conv, "msg", "sess-p")
        sched = WorkflowScheduler(backend=backend, step_executor=executor, max_concurrency=4)
        asyncio.run(sched.execute("sess-p", concurrent=True))

        for rec in _RECORDS:
            # 独立对象：既不是父全量，也不是父切片（证明 deepcopy 新建）
            assert rec["history_id"] != id(conv)
            assert rec["history_id"] != id(conv[:-1])
            # 内容等价（deepcopy 语义，用注入前快照比对，排除 fake 自身哨兵注入干扰）
            assert rec["history_pre"] == conv[:-1]
        # 父 conv 未被哨兵污染 → 证明传的是副本而非引用
        assert not any(m.get("role") == "injected" for m in conv)

    def test_t3_wallclock_concurrent_faster(self, monkeypatch):
        """T3：并发墙钟显著低于串行（证明 gather 真并发，而非假并行）。"""
        serial = self._run(False, monkeypatch, sleep=0.2)
        concurrent = self._run(True, monkeypatch, sleep=0.2)
        assert concurrent < serial * 0.8
        assert concurrent < 0.4
        assert serial > 0.38

    def test_t4_shared_agent_load_bearing(self, monkeypatch):
        """T4 承重墙：共享单 agent 变体必须打破 T1 隔离不变量（断言必红）。

        证明隔离是承重墙：一旦把「每步隔离工厂」退化为「共享单 agent」，T1 的
        distinct-instance / distinct-session 断言即失败；恢复隔离后绿。
        """
        global _RECORDS, _SLEEP, _shared
        _RECORDS = []
        _SLEEP = 0.05
        _shared = None
        monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)

        def _shared_factory(parent_agent, step, parent_session_id):
            global _shared
            if _shared is None:
                _shared = _FakeAgent(session_id="shared-sess", parent_session_id=parent_session_id)
            return _shared

        monkeypatch.setattr(_chat, "_make_step_agent", _shared_factory)
        parent = _FakeParent()
        plan = _make_plan(2)
        backend = _MemBackend(plan, _todo_for(plan))
        executor = _chat._make_workflow_step_executor(
            parent, [{"role": "user", "content": "hi"}], "msg", "sess-p"
        )
        sched = WorkflowScheduler(backend=backend, step_executor=executor, max_concurrency=4)
        asyncio.run(sched.execute("sess-p", concurrent=True))

        # 共享变体：隔离被打破（同一实例、同一 session）
        assert _RECORDS[0]["agent_id"] == _RECORDS[1]["agent_id"]
        assert _RECORDS[0]["session_id"] == _RECORDS[1]["session_id"]
        # T1 的隔离断言在此变体下必红（承重墙证据）
        with pytest.raises(AssertionError):
            _assert_isolated(_RECORDS)


class _FakeParent:
    """父 agent 桩：提供 _make_step_agent 所需的全部构造属性 + 8 个 SSE 回调。"""

    def __init__(self):
        self.base_url = "http://x"
        self.api_key = "k"
        self.provider = "p"
        self.model = "m"
        self.max_iterations = 10
        self.platform = "web"
        self.enabled_toolsets = None
        self.disabled_toolsets = None
        self.ephemeral_system_prompt = "sys"
        self.reasoning_config = None
        self.interaction_mode = None
        self._evo_base_prompt = None
        self.session_id = "sess-p"
        # 8 个 SSE 回调（约束④ 透传），缺省 None 也可；此处给个 no-op 以覆盖非空分支
        self.status_callback = lambda et, m: None
        self.plan_event_callback = None
        self.stream_delta_callback = None
        self.tool_progress_callback = None
        self.tool_start_callback = None
        self.tool_complete_callback = None
        self.thinking_callback = None
        self.reasoning_callback = None
