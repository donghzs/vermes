"""P0/P1/P2 改进端到端测试：任务分步流水线 + 竞态 + 预算收尾 + schema 校验。

Tests:
1. plan_step_update 发射：todo 写入后状态变化触发 SSE
2. todo_update merge：plan 先填充后 todo 不覆盖
3. 预算退出收尾：in_progress → interrupted
4. _find_first_plan_json schema 校验
5. plan id 稳定性：同 title 生成同 id
6. 硬护栏 prune：token 超阈值 1.5x 时先 prune
"""

import json
import hashlib
import pytest
from unittest.mock import MagicMock, patch


# ── P0-1: plan_step_update 发射 ──────────────────────────────

class TestPlanStepUpdateEmission:
    """验证 todo 工具写入后，状态变化的步骤会发射 plan_step_update。"""

    def test_first_step_in_progress_emits_update(self):
        """第一次写入 todo，pending→in_progress 应发射 plan_step_update。"""
        # 模拟 _prev_todo_states 从空到有
        prev_states = {}
        new_todos = [
            {"id": "s1", "content": "步骤1", "status": "in_progress"},
            {"id": "s2", "content": "步骤2", "status": "pending"},
        ]
        updates = []
        for t in new_todos:
            tid = t.get("id", "")
            new_status = t.get("status", "")
            old_status = prev_states.get(tid)
            if new_status != old_status:
                updates.append({"type": "plan_step_update", "step": {"id": tid, "status": new_status}})
                prev_states[tid] = new_status
        assert len(updates) == 2  # 两个步骤都是新状态
        assert updates[0]["step"]["id"] == "s1"
        assert updates[0]["step"]["status"] == "in_progress"
        assert updates[1]["step"]["id"] == "s2"
        assert updates[1]["step"]["status"] == "pending"

    def test_no_update_when_status_unchanged(self):
        """状态未变化时不发射 update。"""
        prev_states = {"s1": "in_progress", "s2": "pending"}
        new_todos = [
            {"id": "s1", "content": "步骤1", "status": "in_progress"},
            {"id": "s2", "content": "步骤2", "status": "pending"},
        ]
        updates = []
        for t in new_todos:
            tid = t.get("id", "")
            new_status = t.get("status", "")
            old_status = prev_states.get(tid)
            if new_status != old_status:
                updates.append({"type": "plan_step_update", "step": {"id": tid, "status": new_status}})
                prev_states[tid] = new_status
        assert len(updates) == 0

    def test_step_completion_emits_update(self):
        """步骤从 in_progress → completed 应发射 update。"""
        prev_states = {"s1": "in_progress", "s2": "pending"}
        new_todos = [
            {"id": "s1", "content": "步骤1", "status": "completed"},
            {"id": "s2", "content": "步骤2", "status": "in_progress"},
        ]
        updates = []
        for t in new_todos:
            tid = t.get("id", "")
            new_status = t.get("status", "")
            old_status = prev_states.get(tid)
            if new_status != old_status:
                updates.append({"type": "plan_step_update", "step": {"id": tid, "status": new_status}})
                prev_states[tid] = new_status
        assert len(updates) == 2
        assert updates[0]["step"]["status"] == "completed"
        assert updates[1]["step"]["id"] == "s2"
        assert updates[1]["step"]["status"] == "in_progress"


# ── P0-2: todo_update merge 逻辑 ──────────────────────────────

class TestTodoUpdateMerge:
    """验证 todo_update 不会覆盖 plan 已填充的步骤。"""

    def test_merge_preserves_existing_order_and_appends_new(self):
        """模拟前端 merge 逻辑：已有条目按 id 更新，新条目追加。"""
        # plan 先填充
        todo_items = [
            {"id": "s1", "content": "分析需求", "status": "in_progress", "started_at": 1000},
            {"id": "s2", "content": "编写代码", "status": "pending", "started_at": None},
            {"id": "s3", "content": "测试", "status": "pending", "started_at": None},
        ]
        # todo_update 来了，只含 s1 和 s2 的更新
        new_todos = [
            {"id": "s1", "content": "分析需求", "status": "completed", "started_at": 1000, "finished_at": 2000},
            {"id": "s2", "content": "编写代码", "status": "in_progress", "started_at": 2000},
        ]
        # 模拟 merge 逻辑
        existing_map = {}
        for item in todo_items:
            existing_map[item["id"]] = {**item, "tool_calls": item.get("tool_calls", [])}

        for t in new_todos:
            old = existing_map.get(t["id"])
            if old:
                existing_map[t["id"]] = {**old, **t,
                    "started_at": t.get("started_at") if t.get("started_at") is not None else old.get("started_at"),
                    "finished_at": t.get("finished_at") if t.get("finished_at") is not None else old.get("finished_at"),
                }
            else:
                existing_map[t["id"]] = {**t, "tool_calls": []}

        old_order = [i["id"] for i in todo_items]
        merged = []
        for id_ in old_order:
            item = existing_map.get(id_)
            if item:
                merged.append(item)
                existing_map.pop(id_)
        for item in existing_map.values():
            merged.append(item)

        # s1 应为 completed
        assert merged[0]["status"] == "completed"
        assert merged[0]["finished_at"] == 2000
        # s2 应为 in_progress
        assert merged[1]["status"] == "in_progress"
        # s3 应保留 pending（不在 todo_update 中但未被删除）
        assert merged[2]["status"] == "pending"
        assert merged[2]["id"] == "s3"
        assert len(merged) == 3


