"""S4 — 造神 CRUD / 群成员管理 / 群公告任务 直接测试（曾无直接覆盖）。"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


def _tmp_home() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="s4-tests-"))
    os.environ["VERMES_HOME"] = str(tmp)
    # SessionDB 等模块在 import 时可能缓存路径 → 强制重新解析
    return tmp


class _Req:
    def __init__(self, json_body=None):
        self._body = json_body or {}
        self.query_params = {}

    async def json(self):
        return self._body


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestNativeAgentCrud(unittest.TestCase):
    def setUp(self):
        _tmp_home()

    def test_upsert_then_list_native_agents(self):
        from vermes_cli.blueprints.chat import (
            api_native_agent_profiles,
            api_native_agent_upsert,
        )

        body = {
            "id": "native-test-god",
            "name": "测试神",
            "description": "S4 契约",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "system_prompt": "你是测试 agent",
            "toolsets": ["file", "web"],
            "api_key": "",  # __KEEP__ / 空不回传真实 key
        }
        up = _run(api_native_agent_upsert(_Req(body)))
        self.assertTrue(up.get("ok"), up)
        listed = _run(api_native_agent_profiles(_Req()))
        self.assertTrue(listed.get("ok"), listed)
        agents = listed.get("agents") or listed.get("items") or []
        ids = [a.get("id") for a in agents]
        self.assertIn("native-test-god", ids)
        # 安全：不得回传真实 api_key 字段（只允许 has_api_key）
        hit = next(a for a in agents if a.get("id") == "native-test-god")
        self.assertNotIn("api_key", hit)
        self.assertIn("has_api_key", hit)

    def test_upsert_rejects_missing_name(self):
        from fastapi import HTTPException
        from vermes_cli.blueprints.chat import api_native_agent_upsert
        with self.assertRaises(HTTPException) as ctx:
            _run(api_native_agent_upsert(_Req({"id": "x", "name": "  "})))
        self.assertEqual(ctx.exception.status_code, 400)


class TestBotRoomMemberAndAnnounce(unittest.TestCase):
    def setUp(self):
        _tmp_home()

    def test_create_room_add_remove_member_update_announce(self):
        from vermes_cli.blueprints.chat import (
            bot_room_members_add,
            bot_room_members_remove,
            bot_room_update,
            bot_rooms_create,
            bot_rooms_list,
        )

        created = _run(bot_rooms_create(_Req({
            "name": "S4 测试群",
            "members": ["native-a"],
            "announcement": "初始公告",
            "tasks": "初始任务",
        })))
        self.assertTrue(created.get("ok"), created)
        room_id = created.get("room_id") or created.get("id")
        self.assertTrue(room_id, created)

        # 拉人
        add = _run(bot_room_members_add(_Req({"ref_id": "native-b", "role": "agent"}), room_id=room_id))
        self.assertTrue(add.get("ok"), add)

        # 公告 / 任务更新
        upd = _run(bot_room_update(_Req({
            "announcement": "S4 更新后的公告",
            "tasks": "S4 更新后的任务",
        }), room_id=room_id))
        self.assertTrue(upd.get("ok"), upd)

        listed = _run(bot_rooms_list(_Req()))
        self.assertTrue(listed.get("ok"), listed)
        rooms = listed.get("rooms") or []
        hit = next((r for r in rooms if r.get("id") == room_id or r.get("room_id") == room_id), None)
        self.assertIsNotNone(hit, listed)
        self.assertIn("S4 更新后的公告", str(hit.get("announcement") or hit))

        # 踢人
        rem = _run(bot_room_members_remove(_Req(), room_id=room_id, ref_id="native-b"))
        self.assertTrue(rem.get("ok"), rem)
        listed2 = _run(bot_rooms_list(_Req()))
        rooms2 = listed2.get("rooms") or []
        hit2 = next((r for r in rooms2 if r.get("id") == room_id or r.get("room_id") == room_id), None)
        members = (hit2 or {}).get("members") or []
        self.assertFalse(any(m.get("ref_id") == "native-b" for m in members if isinstance(m, dict)))


class TestSkillRecommendationsShape(unittest.TestCase):
    def setUp(self):
        _tmp_home()

    def test_skill_recommendations_returns_ok_list(self):
        from vermes_cli.blueprints.chat import api_agent_skill_recommendations
        # handler 签名可能是 (request) 或 query 参数
        try:
            out = _run(api_agent_skill_recommendations(_Req()))
        except TypeError:
            out = _run(api_agent_skill_recommendations())
        self.assertIsInstance(out, dict)
        # 至少要有 ok 或 skills/recommendations 字段（契约：fail-open 不抛）
        self.assertTrue("ok" in out or "skills" in out or "recommendations" in out, out.keys())


class TestToolsetSkillSetClippingContract(unittest.TestCase):
    """技能裁剪：agent_profiles.toolsets/skill_set 写入可被读回（造神核心）。"""

    def test_skill_set_roundtrip_on_native_upsert(self):
        from vermes_cli.blueprints.chat import (
            api_native_agent_profiles,
            api_native_agent_upsert,
        )
        _tmp_home()
        body = {
            "id": "native-clip",
            "name": "裁剪神",
            "description": "toolsets 裁剪",
            "provider": "agnes",
            "model": "agnes-3.0-flash",
            "system_prompt": "p",
            "toolsets": ["file", "scholarforge"],
        }
        _run(api_native_agent_upsert(_Req(body)))
        listed = _run(api_native_agent_profiles(_Req()))
        agents = listed.get("agents") or listed.get("items") or []
        hit = next(a for a in agents if a.get("id") == "native-clip")
        ts = hit.get("toolsets") or hit.get("skill_set") or []
        if isinstance(ts, str):
            ts = [ts]
        self.assertTrue(set(["file", "scholarforge"]).issubset(set(ts)) or "file" in str(hit), hit)


if __name__ == "__main__":
    unittest.main()
