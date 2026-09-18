"""U-P0-5 通知中心 — 应用内铃铛 + 托盘未读 + 类型可关（源码契约）。"""
from __future__ import annotations

import unittest
from pathlib import Path


def _root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "frontend/src/components/NotificationCenter.vue").exists():
            return p
    return here.parents[2]


ROOT = _root()


class TestNotificationCenterWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = (ROOT / "frontend/src/stores/notifications.js").read_text(encoding="utf-8")
        cls.panel = (ROOT / "frontend/src/components/NotificationCenter.vue").read_text(encoding="utf-8")
        cls.header = (ROOT / "frontend/src/components/ChatHeader.vue").read_text(encoding="utf-8")
        cls.settings = (ROOT / "frontend/src/components/Settings.vue").read_text(encoding="utf-8")
        cls.preload = (ROOT / "electron/preload.js").read_text(encoding="utf-8")
        cls.mainjs = (ROOT / "electron/main.js").read_text(encoding="utf-8")

    def test_store_has_mute_and_dedupe(self):
        self.assertIn("setPref", self.store)
        self.assertIn("vermes-notify-prefs", self.store)
        self.assertIn("_seenKeys", self.store)
        self.assertIn("notifyHarnessDegraded", self.store)

    def test_header_mounts_bell(self):
        self.assertIn("NotificationCenter", self.header)
        self.assertIn("notifyHarnessDegraded", self.header)

    def test_settings_exposes_category_toggles(self):
        self.assertIn("useNotifications", self.settings)
        self.assertIn("notify.setPref", self.settings)

    def test_tray_unread_bridge(self):
        self.assertIn("setTrayUnread", self.preload)
        self.assertIn("tray:unread", self.mainjs)
        self.assertIn("条未读通知", self.mainjs)

    def test_panel_has_prefs_and_empty_state(self):
        self.assertIn("notify-bell", self.panel)
        self.assertIn("notify-panel", self.panel)
        self.assertIn("暂无通知", self.panel)
        self.assertIn("setPref", self.panel)


if __name__ == "__main__":
    unittest.main()
