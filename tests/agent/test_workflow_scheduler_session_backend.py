"""G1b 增量 (b-1) 反向可验证测试：真实 SessionPlanBackend（session_plan_store）接线。

不依赖 LLM：用 fake step_executor 模拟 B1（每步直接标完成），验证
「WorkflowScheduler.execute → SessionPlanBackend → session_plan_store 落盘」
这一真实持久化链路，以及依赖门控是承重墙（反向验证）。

对照：G1b(a) 测试用内存 fake backend；本文件用真实 SQLite backend，证明 b-1 的
活链路持久化接线是对的（设计稿 §0.8.7 b-1 验证点）。
"""

import importlib

import pytest

import agent.session_plan_store as sps
from agent.workflow_scheduler import (
    SessionPlanBackend,
    StepExecResult,
    WorkflowScheduler,
    compute_ready_steps,
)


def _make_plan():
    return {
        "steps": [
            {
                "id": "s1",
                "title": "抓取",
                "description": "抓取网页数据",
                "dependencies": [],
                "status": "pending",
            },
            {
                "id": "s2",
                "title": "清洗",
                "description": "清洗数据",
                "dependencies": ["s1"],
                "status": "pending",
            },
            {
                "id": "s3",
                "title": "报告",
                "description": "生成报告",
                "dependencies": ["s1", "s2"],
                "status": "pending",
            },
        ]
    }


def _record_executor(order, failed=None):
    """fake B1 executor：记录执行顺序，按 step_id 决定是否失败。"""
    failed = failed or set()

    async def _exec(step, ctx):
        order.append(step["id"])
        if step["id"] in failed:
            raise RuntimeError(f"step {step['id']} exploded")
        return StepExecResult(status="completed", outputs={"ok": True})

    return _exec


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    sps._DB_PATH = tmp_path / "session_plans.db"
    yield
    sps._DB_PATH = None


def test_session_plan_backend_roundtrip(tmp_path):
    """真实 SQLite 后端：plan_json + todo_states 经 save→load 往返不丢。"""
    backend = SessionPlanBackend()
    plan = _make_plan()
    todo = {"s1": "completed", "s2": "in_progress", "s3": "pending"}
    import asyncio

    asyncio.run(backend.save("sess-rt", plan, todo))

    loaded_plan, loaded_todo = asyncio.run(backend.load("sess-rt"))
    assert loaded_plan["steps"][1]["id"] == "s2"
    assert loaded_todo == todo
    # 依赖边必须贯通到后端（M1a 成果不被 b-1 吞掉）
    assert loaded_plan["steps"][1]["dependencies"] == ["s1"]


def test_scheduler_execute_with_session_backend(tmp_path):
    """真实后端 + fake executor：依赖顺序 s1→s2→s3，且最终态落盘。"""
    import asyncio

    plan = _make_plan()
    sps.save_plan_state("sess-1", plan, {"s1": "pending", "s2": "pending", "s3": "pending"}, True)

    backend = SessionPlanBackend()
    order = []
    scheduler = WorkflowScheduler(backend=backend, step_executor=_record_executor(order))
    result = asyncio.run(scheduler.execute("sess-1", concurrent=False))

    # 依赖顺序正确：s1 先于 s2 先于 s3
    assert order.index("s1") < order.index("s2") < order.index("s3")
    assert result.exec_order == ["s1", "s2", "s3"]
    assert result.deadlocked is False

    # 落盘验证：重开后端读到全 completed
    reloaded_plan, reloaded_todo = asyncio.run(backend.load("sess-1"))
    assert reloaded_todo == {"s1": "completed", "s2": "completed", "s3": "completed"}


def test_scheduler_deadlock_skip_with_session_backend(tmp_path):
    """s1 失败 → s2/s3 被静态传播为 skipped，落盘。"""
    import asyncio

    plan = _make_plan()
    sps.save_plan_state("sess-dl", plan, {"s1": "pending", "s2": "pending", "s3": "pending"}, True)

    backend = SessionPlanBackend()
    order = []
    scheduler = WorkflowScheduler(backend=backend, step_executor=_record_executor(order, failed={"s1"}))
    result = asyncio.run(scheduler.execute("sess-dl", concurrent=False))

    assert result.deadlocked is True
    reloaded_plan, reloaded_todo = asyncio.run(backend.load("sess-dl"))
    # s1 失败，s2/s3 因依赖失败被 skip（G2 地基）
    assert reloaded_todo["s1"] == "failed"
    assert reloaded_todo["s2"] == "skipped"
    assert reloaded_todo["s3"] == "skipped"


def test_dependency_order_is_load_bearing(tmp_path, monkeypatch):
    """反向验证：依赖门控是承重墙。

    - 正确门控：s2 永远不能在 s1 未完成时执行 → 顺序 s1→s2→s3。
    - 破门控（compute_ready_steps 返回反向列表，无视依赖）：s2 会先于 s1 执行。
    证明「顺序正确」这一断言依赖门控，而非巧合。
    """
    import asyncio

    plan = _make_plan()
    sps.save_plan_state("sess-rv", plan, {"s1": "pending", "s2": "pending", "s3": "pending"}, True)

    # ① 正确门控
    backend = SessionPlanBackend()
    order_ok = []
    sch_ok = WorkflowScheduler(backend=backend, step_executor=_record_executor(order_ok))
    asyncio.run(sch_ok.execute("sess-rv", concurrent=False))
    assert order_ok.index("s1") < order_ok.index("s2") < order_ok.index("s3")

    # ② 破门控：仍按「已完成则跳过」过滤 ts（否则 execute 循环会重跑已完成的步 → 死循环），
    #    但无视依赖、把剩余 pending 步按 id 逆序返回 → s3/s2 会在 s1 未完成前被执行。
    def _broken_gate(p, ts=None):
        ts = ts or {}
        pending = [
            s["id"]
            for s in p["steps"]
            if ts.get(s["id"], s.get("status", "pending")) == "pending"
        ]
        return pending[::-1]

    monkeypatch.setattr("agent.workflow_scheduler.compute_ready_steps", _broken_gate)
    # 重置 DB 状态
    sps.save_plan_state("sess-rv", plan, {"s1": "pending", "s2": "pending", "s3": "pending"}, True)
    order_broken = []
    sch_broken = WorkflowScheduler(backend=backend, step_executor=_record_executor(order_broken))
    asyncio.run(sch_broken.execute("sess-rv", concurrent=False))
    # 破门控下，s2/s3 会在 s1 之前执行（依赖未被尊重）
    assert order_broken.index("s2") < order_broken.index("s1")
