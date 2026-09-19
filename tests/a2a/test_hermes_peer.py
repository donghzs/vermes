"""⑦ hermes peer —— bot 间 DM 真行为测试（C3）。

纪律：不读源码做弱断言；用注入 registry + fake chat_runner 走真实协议链路
（注册 → 信封 → dispatch → 结果），并覆盖群聊 @ 触发 peer 回群。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path


def _tmp_home() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="c3-peer-tests-"))
    os.environ["VERMES_HOME"] = str(tmp)
    return tmp


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Req:
    def __init__(self, json_body=None):
        self._body = json_body or {}
        self.query_params = {}

    async def json(self):
        return self._body


def _registry(tmp_path):
    from agent.a2a.registry import AgentRegistry
    from vermes_state import SessionDB
    db = SessionDB(db_path=tmp_path / "peer-a2a.db")
    return AgentRegistry(db=db)


class TestPeerEnvelopeAndDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = _tmp_home()
        self.reg = _registry(self.tmp)
        self.alice = {"id": "alice", "name": "爱丽丝", "provider": "p", "model": "m", "transport": "native"}
        self.bob = {"id": "bob", "name": "鲍勃", "provider": "p", "model": "m", "transport": "native"}

    def test_peer_exchange_real_behavior(self):
        """真行为：注册双方 → 信封 from/to/room → runner 收到 prompt → 结果回传。"""
        from agent.a2a.peer import peer_exchange, ensure_peer_registered

        seen = {}

        def runner(profile, prompt):
            seen["to_id"] = profile.get("id")
            seen["prompt"] = prompt
            return "私聊收到，数据分析如下：完成"

        pr = _run(peer_exchange(
            self.alice, self.bob, "请帮我分析数据",
            room_id="room-1", registry=self.reg, chat_runner=runner,
        ))
        self.assertTrue(pr.ok, pr.error)
        self.assertEqual(pr.result, "私聊收到，数据分析如下：完成")
        self.assertEqual(seen["to_id"], "bob")
        self.assertIn("分析数据", seen["prompt"])
        # 信封协议字段
        env = pr.envelope
        self.assertIsNotNone(env)
        self.assertEqual(env["to_handle"], "vermes://bob")
        self.assertEqual(env["from_handle"], "vermes://alice")
        self.assertEqual(env["room_id"], "room-1")
        self.assertTrue(env["payload"].get("peer") is True)
        self.assertIn("分析数据", env["payload"].get("text", ""))
        # 双方已进 registry 且在线
        self.assertIsNotNone(self.reg.resolve("vermes://alice"))
        self.assertIsNotNone(self.reg.resolve("vermes://bob"))
        self.assertTrue(self.reg.is_alive("bob"))

    def test_peer_rejects_self_target(self):
        from agent.a2a.peer import peer_exchange
        pr = _run(peer_exchange(
            self.alice, dict(self.alice), "自嗨",
            room_id="r", registry=self.reg,
            chat_runner=lambda p, t: "no",
        ))
        self.assertFalse(pr.ok)
        self.assertIn("self-target", pr.error)

    def test_peer_offline_not_silent(self):
        """离线 peer：显式失败，不静默丢消息（① 规格纪律）。"""
        from agent.a2a.peer import peer_exchange, ensure_peer_registered
        ensure_peer_registered(self.bob, self.reg)
        db = self.reg._get_db()
        db._execute_write(lambda c: c.execute(
            "UPDATE a2a_agents SET last_heartbeat = ? WHERE profile_id = ?",
            (0.0, "bob"),
        ))
        pr = _run(peer_exchange(
            self.alice, self.bob, "在吗",
            room_id="r", registry=self.reg,
            chat_runner=lambda p, t: "should-not-run",
        ))
        self.assertFalse(pr.ok)
        self.assertIn("offline", pr.error.lower())
        self.assertEqual(pr.result, "")

    def test_async_chat_runner_supported(self):
        from agent.a2a.peer import peer_exchange

        async def arunner(profile, prompt):
            await asyncio.sleep(0)
            return f"async-ok:{prompt}"

        pr = _run(peer_exchange(
            self.alice, self.bob, "ping",
            room_id="", registry=self.reg, chat_runner=arunner,
        ))
        self.assertTrue(pr.ok, pr.error)
        self.assertTrue(pr.result.startswith("async-ok:"))

    def test_extract_peer_targets_excludes_self(self):
        from agent.a2a.peer import extract_peer_targets
        profiles = [
            {"id": "alice", "name": "爱丽丝"},
            {"id": "bob", "name": "鲍勃"},
        ]
        got = extract_peer_targets("@鲍勃 请看 @爱丽丝 的稿子", profiles, self_id="alice")
        self.assertEqual(got, ["bob"])


class TestBotRoomPeerEndpoint(unittest.TestCase):
    """群聊验收：A @B → peer 结果回群（真行为，fake agent.chat）。"""

    def setUp(self):
        self.tmp = _tmp_home()
        os.environ["VERMES_BOT_MODE"] = "1"
        os.environ["VERMES_ENABLE_BOT_MODE"] = "1"
        # 隔离 agent 缓存 + 强制 bot_mode 开
        import vermes_cli.blueprints.chat as chat_mod
        from vermes_cli.blueprints.agent_cache import _AgentCache
        chat_mod._agent_cache = _AgentCache()
        self._chat_mod = chat_mod
        self._orig_build = chat_mod._bot_build_agent
        self._orig_mode = chat_mod._bot_mode_enabled
        chat_mod._bot_mode_enabled = lambda: True

    def tearDown(self):
        import run_agent
        self._chat_mod._bot_build_agent = self._orig_build
        self._chat_mod._bot_mode_enabled = self._orig_mode
        if hasattr(run_agent, "_orig_AIAgent"):
            run_agent.AIAgent = run_agent._orig_AIAgent

    def _patch_build_agent(self, chat_fn):
        """直接替换 _bot_build_agent：peer/群聊执行面走确定性 fake，不依赖凭证。"""
        chat_mod = self._chat_mod
        calls = []

        class _Fake:
            tools = ["dummy"]
            def __init__(self, session_key):
                self.session_id = session_key
            def chat(self, msg, stream_callback=None):
                calls.append((self.session_id, msg))
                return chat_fn(self.session_id, msg)

        async def _build(session_key, profile):
            return _Fake(session_key)

        chat_mod._bot_build_agent = _build
        return calls

    def test_explicit_peer_endpoint_posts_to_room(self):
        from vermes_cli.blueprints.chat import (
            bot_room_peer_dm,
            bot_rooms_create,
            bot_room_timeline_get,
            api_native_agent_upsert,
        )
        import run_agent
        if not hasattr(run_agent, "_orig_AIAgent"):
            run_agent._orig_AIAgent = run_agent.AIAgent

        for body in (
            {"id": "native-a", "name": "甲A", "provider": "deepseek", "model": "deepseek-chat",
             "system_prompt": "p", "toolsets": []},
            {"id": "native-b", "name": "乙B", "provider": "deepseek", "model": "deepseek-chat",
             "system_prompt": "p", "toolsets": []},
        ):
            out = _run(api_native_agent_upsert(_Req(body)))
            self.assertTrue(out.get("ok", True), out)

        created = _run(bot_rooms_create(_Req({"name": "peer群", "members": ["native-a", "native-b"]})))
        self.assertTrue(created.get("ok"), created)
        room_id = created.get("room_id") or created.get("id")

        calls = self._patch_build_agent(lambda sid, msg: f"[peer-reply] 收到私聊：{msg[-40:]}")

        out = _run(bot_room_peer_dm(_Req({
            "from": "native-a",
            "to": "native-b",
            "text": "帮我核对一下预算数字",
        }), room_id=room_id))
        self.assertTrue(out.get("ok"), out)
        self.assertIn("peer-reply", out.get("result", ""))
        self.assertEqual(out.get("to_handle"), "vermes://native-b")
        self.assertEqual(out.get("from_handle"), "vermes://native-a")
        self.assertTrue(any("native-b" in (s or "") and ":peer" in (s or "") for s, _ in calls), calls)
        tl = _run(bot_room_timeline_get(_Req(), room_id=room_id))
        timeline = tl.get("timeline") or []
        joined = "\n".join((m.get("content") or "") for m in timeline)
        self.assertIn("私聊", joined)
        self.assertIn("peer-reply", joined)
        agent_msgs = [m for m in timeline if m.get("author_type") == "agent" and m.get("author_ref") == "native-b"]
        self.assertTrue(agent_msgs, timeline)

    def test_room_mention_triggers_peer_via_orchestrator(self):
        """验收原句：群聊中 A agent @B agent → peer 结果回群。"""
        from vermes_cli.blueprints.chat import (
            bot_room_message_send,
            bot_rooms_create,
            bot_room_timeline_get,
            api_native_agent_upsert,
        )
        import run_agent
        if not hasattr(run_agent, "_orig_AIAgent"):
            run_agent._orig_AIAgent = run_agent.AIAgent

        for body in (
            {"id": "res", "name": "研究助手", "provider": "deepseek", "model": "deepseek-chat",
             "system_prompt": "p", "toolsets": []},
            {"id": "ana", "name": "分析师", "provider": "deepseek", "model": "deepseek-chat",
             "system_prompt": "p", "toolsets": []},
        ):
            _run(api_native_agent_upsert(_Req(body)))

        created = _run(bot_rooms_create(_Req({"name": "协作房", "members": ["res", "ana"]})))
        room_id = created.get("room_id") or created.get("id")

        def _chat(sid, msg):
            # 研究助手群聊回复点名分析师；分析师 peer 腿（session :peer）给闭环结果
            if sid and "res" in sid and ":peer" not in sid:
                return "我负责调研。@分析师 请补充数据分析。"
            return "私聊闭环：数据分析已完成。"

        seen_prompts = self._patch_build_agent(_chat)

        out = _run(bot_room_message_send(_Req({"text": "@研究助手 调研一下市场"}), room_id=room_id))
        self.assertTrue(out.get("ok"), out)
        peer_hits = [p for s, p in seen_prompts if s and ":peer" in s and "ana" in s]
        self.assertTrue(peer_hits, seen_prompts)
        tl = out.get("timeline") or []
        if not tl:
            tl = _run(bot_room_timeline_get(_Req(), room_id=room_id)).get("timeline") or []
        joined = "\n".join((m.get("content") or "") for m in tl)
        self.assertIn("私聊", joined)
        self.assertIn("数据分析已完成", joined)

    def test_self_peer_endpoint_rejected(self):
        from vermes_cli.blueprints.chat import bot_room_peer_dm, bot_rooms_create, api_native_agent_upsert
        _run(api_native_agent_upsert(_Req({
            "id": "solo", "name": "独狼", "provider": "deepseek", "model": "m",
            "system_prompt": "p", "toolsets": [],
        })))
        created = _run(bot_rooms_create(_Req({"name": "自聊房", "members": ["solo"]})))
        room_id = created.get("room_id") or created.get("id")
        out = _run(bot_room_peer_dm(_Req({"from": "solo", "to": "solo", "text": "hi"}), room_id=room_id))
        self.assertFalse(out.get("ok"))
        self.assertIn("self-target", out.get("error", ""))


if __name__ == "__main__":
    unittest.main()
