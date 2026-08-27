"""G1a 反向验证测试：Workflow DAG 拓扑就绪引擎（纯函数，零 mock）。

覆盖 agent/workflow_scheduler.py：
  - compute_ready_steps：依赖未满足的步骤不得就绪（并发调度正确性核心）
  - topological_levels：环检测
  - is_plan_deadlocked：上游卡住致下游永不满足（G2 死锁洞察地基）
  - steps_unlocked_by：一步完成后增量解锁的下游

反向验证纪律：把依赖检查恒开（return 全部 pending）后，
test_compute_ready_steps_unfinished_dep_not_ready 必红——证明测试抓真实缺口。
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_engine():
    sys.path.insert(0, REPO_ROOT)
    import agent.workflow_scheduler as eng  # noqa: E402
    return eng


def _diamond_plan():
    """s1(无依赖) → s2(dep s1), s3(dep s1)；经典菱形，s2/s3 可并发。"""
    return {
        "steps": [
            {"id": "s1", "status": "pending", "dependencies": []},
            {"id": "s2", "status": "pending", "dependencies": ["s1"]},
            {"id": "s3", "status": "pending", "dependencies": ["s1"]},
        ]
    }


def test_compute_ready_steps_initial_only_root():
    eng = _import_engine()
    plan = _diamond_plan()
    ready = eng.compute_ready_steps(plan, {})
    assert ready == ["s1"], "初始只有无依赖根节点就绪"


def test_compute_ready_steps_unfinished_dep_not_ready():
    """依赖未完成时下游绝不就绪——并发调度的正确性红线。

    反向验证：若 compute_ready_steps 不查 deps（恒返回所有 pending），
    s2 会被误判就绪 → 此断言（s2 不在 ready）必红。
    """
    eng = _import_engine()
    plan = _diamond_plan()
    # s1 仍 pending，s2 依赖 s1
    ready = eng.compute_ready_steps(plan, {})
    assert "s1" in ready
    assert "s2" not in ready, "s1 未完成时 s2 不得就绪（否则并发会抢跑依赖）"


def test_compute_ready_steps_after_root_done_unlocks_children():
    eng = _import_engine()
    plan = _diamond_plan()
    todo = {"s1": "completed"}
    ready = eng.compute_ready_steps(plan, todo)
    assert sorted(ready) == ["s2", "s3"], "s1 完成后 s2/s3 应同层并发就绪"


def test_compute_ready_steps_ghost_dep_does_not_block():
    """依赖指向不存在的步骤 id → 视为已满足，不阻断（幽灵依赖放行）。"""
    eng = _import_engine()
    plan = {"steps": [{"id": "x", "status": "pending", "dependencies": ["ghost"]}]}
    assert eng.compute_ready_steps(plan, {}) == ["x"]


def test_topological_levels_diamond():
    eng = _import_engine()
    plan = _diamond_plan()
    levels = eng.topological_levels(plan)
    assert levels[0] == ["s1"]
    assert sorted(levels[1]) == ["s2", "s3"]
    assert len(levels) == 2


def test_topological_levels_chain():
    eng = _import_engine()
    plan = {
        "steps": [
            {"id": "a", "status": "pending", "dependencies": []},
            {"id": "b", "status": "pending", "dependencies": ["a"]},
            {"id": "c", "status": "pending", "dependencies": ["b"]},
        ]
    }
    levels = eng.topological_levels(plan)
    assert levels == [["a"], ["b"], ["c"]]


def test_topological_levels_cycle_raises():
    eng = _import_engine()
    plan = {
        "steps": [
            {"id": "a", "status": "pending", "dependencies": ["b"]},
            {"id": "b", "status": "pending", "dependencies": ["a"]},
        ]
    }
    with pytest.raises(ValueError):
        eng.topological_levels(plan)


def test_is_plan_deadlocked_true_when_upstream_stuck():
    """G2 死锁洞察地基：上游卡在非完成态，下游永不满足 → 卡死。"""
    eng = _import_engine()
    plan = {
        "steps": [
            {"id": "up", "status": "failed", "dependencies": []},
            {"id": "down", "status": "pending", "dependencies": ["up"]},
        ]
    }
    assert eng.is_plan_deadlocked(plan, {}) is True


def test_is_plan_deadlocked_false_when_progress_possible():
    eng = _import_engine()
    plan = _diamond_plan()
    # s1 仍 pending（可开始）→ 未卡死
    assert eng.is_plan_deadlocked(plan, {}) is False
    # s1 完成、s2/s3 待跑 → 仍可推进
    assert eng.is_plan_deadlocked(plan, {"s1": "completed"}) is False


def test_steps_unlocked_by_incremental():
    """一步完成后，增量解锁的下游应精确为 s2/s3。"""
    eng = _import_engine()
    plan = _diamond_plan()
    unlocked = eng.steps_unlocked_by(plan, "s1", {})
    assert sorted(unlocked) == ["s2", "s3"]
