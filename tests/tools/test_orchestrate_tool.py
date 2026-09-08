"""神魔堂调度工具（tools/orchestrate_tool.py）测试。

覆盖：
- 工具自注册 + schema 合法 + is_async
- 单聊主 agent 默认 toolset（Vermes-cli）含 shenmotang
- status / review 缺参 / delegate 缺 task 的兜底
- 隔离库秘书分身 is_avatar 种子生效
- 桥接函数（找秘书 / 建秘书群）幂等
"""
import json

import pytest

from tools import orchestrate_tool  # noqa: F401  # 触发注册
from tools.registry import registry


def test_shenmotang_registered():
    entry = registry.get_entry("shenmotang")
    assert entry is not None
    assert entry.toolset == "shenmotang"
    assert entry.is_async is True
    assert entry.schema["name"] == "shenmotang"


def test_shenmotang_in_vermes_cli_toolset():
    from model_tools import get_tool_definitions
    defs = get_tool_definitions(enabled_toolsets=["Vermes-cli"], quiet_mode=True)
    names = {t["function"]["name"] for t in defs}
    assert "shenmotang" in names


def test_resolve_secretary_is_avatar(monkeypatch, tmp_path):
    """隔离库：秘书分身 is_avatar=1，能被 _resolve_secretary 找到。"""
    monkeypatch.setenv("VERMES_HOME", str(tmp_path))
    from vermes_state import SessionDB
    db = SessionDB()
    db.seed_default_profiles()
    sec = db.get_agent_profile("secretary")
    assert sec is not None
    assert sec.get("is_avatar") == 1
    # _resolve_secretary 返回秘书
    found = orchestrate_tool._resolve_secretary(db)
    assert found is not None
    assert found["id"] == "secretary"
    db.close()


def test_get_or_create_secretary_room_idempotent(monkeypatch, tmp_path):
    """秘书群房间幂等：重复调用返回同一 room_id 且秘书在群。"""
    monkeypatch.setenv("VERMES_HOME", str(tmp_path))
    from vermes_state import SessionDB
    db = SessionDB()
    db.seed_default_profiles()
    r1 = orchestrate_tool._get_or_create_secretary_room(db, "调研")
    r2 = orchestrate_tool._get_or_create_secretary_room(db, "调研")
    assert r1 == r2 == "secretary-room"
    members = {m["ref_id"] for m in db.list_bot_room_members(r1) if m["member_type"] == "agent"}
    assert "secretary" in members
    db.close()


def test_review_requires_task_id():
    import asyncio
    r = asyncio.run(
        registry.dispatch("shenmotang", {"action": "review"})
        if False else
        orchestrate_tool.shenmotang_tool(action="review")
    )
    d = json.loads(r)
    assert d["ok"] is False
    assert "task_id" in d.get("error", "")


def test_delegate_requires_task():
    import asyncio
    r = asyncio.run(orchestrate_tool.shenmotang_tool(action="delegate", task=""))
    d = json.loads(r)
    assert d["ok"] is False
    assert "task" in d.get("error", "")


def test_truncate():
    assert orchestrate_tool._truncate("abc") == "abc"
    assert orchestrate_tool._truncate("x" * 3000).endswith("…(截断)")
