"""微信扫码免费体验：Agnes 可选模型清单契约。

Agnes 3.0 已发布，免费体验下拉必须在 2.0/2.5 之外暴露 3.0。
后端 claim 响应 + One-API 授权 models 字符串 + 前端兜底列表须一致。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "vermes_cli" / "blueprints" / "quota.py").exists():
            return p
    return here.parents[2]


ROOT = _repo_root()
EXPECTED = ("agnes-3.0-flash", "agnes-2.5-flash", "agnes-2.0-flash")


class TestWeChatFreeAgnesModels(unittest.TestCase):
    def test_quota_claim_lists_agnes_30(self):
        src = (ROOT / "vermes_cli/blueprints/quota.py").read_text(encoding="utf-8")
        for mid in EXPECTED:
            self.assertIn(mid, src, f"quota.py claim models 缺 {mid}")
        # 3.0 应排在最前（前端优先展示）
        m = re.search(r'"models":\s*\[([^\]]+)\]', src)
        self.assertIsNotNone(m)
        ids = re.findall(r'"(agnes-[^"]+)"', m.group(1))
        self.assertEqual(ids[:3], list(EXPECTED), f"claim models 顺序应为 {EXPECTED}，实际 {ids}")

    def test_wechat_oneapi_allowlist_has_30(self):
        src = (ROOT / "vermes_cli/blueprints/wechat.py").read_text(encoding="utf-8")
        self.assertIn("agnes-3.0-flash", src)
        self.assertIn("agnes-2.5-flash", src)
        self.assertIn("agnes-2.0-flash", src)

    def test_frontend_chat_header_default_models(self):
        src = (ROOT / "frontend/src/components/ChatHeader.vue").read_text(encoding="utf-8")
        for mid in EXPECTED:
            self.assertIn(mid, src, f"ChatHeader defaultModels 缺 {mid}")

    def test_frontend_agents_page_forge_models(self):
        src = (ROOT / "frontend/src/components/AgentsPage.vue").read_text(encoding="utf-8")
        for mid in EXPECTED:
            self.assertIn(mid, src, f"AgentsPage defaultForgeModels 缺 {mid}")

    def test_model_prefix_routing_still_agnes(self):
        """agnes-3.0-* 仍须经前缀映射落到 agnes provider。"""
        chat = (ROOT / "vermes_cli/blueprints/chat.py").read_text(encoding="utf-8")
        self.assertIn('"agnes-": "agnes"', chat)


if __name__ == "__main__":
    unittest.main()
