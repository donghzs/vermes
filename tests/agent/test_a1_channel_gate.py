"""A′ 渠道硬门 — 更新为 M7 token 阈值语义后的回归（2026-09-20 终局）。

终局：auto = **token 阈值**，与 platform/cwd 解耦（IM 超限同样降级）。
本文件保留对「off / 未超限 / 未知配置」的否定测试；并记录旧 coding-dir 语义已废止。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestChannelGateUnderTokenSemantics(unittest.TestCase):
    def setUp(self):
        import vermes_cli.config as cfg_mod
        self.cfg_mod = cfg_mod
        self._real = cfg_mod.load_config

    def tearDown(self):
        self.cfg_mod.load_config = self._real

    def test_auto_under_threshold_never_demotes_on_messaging(self):
        from agent.prompt_builder import resolve_compact_skill_categories
        self.cfg_mod.load_config = lambda *a, **k: {
            "agent": {
                "compact_skill_categories": "auto",
                "skill_index_compact_threshold_bytes": 10**9,
            }
        }
        code = Path(tempfile.mkdtemp(prefix="a1-code-"))
        (code / "pyproject.toml").write_text("x", encoding="utf-8")
        for platform in ("telegram", "feishu", "qqbot", "discord", "slack", ""):
            self.assertIsNone(
                resolve_compact_skill_categories(code, platform=platform),
                platform,
            )

    def test_off_never_demotes_messaging(self):
        from agent.prompt_builder import resolve_compact_skill_categories
        self.cfg_mod.load_config = lambda *a, **k: {
            "agent": {"compact_skill_categories": "off"}
        }
        code = Path(tempfile.mkdtemp(prefix="a1-off-"))
        (code / "AGENTS.md").write_text("#", encoding="utf-8")
        self.assertIsNone(resolve_compact_skill_categories(code, platform="telegram"))


if __name__ == "__main__":
    unittest.main()
