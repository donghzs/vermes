"""
#4 边界修复测试 — _session_plan_store SQLite 持久化（跨重启恢复）

验证：
1. save → load 往返一致（plan / todo_states / plan_emitted）
2. 缺失 session 返回 None
3. 同 session 覆盖写（upsert）
4. delete 清除
5. plan=None 也能持久化
6. 跨重启模拟：清空内存 store 后，plan_snapshot 端点从 SQLite 恢复
"""
import asyncio
import pytest


@pytest.fixture
def plan_db(tmp_path, monkeypatch):
    import agent.session_plan_store as sps

    db = tmp_path / "session_plans.db"
    monkeypatch.setattr(sps, "_DB_PATH", db)
    yield sps


def test_save_load_roundtrip(plan_db):
    plan = {"title": "T", "steps": [{"id": "a", "title": "s1"}]}
    plan_db.save_plan_state("s1", plan, {"a": "completed"}, True)
    loaded = plan_db.load_plan_state("s1")
    assert loaded == {"plan": plan, "todo_states": {"a": "completed"}, "plan_emitted": True}


def test_load_missing_returns_none(plan_db):
    assert plan_db.load_plan_state("nope") is None


def test_save_overwrites(plan_db):
    plan_db.save_plan_state("s1", {"v": 1}, {}, False)
    plan_db.save_plan_state("s1", {"v": 2}, {"x": "in_progress"}, True)
    loaded = plan_db.load_plan_state("s1")
    assert loaded["plan"] == {"v": 2}
    assert loaded["todo_states"] == {"x": "in_progress"}
    assert loaded["plan_emitted"] is True


def test_delete(plan_db):
    plan_db.save_plan_state("s1", {"v": 1}, {}, True)
    plan_db.delete_plan_state("s1")
    assert plan_db.load_plan_state("s1") is None


def test_json_none_plan(plan_db):
    plan_db.save_plan_state("s1", None, {"a": "pending"}, False)
    loaded = plan_db.load_plan_state("s1")
    assert loaded["plan"] is None
    assert loaded["todo_states"] == {"a": "pending"}


def test_restore_after_restart_simulation(plan_db):
    """模拟跨重启：内存 store 清空后，plan_snapshot 端点从 SQLite 恢复。"""
    plan_db.save_plan_state("sess-x", {"title": "Plan X"}, {"a": "completed", "b": "in_progress"}, True)

    from hermes_cli.blueprints.chat import _session_plan_store, plan_snapshot

    _session_plan_store.clear()
    assert "sess-x" not in _session_plan_store
    result = asyncio.run(plan_snapshot("sess-x"))
    assert result["ok"] is True
    assert result["plan"] == {"title": "Plan X"}
    assert result["todo_states"] == {"a": "completed", "b": "in_progress"}
    assert result["plan_emitted"] is True
