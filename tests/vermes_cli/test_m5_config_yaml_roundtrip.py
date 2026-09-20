"""M5 — 凭据写路径 config.yaml 保注释（与 M4 home-channel 同源纪律）。"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


def _tmp_home() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="m5-cfg-rt-"))
    os.environ["VERMES_HOME"] = str(tmp)
    return tmp


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


SAMPLE = """\
# user comment keep me
platforms:
  # feishu block
  feishu:
    enabled: false
    # inner note about token
    token: old
"""


class TestRoundtripHelpers(unittest.TestCase):
    def setUp(self):
        self.home = _tmp_home()
        self.cfg = self.home / "config.yaml"
        self.cfg.write_text(SAMPLE, encoding="utf-8")

    def test_load_and_mutate_preserves_comments(self):
        from utils import load_roundtrip_yaml, atomic_roundtrip_yaml_dump
        data = load_roundtrip_yaml(self.cfg)
        data["platforms"]["feishu"]["enabled"] = True
        data["platforms"]["feishu"]["token"] = "new-token"
        atomic_roundtrip_yaml_dump(self.cfg, data)
        text = self.cfg.read_text(encoding="utf-8")
        self.assertIn("# user comment keep me", text)
        self.assertIn("# inner note about token", text)
        self.assertIn("new-token", text)
        self.assertNotIn("token: old", text)

    def test_mutate_helper_roundtrip(self):
        from utils import atomic_roundtrip_yaml_mutate
        def mut(cfg):
            cfg["platforms"]["feishu"]["api_key"] = "k1"
        atomic_roundtrip_yaml_mutate(self.cfg, mut)
        text = self.cfg.read_text(encoding="utf-8")
        self.assertIn("# user comment keep me", text)
        self.assertIn("api_key: k1", text)


class TestCredentialEndpointsKeepComments(unittest.TestCase):
    """PUT 凭据 / DELETE 清除 / POST toggle — 三条写路径都不得抹注释。"""

    def setUp(self):
        self.home = _tmp_home()
        self.cfg = self.home / "config.yaml"
        self.cfg.write_text(SAMPLE, encoding="utf-8")
        # 避免真实 gateway control 干扰
        import vermes_cli.blueprints._gateway_control as gc
        async def _noop(*a, **k):
            return {"ok": True, "note": "noop"}
        self._gc = gc
        self._orig = {}
        for name in ("reload_channel", "disconnect_channel", "connect_channel"):
            if hasattr(gc, name):
                self._orig[name] = getattr(gc, name)
                setattr(gc, name, _noop)

    def tearDown(self):
        for name, fn in self._orig.items():
            setattr(self._gc, name, fn)

    def _assert_comments_intact(self):
        text = self.cfg.read_text(encoding="utf-8")
        self.assertIn("# user comment keep me", text)
        self.assertIn("# inner note about token", text)

    def test_put_save_channel_keeps_comments(self):
        from vermes_cli.blueprints import gateway_channels as bp
        # feishu schema: app_id/app_secret 走 extra 存储
        req = SimpleNamespace(fields={"app_id": "cli-new", "app_secret": "sec-new"}, enabled=True)
        out = _run(bp.save_channel("feishu", req))
        self.assertTrue(out.get("ok"), out)
        self._assert_comments_intact()
        text = self.cfg.read_text(encoding="utf-8")
        self.assertIn("cli-new", text)
        self.assertIn("sec-new", text)

    def test_delete_clear_keeps_comments(self):
        from vermes_cli.blueprints import gateway_channels as bp
        out = _run(bp.clear_channel("feishu"))
        self.assertTrue(out.get("ok"), out)
        self._assert_comments_intact()
        text = self.cfg.read_text(encoding="utf-8")
        # 凭据值已清；注释仍在（删除键不应带走兄弟注释）
        self.assertNotIn("token: old", text)
        self.assertIn("enabled: false", text)

    def test_toggle_keeps_comments_and_flips_enabled(self):
        from vermes_cli.blueprints import gateway_channels as bp
        out = _run(bp.toggle_channel("feishu"))
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("enabled"))
        self._assert_comments_intact()
        text = self.cfg.read_text(encoding="utf-8")
        self.assertIn("enabled: true", text)

    def test_save_config_yaml_uses_roundtrip_not_yaml_dump(self):
        src = (Path(__file__).resolve().parents[2] /
               "vermes_cli/blueprints/gateway_channels.py").read_text(encoding="utf-8")
        self.assertIn("atomic_roundtrip_yaml_dump", src)
        self.assertIn("_load_config_rt", src)
        # 旧写法不得残留
        self.assertNotIn("yaml.dump(data, f", src)


if __name__ == "__main__":
    unittest.main()
