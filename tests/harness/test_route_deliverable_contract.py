"""U-P0-2/3 — route 与 deliverable 契约字段（E-P0-5 v2.5-s1）。"""
from __future__ import annotations

import re
import unittest


def _repo_root():
    from pathlib import Path
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "vermes_cli" / "blueprints" / "chat.py").exists():
            return p
    return here.parents[2]


ROOT = _repo_root()


class TestRouteAndDeliverableWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat_src = (ROOT / "vermes_cli/blueprints/chat.py").read_text(encoding="utf-8")
        cls.transport = (ROOT / "frontend/src/services/chat-transport.js").read_text(encoding="utf-8")
        cls.chat_js = (ROOT / "frontend/src/stores/chat.js").read_text(encoding="utf-8")
        cls.delivery = (ROOT / "frontend/src/components/DeliveryCard.vue").read_text(encoding="utf-8")
        cls.header = (ROOT / "frontend/src/components/ChatHeader.vue").read_text(encoding="utf-8")

    def test_backend_builds_route_event(self):
        self.assertIn('"type": "route"', self.chat_src)
        self.assertIn('"contract": "v2.5-s1"', self.chat_src)
        self.assertIn("_route_event", self.chat_src)
        self.assertIn('yield f\'data: {json.dumps(_route_event', self.chat_src)

    def test_backend_delivery_enriched(self):
        self.assertIn("_session_deliverable_items", self.chat_src)
        self.assertIn('"items": _items', self.chat_src)
        self.assertIn('"contract": "v2.5-s1"', self.chat_src)
        # 外证：write_file 磁盘存在
        self.assertIn("verified", self.chat_src)
        self.assertIn("os.path.exists(path)", self.chat_src)

    def test_frontend_transport_route(self):
        self.assertIn("type === 'route'", self.transport)
        self.assertIn("onRoute", self.transport)

    def test_frontend_chat_store_route(self):
        self.assertIn("sessionRouteInfo", self.chat_js)
        self.assertIn("currentRouteInfo", self.chat_js)
        self.assertIn("onRoute:", self.chat_js)
        self.assertIn("deliverableItems", self.chat_js)
        self.assertIn("deliveryText", self.chat_js)
        # delivery 消息必须带上契约字段（空格/换行容忍）
        compact = re.sub(r"\s+", "", self.chat_js)
        self.assertIn("items:deliverableItems", compact)
        self.assertIn("route:routeInfo", compact)
        # 禁止重复声明 sessionRouteInfo（vite/rollup 会构建失败）
        self.assertEqual(self.chat_js.count("const sessionRouteInfo"), 1)

    def test_frontend_ui_surfaces(self):
        self.assertIn("routeLabel", self.header)
        self.assertIn("currentRouteInfo", self.header)
        self.assertIn("items", self.delivery)
        self.assertIn("verifyLabel", self.delivery)
        self.assertIn("暂无信号", self.delivery)


if __name__ == "__main__":
    unittest.main()
