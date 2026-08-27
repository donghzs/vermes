"""G1b 增量 (a)：WorkflowScheduler 串行执行器 —— 反向可验证测试。

纪律（沿用 §7 铁律）：
- 不 mock 调度器本身；注入内存 fake backend + fake executor，测真实编排逻辑。
- 反向验证：临时把依赖门控 compute_ready_steps 改成「恒返回所有 pending」，
  证明生产断言（依赖顺序 s1 先于 s2）会红 → 恢复后绿，说明门控是承重墙。
"""

import asyncio
import time

import pytest

import agent.workflow_scheduler as ws
from agent.workflow_scheduler import (
    StepExecResult,
    WorkflowScheduler,
    WorkflowResult,
)


class MemBackend:
    """内存 PlanBackend（reducer #1 落点），单测不碰真实 SQLite。"""

    def __init__(self):
        self._store: dict = {}

    def put(self, session_id: str, plan_json: dict, todo_states: dict) -> None:
        # 深拷贝隔离，模拟持久化
        self._store[session_id] = (
            {"steps": [dict(s) for s in plan_json.get("steps", [])]},
            dict(todo_states),
        )

    async def load(self, session_id: str):
        plan_json, todo_states = self._store[session_id]
        return {"steps": [dict(s) for s in plan_json["steps"]]}, dict(todo_states)

    async def save(self, session_id: str, plan_json: dict, todo_states: dict) -> None:
        self._store[session_id] = (
            {"steps": [dict(s) for s in plan_json.get("steps", [])]},
            dict(todo_states),
        )


class FakeExecutor:
    """步骤执行体（注入）。normal 完成；fail_set 中的步抛异常 → failed。

    work_seconds>0 时 sleep，用于验证并发路径真的并行（而非串行退化）。
    """

    def __init__(self, fail_set=None, work_seconds=0.0):
        self.fail_set = set(fail_set or [])
        self.work_seconds = work_seconds
        self.calls: list = []

    async def __call__(self, step, ctx):
        sid = step["id"]
        self.calls.append(("start", sid, time.monotonic()))
        if sid in self.fail_set:
            raise RuntimeError(f"step {sid} boomed")
        if self.work_seconds:
            await asyncio.sleep(self.work_seconds)
        self.calls.append(("end", sid, time.monotonic()))
        return StepExecResult(status="completed", outputs={"did": sid})


def _plan(steps):
    return {"steps": steps}


def _run(plan, executor, concurrent=False, max_concurrency=4) -> WorkflowResult:
    backend = MemBackend()
    backend.put("sess", plan, {})
    sched = WorkflowScheduler(backend, executor, max_concurrency=max_concurrency)
    result = asyncio.run(sched.execute("sess", concurrent=concurrent))
    result._backend = backend  # 测试用：便于查落盘状态
    return result


def test_serial_respects_dependency_order():
    plan = _plan([
        {"id": "s1", "dependencies": []},
        {"id": "s2", "dependencies": ["s1"]},
        {"id": "s3", "dependencies": ["s2"]},
    ])
    res = _run(plan, FakeExecutor())
    assert res.exec_order == ["s1", "s2", "s3"], res.exec_order
    assert len(res.results) == 3
    assert all(r["status"] == "completed" for r in res.results)


def test_serial_parallel_layers_ordered():
    # a,b 无依赖（同层），c 依赖 a,b → c 必在 a,b 之后
    plan = _plan([
        {"id": "a", "dependencies": []},
        {"id": "b", "dependencies": []},
        {"id": "c", "dependencies": ["a", "b"]},
    ])
    res = _run(plan, FakeExecutor())
    assert res.exec_order.index("c") == 2
    assert res.exec_order.index("a") < 2
    assert res.exec_order.index("b") < 2
    assert not res.deadlocked


def test_status_persisted_to_backend():
    plan = _plan([
        {"id": "s1", "dependencies": []},
        {"id": "s2", "dependencies": ["s1"]},
    ])
    res = _run(plan, FakeExecutor())
    _, todo = res._backend._store["sess"]
    assert todo == {"s1": "completed", "s2": "completed"}


