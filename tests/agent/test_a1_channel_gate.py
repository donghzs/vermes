"""A′ 渠道硬门 — auto = 纯渠道门（2026-09-20 拍板）。

终局 auto 语义：
- platform ∈ _INTERACTIVE_CODING_PLATFORMS → 降级
- messaging / 未知 / 空 platform → 永不降级
- 无成本/比例/字节阈值
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestAutoPureChannelGate(unittest.TestCase):
    def setUp(self):
        import vermes_cli.config as cfg_mod
        self.cfg_mod = cfg_mod
        self._real = cfg_mod.load_config
        self.cfg_mod.load_config = lambda *a, **k: {
            "agent": {"compact_skill_categories": "auto"}
        }

    def tearDown(self):
        self.cfg_mod.load_config = self._real

    def test_auto_never_demotes_on_messaging(self):
        """A′：messaging / 未知 / 空 platform 在 auto 下永不降级。"""
        from agent.prompt_builder import resolve_compact_skill_categories
        code = Path(tempfile.mkdtemp(prefix="a1-code-"))
        (code / "pyproject.toml").write_text("x", encoding="utf-8")
        for platform in ("telegram", "feishu", "qqbot", "discord", "slack", "", None):
            self.assertIsNone(
                resolve_compact_skill_categories(code, platform=platform),
                platform,
            )

    def test_auto_demotes_on_interactive(self):
        """auto + 交互式渠道 → 降级（纯渠道门，无成本条件）。"""
        from agent.prompt_builder import resolve_compact_skill_categories
        code = Path(tempfile.mkdtemp(prefix="a1-cli-"))
        (code / "pyproject.toml").write_text("x", encoding="utf-8")
        for platform in ("cli", "web", "desktop", "tui", "acp", "api"):
            cats = resolve_compact_skill_categories(code, platform=platform)
            self.assertIsNotNone(cats, platform)
            self.assertIn("creative", cats)

    def test_no_cost_fields_in_default_config(self):
        """成本面已删：配置中不再有 ratio/threshold 字段。"""
        from vermes_cli.config import DEFAULT_CONFIG
        agent = DEFAULT_CONFIG["agent"]
        self.assertEqual(agent["compact_skill_categories"], "off")
        self.assertNotIn("skill_index_compact_ratio_pct", agent)
        self.assertNotIn("skill_index_compact_threshold_bytes", agent)

    def test_no_model_context_window_helper(self):
        """无残留成本面死代码。"""
        import agent.prompt_builder as pb
        self.assertFalse(hasattr(pb, "_model_context_window"))
        self.assertFalse(hasattr(pb, "estimate_skills_index_bytes"))
        src = Path(pb.__file__).read_text(encoding="utf-8")
        self.assertNotIn("_model_context_window", src)
        self.assertNotIn("estimate_skills_index_bytes", src)
        self.assertNotIn("skill_index_compact_ratio_pct", src)
        self.assertNotIn("skill_index_compact_threshold_bytes", src)

    def test_off_never_demotes_messaging(self):
        from agent.prompt_builder import resolve_compact_skill_categories
        self.cfg_mod.load_config = lambda *a, **k: {
            "agent": {"compact_skill_categories": "off"}
        }
        code = Path(tempfile.mkdtemp(prefix="a1-off-"))
        (code / "AGENTS.md").write_text("#", encoding="utf-8")
        self.assertIsNone(resolve_compact_skill_categories(code, platform="telegram"))

    def test_on_always_demotes_even_on_im(self):
        """显式 on 跨渠道降级（用户要求，非 auto）。"""
        from agent.prompt_builder import resolve_compact_skill_categories
        self.cfg_mod.load_config = lambda *a, **k: {
            "agent": {"compact_skill_categories": "on"}
        }
        self.assertIsNotNone(resolve_compact_skill_categories(platform="telegram"))
        self.assertIsNotNone(resolve_compact_skill_categories(platform="cli"))

    def test_unknown_mode_fails_safe_off(self):
        from agent.prompt_builder import resolve_compact_skill_categories
        self.cfg_mod.load_config = lambda *a, **k: {
            "agent": {"compact_skill_categories": "maybe"}
        }
        self.assertIsNone(resolve_compact_skill_categories(platform="cli"))


if __name__ == "__main__":
    unittest.main()
