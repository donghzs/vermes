"""G2 断点续跑 / 失败语义（§0.10）反向可验证测试。

覆盖四个真实缺口：
  T1 死锁 skip + resume 幂等（已终态步 resume 不重跑）
  T2 retry 预算（前 N 次失败、第 N+1 次成功 → completed；budget=0 恒失败 → failed）
  T3 断点续跑（预设已完成态 → 只跑 pending 步，completed 步不重跑）
  T4 人工 skip 下游传播（mark_skipped → 依赖步静态 skip）

不 import chat.py（避开重 conftest），直接驱动 agent.workflow_scheduler 内核。
"""

import asyncio

from agent.workflow_scheduler import (
    StepExecResult,
    WorkflowScheduler,
)


class _MemBackend:
    """可预设初始状态的持久化后端（模拟进程崩溃后残留的 plan+todo）。"""

    def __init__(self, plan, todo):
        self._plan = plan
        self._todo = dict(todo)

    async def load(self, session_id):
        return (self._plan, dict(self._todo))

    async def save(self, session_id, plan_json, todo_states):
        self._plan = plan_json
        self._todo = dict(todo_states)


def _mk_plan(n=3, deps=None):
    steps = []
    for i in range(1, n + 1):
        d = deps[i - 1] if deps else ([] if i == 1 else [f"s{i - 1}"])
        steps.append(
            {
                "id": f"s{i}",
                "title": f"t{i}",
                "description": f"d{i}",
                "dependencies": d,
                "status": "pending",
            }
        )
    return {"steps": steps}


def _todo_for(plan, states=None):
    states = states or {}
    return {s["id"]: states.get(s["id"], "pending") for s in plan["steps"]}


async def _noop_exec(step, ctx):
    return StepExecResult(status="completed", outputs={})


# ── T1：死锁 skip + resume 幂等 ────────────────────────────────────────────
def test_deadlock_skip_and_resume_idempotent():
    plan = _mk_plan(3)
    calls = {"n": 0}

    async def _exec(step, ctx):
        calls["n"] += 1
        if step["id"] == "s1":
            raise RuntimeError("boom")  # 首步失败 → 下游应被 skip
        return StepExecResult(status="completed", outputs={"ok": True})

    backend = _MemBackend(plan, _todo_for(plan))
    s = WorkflowScheduler(backend=backend, step_executor=_exec)
    r = asyncio.run(s.execute("sid", concurrent=False))
    assert r.deadlocked is True
    _, st = asyncio.run(backend.load("sid"))
    assert st == {"s1": "failed", "s2": "skipped", "s3": "skipped"}, st

    # resume：无 pending → 不重跑任何步（completed/failed/skipped 都不重跑）
    calls["n"] = 0
    r2 = asyncio.run(s.resume("sid"))
    assert r2.exec_order == [], r2.exec_order
    assert calls["n"] == 0, "resume 不应重跑已终态步"


# ── T2：retry 预算 ────────────────────────────────────────────────────────
def test_retry_budget_succeeds_after_n_failures():
    plan = _mk_plan(1)
    state = {"fails": 0}
    N = 2  # 前 2 次失败，第 3 次成功 → 需要 budget >= 2

    async def _exec(step, ctx):
        if state["fails"] < N:
            state["fails"] += 1
            raise RuntimeError("transient")
        return StepExecResult(status="completed", outputs={"ok": True})

    backend = _MemBackend(plan, _todo_for(plan))
    s = WorkflowScheduler(backend=backend, step_executor=_exec, retry_budget=N)
    r = asyncio.run(s.execute("sid", concurrent=False))
    assert r.exec_order == ["s1"], r.exec_order
    assert r.results[0]["status"] == "completed", r.results
    # 1 次初次 + N 次重试 = N+1 次尝试
    assert r.results[0]["attempts"] == N + 1, r.results[0]


def test_retry_budget_exhausted_stays_failed():
    plan = _mk_plan(1)

    async def _exec(step, ctx):
        raise RuntimeError("always")

    backend = _MemBackend(plan, _todo_for(plan))
    s = WorkflowScheduler(backend=backend, step_executor=_exec, retry_budget=0)
    r = asyncio.run(s.execute("sid", concurrent=False))
    assert r.results[0]["status"] == "failed", r.results
    assert r.results[0]["attempts"] == 1, r.results[0]  # budget=0 → 仅 1 次尝试


def test_retry_budget_required_for_recovery():
    """承重墙（控制变量）：budget=0 时，『失败后恢复』不成立（单次失败即终态）。"""
    plan = _mk_plan(1)
    state = {"fails": 0}

    async def _exec(step, ctx):
        if state["fails"] < 1:
            state["fails"] += 1
            raise RuntimeError("transient")
        return StepExecResult(status="completed", outputs={"ok": True})

    backend = _MemBackend(plan, _todo_for(plan))
    # 强制 budget=0：模拟『掏空重试逻辑』
    s = WorkflowScheduler(backend=backend, step_executor=_exec, retry_budget=0)
    r = asyncio.run(s.execute("sid"))
    assert r.results[0]["status"] == "failed", r.results  # 无重试 → 直接 failed


# ── T3：断点续跑（预设已完成态）────────────────────────────────────────────
def test_resume_runs_only_pending():
    plan = _mk_plan(3)
    # 模拟「进程崩在 s1 完成后」：持久化态 s1=completed, s2/s3=pending
    backend = _MemBackend(plan, {"s1": "completed", "s2": "pending", "s3": "pending"})
    ran = []

    async def _exec(step, ctx):
        ran.append(step["id"])
        return StepExecResult(status="completed", outputs={"ok": True})

    s = WorkflowScheduler(backend=backend, step_executor=_exec)
    r = asyncio.run(s.resume("sid"))
    # s1 不重跑，只跑 s2/s3
    assert r.exec_order == ["s2", "s3"], r.exec_order
    assert "s1" not in ran, "completed 步不应被续跑重跑"
    _, st = asyncio.run(backend.load("sid"))
    assert st == {"s1": "completed", "s2": "completed", "s3": "completed"}, st


# ── T4：人工 skip 下游传播 ─────────────────────────────────────────────────
def test_mark_skipped_propagates_downstream():
    plan = _mk_plan(3)
    backend = _MemBackend(plan, _todo_for(plan))
    asyncio.run(
        WorkflowScheduler(backend=backend, step_executor=_noop_exec).mark_skipped("sid", "s2")
    )
    _, st = asyncio.run(backend.load("sid"))
    assert st["s2"] == "skipped", st
    assert st["s3"] == "skipped", st  # s3 依赖 s2 → 被静态传播 skip


def test_retry_step_resets_failed_to_pending():
    """retry_step：把 failed 步重置 pending 后再 execute 重跑。"""
    plan = _mk_plan(3)
    backend = _MemBackend(plan, {"s1": "completed", "s2": "failed", "s3": "skipped"})
    ran = []

    async def _exec(step, ctx):
        ran.append(step["id"])
        return StepExecResult(status="completed", outputs={"ok": True})

    s = WorkflowScheduler(backend=backend, step_executor=_exec)
    asyncio.run(s.retry_step("sid", "s2"))
    # s2 被重置并重跑；s1 已完成不重跑；s3 因 s2 完成重新解锁
    assert "s2" in ran, ran
    assert "s1" not in ran, "completed 步不应被重跑"
    _, st = asyncio.run(backend.load("sid"))
    assert st["s2"] == "completed", st
    assert st["s3"] == "completed", st  # s3 原 skipped 因 s2 完成重新解锁