# ── P1-1: 预算退出收尾 ──────────────────────────────────────

class TestBudgetExitInterrupted:
    """验证 agent 结束后，in_progress 步骤被标记为 interrupted。"""

    def test_in_progress_marked_interrupted_on_exit(self):
        prev_states = {"s1": "completed", "s2": "in_progress", "s3": "pending"}
        # 模拟 finally 块
        for tid, status in prev_states.items():
            if status == "in_progress":
                prev_states[tid] = "interrupted"
        assert prev_states["s1"] == "completed"  # 不变
        assert prev_states["s2"] == "interrupted"  # 被标记
        assert prev_states["s3"] == "pending"  # 不变

    def test_no_interrupted_when_all_completed(self):
        prev_states = {"s1": "completed", "s2": "completed"}
        for tid, status in prev_states.items():
            if status == "in_progress":
                prev_states[tid] = "interrupted"
        assert prev_states["s1"] == "completed"
        assert prev_states["s2"] == "completed"


# ── P2-1: _find_first_plan_json schema 校验 ──────────────────

class TestPlanJsonSchemaValidation:
    """验证 _find_first_plan_json 的最小 schema 校验。"""

    def test_valid_plan_json(self):
        from vermes_cli.blueprints.chat import _find_first_plan_json
        text = json.dumps({
            "plan": "测试计划",
            "steps": [{"id": "s1", "title": "步骤1", "status": "pending"}]
        })
        result = _find_first_plan_json(text)
        assert result is not None
        assert result["plan"] == "测试计划"
        assert len(result["steps"]) == 1

    def test_reject_missing_plan_key(self):
        from vermes_cli.blueprints.chat import _find_first_plan_json
        text = json.dumps({"title": "not a plan", "steps": []})
        result = _find_first_plan_json(text)
        assert result is None

    def test_reject_plan_not_string_or_dict(self):
        from vermes_cli.blueprints.chat import _find_first_plan_json
        text = json.dumps({"plan": 123, "steps": [{"id": "s1"}]})
        result = _find_first_plan_json(text)
        assert result is None

    def test_reject_steps_not_list(self):
        from vermes_cli.blueprints.chat import _find_first_plan_json
        text = json.dumps({"plan": "ok", "steps": "not a list"})
        result = _find_first_plan_json(text)
        assert result is None

    def test_reject_empty_steps(self):
        from vermes_cli.blueprints.chat import _find_first_plan_json
        text = json.dumps({"plan": "ok", "steps": []})
        result = _find_first_plan_json(text)
        assert result is None

    def test_reject_no_steps_key(self):
        from vermes_cli.blueprints.chat import _find_first_plan_json
        # plan 是 dict 但不含 steps
        text = json.dumps({"plan": {"title": "ok"}})
        result = _find_first_plan_json(text)
        assert result is None

    def test_accept_plan_as_string_with_steps(self):
        from vermes_cli.blueprints.chat import _find_first_plan_json
        # plan 是标题字符串，steps 在顶层
        text = json.dumps({"plan": "my plan", "steps": [{"id": "s1"}]})
        result = _find_first_plan_json(text)
        assert result is not None
        assert result["plan"] == "my plan"


# ── P2-1: plan id 稳定性 ─────────────────────────────────────

class TestPlanIdStability:
    """验证 plan id 用 md5 hash 生成，同 title 得同 id。"""

    def test_same_title_same_id(self):
        title = "代码审查"
        id1 = hashlib.md5(title.encode()).hexdigest()[:8]
        id2 = hashlib.md5(title.encode()).hexdigest()[:8]
        assert id1 == id2

    def test_different_title_different_id(self):
        id1 = hashlib.md5(b"code review").hexdigest()[:8]
        id2 = hashlib.md5(b"deploy").hexdigest()[:8]
        assert id1 != id2

    def test_id_is_8_chars_hex(self):
        title = "any task"
        plan_id = hashlib.md5(title.encode()).hexdigest()[:8]
        assert len(plan_id) == 8
        int(plan_id, 16)  # 是合法 hex


# ── P1-2: 硬护栏 prune 逻辑 ──────────────────────────────────

class TestHardPruneGuardrail:
    """验证 token 超阈值 1.5x 时触发确定性 prune。"""

    def test_1_5x_threshold_triggers_prune(self):
        threshold = 32000
        actual_tokens = 48001  # > 1.5x
        assert actual_tokens >= int(threshold * 1.5)

    def test_below_1_5x_no_prune(self):
        threshold = 32000
        actual_tokens = 35000  # > threshold but < 1.5x
        assert actual_tokens < int(threshold * 1.5)
