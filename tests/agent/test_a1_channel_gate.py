"""A′ 渠道硬门否定测试（真行为）。

纪律：auto 模式只在「交互式编码平台 + 代码目录」同时成立时降级。
messaging/群聊渠道（telegram/feishu/qqbot/discord/slack/...）与未知/空 platform
**永远**不得 names-only，即便 gateway 进程 cwd 是仓库根（有 AGENTS.md/pyproject.toml）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestChannelGate(unittest.TestCase):
    """渠道硬门：messaging 渠道 auto 下永不降级。"""

    def setUp(self):
        import vermes_cli.config as cfg_mod
        self.cfg_mod = cfg_mod
        self._real = cfg_mod.load_config
        # 模拟 auto 已打开（用户点了 M7 auto）
        cfg_mod.load_config = lambda *a, **k: {"agent": {"compact_skill_categories": "auto"}}
        # 模拟 gateway 单进程 cwd = 仓库根：三个 marker 全中
        self.code_dir = Path(tempfile.mkdtemp(prefix="a1-code-root-"))
        (self.code_dir / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
        (self.code_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (self.code_dir / "package.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.cfg_mod.load_config = self._real

    def _resolve(self, platform):
        from agent.prompt_builder import resolve_compact_skill_categories
        return resolve_compact_skill_categories(self.code_dir, platform=platform)

    def test_messaging_channels_never_demote(self):
        for platform in (
            "telegram", "feishu", "qqbot", "discord", "slack", "whatsapp",
            "signal", "matrix", "wechat", "weixin", "line", "mattermost",
            "dingtalk", "wecom", "sms", "email", "nostr", "zalo", "irc",
        ):
            self.assertIsNone(self._resolve(platform), platform)

    def test_unknown_platform_never_demotes(self):
        # 未知 platform（插件渠道 / 未来新增）→ 默认拒绝，宁可不降级也不误伤
        self.assertIsNone(self._resolve("some-future-plugin"))
        self.assertIsNone(self._resolve("group-chat-xyz"))

    def test_empty_platform_never_demotes(self):
        # platform 缺失/空字符串 → 默认拒绝（fail-safe）
        self.assertIsNone(self._resolve(""))
        self.assertIsNone(self._resolve(None))

    def test_interactive_platforms_demote_in_coding_dir(self):
        from agent.prompt_builder import _NON_CODING_SKILL_CATEGORIES
        for platform in ("cli", "web", "desktop", "tui", "acp", "local", "api"):
            cats = self._resolve(platform)
            self.assertIsNotNone(cats, platform)
            self.assertIn("creative", cats, platform)


class TestChannelGateNotCodingDir(unittest.TestCase):
    """交互式平台但 cwd 非代码目录 → 仍不降级（原有语义不回归）。"""

    def setUp(self):
        import vermes_cli.config as cfg_mod
        self.cfg_mod = cfg_mod
        self._real = cfg_mod.load_config
        cfg_mod.load_config = lambda *a, **k: {"agent": {"compact_skill_categories": "auto"}}
        self.plain_dir = Path(tempfile.mkdtemp(prefix="a1-plain-"))

    def tearDown(self):
        self.cfg_mod.load_config = self._real

    def test_interactive_platform_plain_dir_no_demote(self):
        from agent.prompt_builder import resolve_compact_skill_categories
        self.assertIsNone(resolve_compact_skill_categories(self.plain_dir, platform="cli"))
        self.assertIsNone(resolve_compact_skill_categories(self.plain_dir, platform="desktop"))


class TestSystemPromptPassesPlatform(unittest.TestCase):
    """调用点（system_prompt）确实把 agent.platform 传给 resolve。"""

    def test_call_site_passes_platform(self):
        root = Path(__file__).resolve().parents[2]
        src = (root / "agent/system_prompt.py").read_text(encoding="utf-8")
        # 断言调用点传了 platform=getattr(agent, "platform", None)
        self.assertIn("resolve_compact_skill_categories(", src)
        self.assertIn('getattr(agent, "platform", None)', src)


if __name__ == "__main__":
    unittest.main()
