"""G4 步骤间数据流 · 反向可验证测试。

覆盖 §0.12.4 验证计划：
- T1 注入：已完成依赖步 outputs → 下游 step.inputs 聚合
- T2 prompt 渲染：_build_workflow_step_prompt 把 inputs 渲染为「已知前置产物」
- T3 隔离（承重墙）：下游改 inputs 不污染上游 outputs（deepcopy 隔离）
- T4 向后兼容：旧 plan 无 outputs/inputs 字段 → 视为空，不报错

反向承重墙内嵌：
- 去掉 _gather_inputs 的聚合 → T1 必红（s2.inputs 空）
- 把 deepcopy 改成共享引用 → T3 必红（s1.outputs 被下游改污染）
"""

import asyncio

from agent.workflow_scheduler import StepExecResult, WorkflowScheduler


class _MemBackend:
    """内存 PlanBackend：保留 plan_json / todo_states 引用，供断言读取。"""

    def __init__(self, plan, todo):
        self._plan = plan
        self._todo = todo

    async def load(self, session_id):
        return (self._plan, self._todo)

    async def save(self, session_id, plan_json, todo_states):
        self._plan = plan_json
        self._todo = todo_states


def _make_plan():
    """无预设 outputs：executor 跑完后由 _run_step 写入 outputs（验证真实聚合链路）。"""
    return {
        "steps": [
            {"id": "s1", "title": "a", "description": "a", "dependencies": [], "status": "pending"},
            {"id": "s2", "title": "b", "description": "b", "dependencies": ["s1"], "status": "pending"},
            {
                "id": "s3",
                "title": "c",
                "description": "c",
                "dependencies": ["s1", "s2"],
                "status": "pending",
            },
        ]
    }


def _make_executor(captured):
    """executor 记录每步聚合到的 inputs，并返回带 summary 的产物（供下游消费）。"""

    async def _exec(step, ctx):
        captured[step["id"]] = step.get("inputs", {})
        return StepExecResult(
            status="completed",
            outputs={"summary": f"{step['id']} done", "final_response": f"{step['id']} done"},
        )

    return _exec


def test_gather_inputs_injected():
    """T1 注入：s1 产物 → s2.inputs 含 s1.outputs；s3 依赖 s1+s2 均注入。"""
    plan = _make_plan()
    todo = {s["id"]: "pending" for s in plan["steps"]}
    captured = {}
    backend = _MemBackend(plan, todo)
    scheduler = WorkflowScheduler(backend=backend, step_executor=_make_executor(captured))
    asyncio.run(scheduler.execute("sid", concurrent=False))

    # 反向承重墙：若 _gather_inputs 改为 return {} → s2.inputs 空 → 此断言必红
    assert "s1" in captured["s2"], captured["s2"]
    assert captured["s2"]["s1"]["summary"] == "s1 done", captured["s2"]
    assert captured["s2"]["s1"]["final_response"] == "s1 done", captured["s2"]
    # s3 同时含 s1、s2 上游产物
    assert "s1" in captured["s3"] and "s2" in captured["s3"], captured["s3"]
    assert captured["s3"]["s2"]["summary"] == "s2 done", captured["s3"]


def test_inputs_isolation_deepcopy():
    """T3 隔离（承重墙）：下游改 inputs 不污染上游 outputs（deepcopy 隔离）。"""
    plan = _make_plan()
    todo = {s["id"]: "pending" for s in plan["steps"]}
    captured = {}
    backend = _MemBackend(plan, todo)
    scheduler = WorkflowScheduler(backend=backend, step_executor=_make_executor(captured))
    asyncio.run(scheduler.execute("sid", concurrent=False))

    # 改写 captured 里 s2 对 s1 的引用副本
    captured["s2"]["s1"]["summary"] = "MUTATED"
    captured["s2"]["s1"].setdefault("artifacts", []).append("MUTATED_ART")

    s1 = next(s for s in plan["steps"] if s["id"] == "s1")
    # 反向承重墙：若 _gather_inputs 用共享引用（inputs[dep]=dstep.get("outputs")）
    # → 此处 s1.outputs 会被污染 → 断言必红
    assert s1["outputs"]["summary"] == "s1 done", s1["outputs"]
    assert "MUTATED_ART" not in s1["outputs"].get("artifacts", []), s1["outputs"]


def test_backward_compat_no_inputs_field():
    """T4 向后兼容：旧 plan 的 step 无 inputs 字段 → 被安全补全为空 dict，不报错、不崩。"""
    plan = {
        "steps": [
            {"id": "s1", "title": "a", "description": "a", "dependencies": [], "status": "pending"},
        ]
    }
    todo = {"s1": "pending"}
    captured = {}
    backend = _MemBackend(plan, todo)
    scheduler = WorkflowScheduler(backend=backend, step_executor=_make_executor(captured))
    result = asyncio.run(scheduler.execute("sid", concurrent=False))

    assert result.exec_order == ["s1"], result.exec_order
    # 旧 plan 无 inputs 字段 → 被安全创建为空 dict，不抛异常（反向承重墙：若聚合写坏字段必红）
    assert captured["s1"] == {}, captured["s1"]


def test_prompt_renders_inputs():
    """T2 prompt 含 inputs：_build_workflow_step_prompt 渲染「已知前置产物」。"""
    from vermes_cli.blueprints.chat import _build_workflow_step_prompt

    step = {
        "id": "s2",
        "title": "b",
        "description": "b",
        "inputs": {
            "s1": {"summary": "s1 result", "artifacts": ["a1", "a2", "a3", "a4", "a5"]}
        },
    }
    prompt = _build_workflow_step_prompt(step)
    # 反向承重墙：若 prompt 构造忘渲染 inputs → 这两行必红
    assert "已知前置产物" in prompt, prompt
    assert "s1 result" in prompt, prompt
    # artifacts 截断到 3 条（§0.12.3 防 context 爆量）
    assert prompt.count("产物：") == 3, prompt.count("产物：")

    # 无 inputs 时不渲染该段（向后兼容）
    step2 = {"id": "s3", "title": "c", "description": "c"}
    prompt2 = _build_workflow_step_prompt(step2)
    assert "已知前置产物" not in prompt2, prompt2
