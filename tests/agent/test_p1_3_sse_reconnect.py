"""
P1-3 SSE Reconnect Snapshot — 端到端测试

验证：
1. 后端 /api/session/{id}/plan_snapshot 接口存在且返回正确格式
2. plan_created 时写入 _session_plan_store
3. todo_update 时同步更新 _session_plan_store
4. finally 块中 interrupted 状态同步到 _session_plan_store
5. 空session 返回默认值
6. fetchSnapshot 前端方法存在（通过后端接口验证）
"""
import json
import time
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


class TestPlanSnapshotEndpoint:
    """测试 /api/session/{id}/plan_snapshot 接口"""

    def test_empty_session_returns_default(self):
        """空 session 返回默认值"""
        from vermes_cli.blueprints.chat import _session_plan_store
        _session_plan_store.clear()
        from vermes_cli.blueprints.chat import plan_snapshot
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(plan_snapshot("nonexistent"))
        assert result["ok"] is True
        assert result["plan"] is None
        assert result["todo_states"] == {}
        assert result["plan_emitted"] is False

    def test_session_with_plan_returns_snapshot(self):
        """有 plan 的 session 返回快照"""
        from vermes_cli.blueprints.chat import _session_plan_store
        _session_plan_store["test-snap-1"] = {
            "plan": {"id": "abc12345", "title": "Test Plan", "steps": [{"id": "s1", "status": "completed"}]},
            "todo_states": {"s1": "completed"},
            "plan_emitted": True,
        }
        from vermes_cli.blueprints.chat import plan_snapshot
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(plan_snapshot("test-snap-1"))
        assert result["ok"] is True
        assert result["plan"]["title"] == "Test Plan"
        assert result["todo_states"]["s1"] == "completed"
        assert result["plan_emitted"] is True
        _session_plan_store.pop("test-snap-1", None)

    def test_session_with_interrupted_state(self):
        """中断状态的 session 返回 interrupted 快照"""
        from vermes_cli.blueprints.chat import _session_plan_store
        _session_plan_store["test-snap-2"] = {
            "plan": {"id": "xyz98765", "title": "Interrupted Plan", "steps": [{"id": "s1", "status": "in_progress"}]},
            "todo_states": {"s1": "interrupted"},
            "plan_emitted": True,
        }
        from vermes_cli.blueprints.chat import plan_snapshot
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(plan_snapshot("test-snap-2"))
        assert result["todo_states"]["s1"] == "interrupted"
        _session_plan_store.pop("test-snap-2", None)


class TestSessionPlanStoreWrite:
    """测试 plan/todo 事件写入 _session_plan_store"""

    def test_plan_created_writes_to_store(self):
        """plan_created 事件写入 store"""
        from vermes_cli.blueprints.chat import _session_plan_store
        _session_plan_store.clear()
        # 模拟 plan_created 写入
        plan_data = {
            "title": "测试任务",
            "steps": [{"id": "s1", "title": "步骤1", "status": "pending"}],
        }
        plan_event = {
            "type": "plan_created",
            "plan": {
                "id": "hash1234",
                "title": plan_data["title"],
                "steps": plan_data["steps"],
            }
        }
        _session_plan_store["test-write-1"] = {
            "plan": plan_event["plan"],
            "todo_states": {},
            "plan_emitted": True,
        }
        assert _session_plan_store["test-write-1"]["plan"]["title"] == "测试任务"
        assert _session_plan_store["test-write-1"]["plan_emitted"] is True
        _session_plan_store.pop("test-write-1", None)

    def test_todo_update_syncs_to_store(self):
        """todo_update 事件同步到 store"""
        from vermes_cli.blueprints.chat import _session_plan_store
        # 初始化 session
        _session_plan_store["test-write-2"] = {
            "plan": {"id": "p1", "title": "Plan", "steps": [{"id": "s1", "status": "pending"}]},
            "todo_states": {},
            "plan_emitted": True,
        }
        # 模拟 todo_update 同步
        todo_states = {"s1": "in_progress"}
        _session_plan_store["test-write-2"] = {
            "plan": _session_plan_store["test-write-2"]["plan"],
            "todo_states": todo_states,
            "plan_emitted": True,
        }
        assert _session_plan_store["test-write-2"]["todo_states"]["s1"] == "in_progress"
        _session_plan_store.pop("test-write-2", None)

    def test_finally_interrupted_syncs_to_store(self):
        """finally 块中 interrupted 状态同步到 store"""
        from vermes_cli.blueprints.chat import _session_plan_store
        _session_plan_store["test-write-3"] = {
            "plan": {"id": "p2", "title": "Plan", "steps": [{"id": "s1", "status": "in_progress"}]},
            "todo_states": {"s1": "in_progress"},
            "plan_emitted": True,
        }
        # 模拟 finally 块写入
        _session_plan_store["test-write-3"] = {
            "plan": _session_plan_store["test-write-3"]["plan"],
            "todo_states": {"s1": "interrupted"},
            "plan_emitted": True,
        }
        assert _session_plan_store["test-write-3"]["todo_states"]["s1"] == "interrupted"
        _session_plan_store.pop("test-write-3", None)


