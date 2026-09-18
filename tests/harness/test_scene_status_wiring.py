"""U-P0-6 场景顶栏状态 — 简洁优先（细 chips / 异常才 banner）。"""
from __future__ import annotations

import unittest
from pathlib import Path


def _root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "frontend/src/components/SceneStatusBar.vue").exists():
            return p
    return here.parents[2]


ROOT = _root()


class TestSceneStatusWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bar = (ROOT / "frontend/src/components/SceneStatusBar.vue").read_text(encoding="utf-8")
        cls.hook = (ROOT / "frontend/src/composables/useHarnessLight.js").read_text(encoding="utf-8")
        cls.scholar = (ROOT / "frontend/src/components/ScholarForgePanel.vue").read_text(encoding="utf-8")
        cls.studio = (ROOT / "frontend/src/components/StudioChat.vue").read_text(encoding="utf-8")
        cls.bot = (ROOT / "frontend/src/components/BotRooms.vue").read_text(encoding="utf-8")

    def test_bar_prefers_quiet_chips(self):
        self.assertIn("scene-status-chips", self.bar)
        self.assertIn("PrereqBanner", self.bar)
        self.assertIn("showAlert", self.bar)

    def test_harness_only_when_degraded(self):
        self.assertIn("degraded", self.hook)
        self.assertIn("return null", self.hook)
        self.assertIn("/api/harness/status", self.hook)

    def test_three_scenes_wired(self):
        self.assertIn("SceneStatusBar", self.scholar)
        self.assertIn("sceneItems", self.scholar)
        self.assertIn("SceneStatusBar", self.studio)
        self.assertIn("studioSceneItems", self.studio)
        self.assertIn("SceneStatusBar", self.bot)
        self.assertIn("sceneItems", self.bot)

    def test_scholar_hides_chips_when_blocked(self):
        self.assertIn("sceneReady", self.scholar)
        self.assertIn("if (!sceneReady.value) return []", self.scholar)

    def test_studio_hides_when_unconfigured(self):
        self.assertIn("if (!isConfigured.value) return []", self.studio)


if __name__ == "__main__":
    unittest.main()