def test_deadlock_propagates_skip():
    # s1 失败 → s2 依赖 s1 应被标 skipped，调度器返回 deadlocked
    plan = _plan([
        {"id": "s1", "dependencies": []},
        {"id": "s2", "dependencies": ["s1"]},
    ])
    res = _run(plan, FakeExecutor(fail_set={"s1"}))
    assert res.deadlocked is True
    _, todo = res._backend._store["sess"]
    assert todo["s1"] == "failed"
    assert todo["s2"] == "skipped"


def test_results_aggregation_under_lock():
    plan = _plan([
        {"id": "s1", "dependencies": []},
        {"id": "s2", "dependencies": ["s1"]},
    ])
    res = _run(plan, FakeExecutor())
    by_id = {r["step_id"]: r for r in res.results}
    assert set(by_id) == {"s1", "s2"}
    assert by_id["s1"]["outputs"] == {"did": "s1"}


def test_concurrent_ready_steps_run_overlapping():
    # 并发路径 sanity：a,b 无依赖，各 sleep 0.1s。
    # concurrent=True → 两者重叠（同时起步），总墙钟 < 串行 0.2s。
    plan = _plan([
        {"id": "a", "dependencies": []},
        {"id": "b", "dependencies": []},
    ])
    backend = MemBackend()
    backend.put("sess", plan, {})
    ex = FakeExecutor(work_seconds=0.1)
    sched = WorkflowScheduler(backend, ex, max_concurrency=4)
    t0 = time.monotonic()
    res = asyncio.run(sched.execute("sess", concurrent=True))
    elapsed = time.monotonic() - t0
    # 重叠判定：所有「start」发生在任何「end」之前
    starts = [t for kind, _, t in ex.calls if kind == "start"]
    ends = [t for kind, _, t in ex.calls if kind == "end"]
    assert max(starts) < min(ends), "并发步未重叠 → 退化串行"
    assert elapsed < 0.19, f"并发墙钟 {elapsed:.3f}s 接近串行，疑似退化"
    assert res.exec_order[0] in ("a", "b")


def test_reverse_gate_is_load_bearing():
    """反向验证：去掉依赖门控后，依赖顺序断言必红 → 证明门控是承重墙。

    构造 s2 在 dict 中排在 s1 前、但 s2 依赖 s1 的计划。
    真门控：s2 被挡 → exec_order 必以 s1 开头（== ['s1','s2']）。
    破门控（恒返回所有 pending）：s2 先跑 → exec_order == ['s2','s1']，
    于是生产断言 assert == ['s1','s2'] 必抛 AssertionError。
    """
    plan_s2_first = _plan([
        {"id": "s2", "dependencies": ["s1"]},  # 故意排在 s1 前
        {"id": "s1", "dependencies": []},
    ])
    # 1) 真门控：s1 必须先跑
    res_real = _run(plan_s2_first, FakeExecutor())
    assert res_real.exec_order == ["s1", "s2"], res_real.exec_order

    # 2) 破门控：把 compute_ready_steps 改成「恒返回所有 pending」
    orig = ws.compute_ready_steps
    ws.compute_ready_steps = lambda plan, todo=None: [
        s["id"] for s in plan["steps"]
        if (todo or {}).get(s["id"], s.get("status", "pending")) == "pending"
    ]
    try:
        res_broken = _run(plan_s2_first, FakeExecutor())
        # 破门控下 s2（dict 中靠前）先跑 → 生产断言必红
        with pytest.raises(AssertionError):
            assert res_broken.exec_order == ["s1", "s2"]
        # 且确实以 s2 开头（确认是「顺序乱了」而非别的失败）
        assert res_broken.exec_order[0] == "s2"
    finally:
        ws.compute_ready_steps = orig  # 恢复

    # 3) 恢复后真门控仍绿
    res_restored = _run(plan_s2_first, FakeExecutor())
    assert res_restored.exec_order == ["s1", "s2"]