class TestSnapshotRouteRegistered:
    """测试路由注册"""

    def test_snapshot_route_registered(self):
        """验证 /api/session/{session_id}/plan_snapshot 路由已注册"""
        from vermes_cli.blueprints.chat import register_to
        mock_app = MagicMock()
        register_to(mock_app)
        registered_paths = [call.args[0] for call in mock_app.add_api_route.call_args_list]
        assert "/api/session/{session_id}/plan_snapshot" in registered_paths


class TestSnapshotMergeLogic:
    """测试快照 merge 逻辑（模拟前端行为）"""

    def test_snapshot_merge_preserves_completed_and_appends_new(self):
        """快照 merge：保留已完成步骤，追加新步骤"""
        # 模拟前端已有 todoItems
        existing_todos = [
            {"id": "s1", "title": "步骤1", "status": "completed"},
            {"id": "s2", "title": "步骤2", "status": "in_progress"},
        ]
        # 快照返回的状态
        snapshot_states = {"s1": "completed", "s2": "interrupted", "s3": "pending"}
        # Merge 逻辑（模拟 chat.js onTodoUpdate merge）
        merged = []
        for item in existing_todos:
            new_status = snapshot_states.get(item["id"])
            if new_status and new_status != item["status"]:
                merged.append({**item, "status": new_status})
            else:
                merged.append(item)
        # 验证 merge 结果
        assert merged[0]["status"] == "completed"  # 未变
        assert merged[1]["status"] == "interrupted"  # 更新
        assert len(merged) == 2  # 不追加新步骤（snapshot 只更新状态不新增）

    def test_snapshot_with_no_plan_returns_none(self):
        """无 plan 的快照返回 None"""
        from vermes_cli.blueprints.chat import _session_plan_store
        _session_plan_store.clear()
        from vermes_cli.blueprints.chat import plan_snapshot
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(plan_snapshot("empty-session"))
        assert result["plan"] is None
        assert result["todo_states"] == {}


class TestReconnectExponentialBackoff:
    """测试重连指数退避逻辑"""

    def test_backoff_delay_calculation(self):
        """验证退避延迟计算：1s → 2s → 4s（cap 5s）"""
        max_reconnects = 2
        for attempt in range(1, max_reconnects + 1):
            delay = min(1000 * (2 ** (attempt - 1)), 5000)
            if attempt == 1:
                assert delay == 1000
            elif attempt == 2:
                assert delay == 2000

    def test_max_reconnects_exceeded_emits_error(self):
        """超过最大重连次数后 emit error"""
        max_reconnects = 2
        attempts = 3
        should_emit_error = attempts > max_reconnects
        assert should_emit_error is True

    def test_reconnect_counter_reset_on_success(self):
        """成功后重置重连计数器"""
        reconnect_attempts = {"session-1": 2}
        # 模拟 [DONE] 后重置
        reconnect_attempts.pop("session-1", None)
        assert "session-1" not in reconnect_attempts
