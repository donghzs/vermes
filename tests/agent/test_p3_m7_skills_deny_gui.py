"""P3 deny-list 细校 + M7 GUI 配置契约（真行为）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class TestP3DenyList(unittest.TestCase):
    def test_p3_extras_in_deny_list(self):
        from agent.prompt_builder import _NON_CODING_SKILL_CATEGORIES as deny
        for name in (
            "metaphysics", "weather", "eco-decision-framework",
            "wechat-official-account", "email-skill", "imap-smtp-email",
            "agnes-multi-modal-integration", "agnes-shot-generator",
            "agnes-video-i2v", "agnes-video-keyframes", "agnes-video-multi-img",
            "agnes-video-t2v",
            # P1 既有
            "daily", "content-marketing", "openclaw-imports", "creative",
        ):
            self.assertIn(name, deny, name)

    def test_conservative_keeps_coding_adjacent(self):
        from agent.prompt_builder import _NON_CODING_SKILL_CATEGORIES as deny
        for name in (
            "research", "software-development", "devops", "software-engineering",
            "autonomous-ai-agents", "github", "vermes", "ppt", "docx",
            "reportlab-chinese-pdf", "mlops", "security", "hardware",
        ):
            parent = name.split("/", 1)[0]
            self.assertNotIn(parent, deny, name)

    def test_auto_pure_channel_gate(self):
        """拍板：auto = 纯渠道门。"""
        from agent import prompt_builder as pb
        import vermes_cli.config as cfg_mod
        code = Path(tempfile.mkdtemp(prefix="p3-code-"))
        (code / "package.json").write_text("{}", encoding="utf-8")
        real_load = cfg_mod.load_config
        try:
            cfg_mod.load_config = lambda *a, **k: {
                "agent": {"compact_skill_categories": "auto"}
            }
            self.assertIsNone(pb.resolve_compact_skill_categories(code, platform="telegram"))
            cats = pb.resolve_compact_skill_categories(code, platform="cli")
            self.assertIsNotNone(cats)
            self.assertIn("agnes-video-t2v", cats)
        finally:
            cfg_mod.load_config = real_load




class TestDefaultConfigOff(unittest.TestCase):
    def test_default_agent_compact_skill_categories_off(self):
        from vermes_cli.config import DEFAULT_CONFIG
        self.assertEqual(
            (DEFAULT_CONFIG.get("agent") or {}).get("compact_skill_categories"),
            "off",
        )


class TestM7FrontendSurface(unittest.TestCase):
    def _settings(self) -> str:
        root = Path(__file__).resolve().parents[2]
        return (root / "frontend/src/components/Settings.vue").read_text(encoding="utf-8")

    def test_security_tab_has_compact_skill_ui(self):
        src = self._settings()
        self.assertIn("编码场景技能索引", src)
        self.assertIn("compactSkillMode", src)
        self.assertIn("setCompactSkillMode", src)
        self.assertIn("compact_skill_categories", src)
        self.assertIn("loadCompactSkills", src)

    def test_patches_agent_config(self):
        src = self._settings()
        self.assertIn("{ agent: { compact_skill_categories: mode } }", src)
        self.assertIn("method: 'PATCH'", src)


class TestM7ConfigRoundtrip(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="m7-cfg-"))
        os.environ["VERMES_HOME"] = str(self.home)
        for k in list(os.environ):
            if k.endswith("_HOME_CHANNEL"):
                os.environ.pop(k, None)

    def test_patch_config_writes_agent_compact(self):
        """PATCH /api/config 深合并写入 agent.compact_skill_categories。"""
        from vermes_cli.blueprints import config as cfg_bp
        import asyncio

        async def _patch():
            return await cfg_bp.patch_config({"agent": {"compact_skill_categories": "auto"}})

        out = asyncio.new_event_loop().run_until_complete(_patch())
        self.assertTrue(out.get("ok"), out)
        import yaml
        data = yaml.safe_load((self.home / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(data["agent"]["compact_skill_categories"], "auto")

        async def _patch_off():
            return await cfg_bp.patch_config({"agent": {"compact_skill_categories": "off"}})
        asyncio.new_event_loop().run_until_complete(_patch_off())
        data = yaml.safe_load((self.home / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(data["agent"]["compact_skill_categories"], "off")

    def test_resolve_reads_patched_config(self):
        from vermes_cli.blueprints import config as cfg_bp
        from agent import prompt_builder as pb
        import asyncio

        code = Path(tempfile.mkdtemp(prefix="m7-code-"))
        (code / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        self.assertIsNone(pb.resolve_compact_skill_categories(code, platform="cli"))
        asyncio.new_event_loop().run_until_complete(
            cfg_bp.patch_config({
                "agent": {"compact_skill_categories": "auto"},
            })
        )
        cats = pb.resolve_compact_skill_categories(code, platform="cli")
        self.assertIsNotNone(cats)
        self.assertIn("agnes-video-t2v", cats)
        self.assertIn("metaphysics", cats)
        self.assertIsNone(pb.resolve_compact_skill_categories(code, platform="telegram"))


if __name__ == "__main__":
    unittest.main()
