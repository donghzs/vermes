"""
审计改进点 #1 / #2 单元测试 — chat blueprint 的 plan store helper。

#2: _update_session_plan 部分字段更新 + 持久化（去重 DRY，替换 3 处重复模式）
#1: clean_session_plan_state 清理内存 dict + SQLite（关闭会话泄漏）

隔离方式：monkeypatch agent.session_plan_store._DB_PATH 到 tmp，并清空模块级
_session_plan_store；不污染真实 DB，也不影响其他用例。
"""
import pytest


@pytest.fixture
def plan_env(tmp_path, monkeypatch):
    import agent.session_plan_store as sps
    import hermes_cli.blueprints.chat as chat

    db = tmp_path / "session_plans.db"
    monkeypatch.setattr(sps, "_DB_PATH", db)
    saved = dict(chat._session_plan_store)
    chat._session_plan_store.clear()
    yield chat
    chat._session_plan_store.clear()
    chat._session_plan_store.update(saved)


def test_update_session_plan_creates_and_persists(plan_env):
    plan_env._update_session_plan(
        "s1", plan={"title": "T"}, todo_states={"a": "pending"}, plan_emitted=True
    )
    # 内存态
    assert plan_env._session_plan_store["s1"]["plan"] == {"title": "T"}
    assert plan_env._session_plan_store["s1"]["plan_emitted"] is True
    # SQLite 往返（真实 session_plan_store 模块）
    import agent.session_plan_store as sps

    loaded = sps.load_plan_state("s1")
    assert loaded == {
        "plan": {"title": "T"},
        "todo_states": {"a": "pending"},
        "plan_emitted": True,
    }


def test_update_session_plan_partial_preserves_plan(plan_env):
    plan_env._update_session_plan("s1", plan={"title": "orig"}, todo_states={}, plan_emitted=False)
    # 仅更新 todo_states / plan_emitted，plan 必须保持
    plan_env._update_session_plan("s1", todo_states={"a": "completed"}, plan_emitted=True)
    assert plan_env._session_plan_store["s1"]["plan"] == {"title": "orig"}
    assert plan_env._session_plan_store["s1"]["todo_states"] == {"a": "completed"}
    assert plan_env._session_plan_store["s1"]["plan_emitted"] is True


def test_clean_session_plan_state_clears_mem_and_db(plan_env):
    plan_env._update_session_plan("s1", plan={"title": "T"}, todo_states={}, plan_emitted=True)
    import agent.session_plan_store as sps

    assert sps.load_plan_state("s1") is not None  # SQLite 已落库
    plan_env.clean_session_plan_state("s1")
    assert "s1" not in plan_env._session_plan_store  # 内存已清
    assert sps.load_plan_state("s1") is None  # SQLite 已删


def test_clean_session_plan_state_idempotent_on_missing(plan_env):
    # 不存在的 session 不应抛异常（fail-open）
    plan_env.clean_session_plan_state("ghost")
    assert "ghost" not in plan_env._session_plan_store
