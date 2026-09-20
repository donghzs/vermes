"""M4/A7 — GUI home channel 真行为测试（共享解析器单一口径）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


def _tmp_home() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="m4-home-channel-"))
    os.environ["VERMES_HOME"] = str(tmp)
    return tmp


class TestHomeChannelWriteRead(unittest.TestCase):
    def setUp(self):
        self.home = _tmp_home()
        # 清掉可能残留的 env
        for k in list(os.environ):
            if k.endswith("_HOME_CHANNEL"):
                os.environ.pop(k, None)

    def test_dual_write_then_resolve_single_source(self):
        """写 config+env 后，resolve_home_channel_chat_id 当次读到同一值。"""
        from vermes_cli.gateway_channels import write_home_channel, read_home_channel, home_env_var_name

        out = write_home_channel("feishu", "oc_home_123", name="默认群")
        self.assertEqual(out["chat_id"], "oc_home_123")
        self.assertEqual(out["env_key"], home_env_var_name("feishu"))

        # config.yaml 落盘
        import yaml
        cfg = yaml.safe_load((self.home / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["platforms"]["feishu"]["home_channel"]["chat_id"], "oc_home_123")

        # .env 落盘
        env_text = (self.home / ".env").read_text(encoding="utf-8")
        self.assertIn("oc_home_123", env_text)

        # 共享解析器（M4 红线：不许另起口径）
        from gateway.gateway_utils import resolve_home_channel_chat_id
        self.assertEqual(resolve_home_channel_chat_id("feishu"), "oc_home_123")
        self.assertEqual(read_home_channel("feishu")["chat_id"], "oc_home_123")

    def test_clear_home_channel(self):
        from vermes_cli.gateway_channels import write_home_channel, read_home_channel
        from gateway.gateway_utils import resolve_home_channel_chat_id
        write_home_channel("telegram", "42")
        self.assertEqual(resolve_home_channel_chat_id("telegram"), "42")
        out = write_home_channel("telegram", "")
        self.assertEqual(out["chat_id"], "")
        self.assertEqual(resolve_home_channel_chat_id("telegram"), "")

    def test_env_override_still_wins(self):
        """解析顺序 env 优先：显式环境变量覆盖不被 config 推翻（E4 取舍）。"""
        from vermes_cli.gateway_channels import write_home_channel, home_env_var_name
        from gateway.gateway_utils import resolve_home_channel_chat_id
        write_home_channel("discord", "from_config")
        env_key = home_env_var_name("discord")
        os.environ[env_key] = "from_env_override"
        self.assertEqual(resolve_home_channel_chat_id("discord"), "from_env_override")


class TestFrontendHomeChannelSurface(unittest.TestCase):
    def test_settings_has_home_channel_entry(self):
        src = (Path(__file__).resolve().parents[2] / "frontend/src/components/Settings.vue").read_text(encoding="utf-8")
        self.assertIn("默认通知频道", src)
        self.assertIn("saveChannelHome", src)
        self.assertIn("putGatewayChannelHome", src)
        self.assertIn("home_channel", src)

    def test_api_helper_exists(self):
        src = (Path(__file__).resolve().parents[2] / "frontend/src/services/api.js").read_text(encoding="utf-8")
        self.assertIn("putGatewayChannelHome", src)
        self.assertIn("/home-channel", src)

    def test_ci_lanes_exist_and_safe(self):
        wf = Path(__file__).resolve().parents[2] / ".github/workflows"
        self.assertTrue((wf / "js-tests.yml").exists())
        self.assertTrue((wf / "tests-os.yml").exists())
        self.assertTrue((wf / "install-e2e.yml").exists())
        # 占位 lane 必须 if: false，避免误伤现有流水线
        self.assertIn("if: false", (wf / "tests-os.yml").read_text(encoding="utf-8"))
        self.assertIn("if: false", (wf / "install-e2e.yml").read_text(encoding="utf-8"))

    def test_upstream_sync_measured_versions(self):
        src = (Path(__file__).resolve().parents[2] / "UPSTREAM_SYNC.md").read_text(encoding="utf-8")
        self.assertIn("2.5.0", src)
        self.assertIn("v0.21.3", src)
        self.assertIn("NousResearch/hermes-agent", src)
        self.assertNotIn("0.18.2+", src)

    def test_diverge_baseline_report_exists(self):
        p = Path(__file__).resolve().parents[2] / "reports/diverge-baseline-20260920.md"
        self.assertTrue(p.exists())
        text = p.read_text(encoding="utf-8")
        self.assertIn("Jaccard", text)
        self.assertIn("60", text)  # 上游核心工具数


if __name__ == "__main__":
    unittest.main()
