"""C1 — channel_push / 桌面实时同步：真行为测试（订阅广播 → 客户端收到）。"""
from __future__ import annotations

import asyncio
import unittest


class TestChannelPushRealtime(unittest.TestCase):
    def test_broadcast_reaches_sse_subscriber(self):
        """真行为：订阅 SSE 队列后 broadcast，断言队列收到同一 payload。"""
        from vermes_cli import web_server as ws

        async def _run():
            q: asyncio.Queue = asyncio.Queue()
            ws._sse_channel_subscribers.add(q)
            try:
                payload = {
                    "type": "room_update",
                    "topic": "room:r1",
                    "event": "room_message",
                    "room_id": "r1",
                }
                await ws._channel_sync_broadcast(payload)
                got = await asyncio.wait_for(q.get(), timeout=1.0)
                self.assertEqual(got.get("event"), "room_message")
                self.assertEqual(got.get("room_id"), "r1")
            finally:
                ws._sse_channel_subscribers.discard(q)

        asyncio.new_event_loop().run_until_complete(_run())

    def test_broadcast_fanout_multiple_subscribers(self):
        from vermes_cli import web_server as ws

        async def _run():
            q1: asyncio.Queue = asyncio.Queue()
            q2: asyncio.Queue = asyncio.Queue()
            ws._sse_channel_subscribers.add(q1)
            ws._sse_channel_subscribers.add(q2)
            try:
                await ws._channel_sync_broadcast({"type": "room_update", "event": "member_change", "room_id": "r2"})
                a = await asyncio.wait_for(q1.get(), timeout=1.0)
                b = await asyncio.wait_for(q2.get(), timeout=1.0)
                self.assertEqual(a.get("event"), "member_change")
                self.assertEqual(b.get("event"), "member_change")
            finally:
                ws._sse_channel_subscribers.discard(q1)
                ws._sse_channel_subscribers.discard(q2)

        asyncio.new_event_loop().run_until_complete(_run())

    def test_sse_endpoint_registered_and_public(self):
        from pathlib import Path
        import vermes_cli.web_server as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        self.assertIn('"/api/channels/events"', src)
        start = src.index("_PUBLIC_API_PATHS")
        end = src.index("def _has_valid_session_token", start)
        self.assertIn('"/api/channels/events"', src[start:end])
        self.assertIn("_sse_channel_subscribers", src)

    def test_frontend_room_update_handles_announcement(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        br = (root / "frontend/src/stores/botRoom.js").read_text(encoding="utf-8")
        self.assertIn("onRoomUpdate", br)
        self.assertIn("room_updated", br)
        self.assertIn("ensureRoomRealtime", br)
        self.assertIn("/api/channels/events", br)


class TestBotRoomUpdateHandlerBehavior(unittest.TestCase):
    def test_room_updated_triggers_reload(self):
        from pathlib import Path
        import re
        src = (Path(__file__).resolve().parents[2] / "frontend/src/stores/botRoom.js").read_text(encoding="utf-8")
        m = re.search(r"async onRoomUpdate\(msg\)\s*\{([\s\S]*?)\n    \},\n", src)
        self.assertIsNotNone(m, "onRoomUpdate not found")
        body = m.group(1)
        self.assertIn("room_updated", body)
        self.assertIn("loadRooms", body)


if __name__ == "__main__":
    unittest.main()
