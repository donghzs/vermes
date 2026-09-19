"""C3 — stability 热路径探针产品化（默认关 / env / 模块开关 / agent 属性）。"""
from __future__ import annotations

import os
import unittest


class TestStabilityProbeProductization(unittest.TestCase):
    def setUp(self):
        from harness.stability_hotpath import set_stability_probe_enabled
        set_stability_probe_enabled(False)
        os.environ.pop("VERMES_STABILITY_PROBE", None)

    def tearDown(self):
        from harness.stability_hotpath import set_stability_probe_enabled
        set_stability_probe_enabled(False)
        os.environ.pop("VERMES_STABILITY_PROBE", None)

    def test_default_off_zero_regression(self):
        from harness.stability_hotpath import (
            is_stability_probe_enabled,
            probe_tool_stability,
        )
        class A:
            pass
        self.assertFalse(is_stability_probe_enabled(A()))
        self.assertIsNone(probe_tool_stability(A(), "web_search", {"q": "x"}))

    def test_module_toggle_enables_without_agent_attr(self):
        from harness.stability_hotpath import (
            is_stability_probe_enabled,
            set_stability_probe_enabled,
        )
        class A:
            pass
        set_stability_probe_enabled(True)
        self.assertTrue(is_stability_probe_enabled(A()))
        set_stability_probe_enabled(False)
        self.assertFalse(is_stability_probe_enabled(A()))

    def test_env_flag(self):
        from harness.stability_hotpath import is_stability_probe_enabled
        class A:
            pass
        os.environ["VERMES_STABILITY_PROBE"] = "1"
        self.assertTrue(is_stability_probe_enabled(A()))

    def test_agent_attr_still_wins(self):
        from harness.stability_hotpath import is_stability_probe_enabled
        class A:
            _enable_stability_probe = True
        self.assertTrue(is_stability_probe_enabled(A()))

    def test_is_probe_enabled_respects_hot_path_set(self):
        from harness.stability_hotpath import (
            is_probe_enabled,
            set_stability_probe_enabled,
        )
        class A:
            pass
        set_stability_probe_enabled(True)
        self.assertTrue(is_probe_enabled(A(), "web_search"))
        self.assertFalse(is_probe_enabled(A(), "read_file"))

    def test_settings_ui_toggle_present(self):
        from pathlib import Path
        here = Path(__file__).resolve()
        root = next(p for p in here.parents if (p / "frontend/src/components/Settings.vue").exists())
        ui = (root / "frontend/src/components/Settings.vue").read_text(encoding="utf-8")
        self.assertIn("stability-probe-toggle", ui)
        self.assertIn("stability_probe", ui)
        self.assertIn("PATCH", ui)
        cfg = (root / "vermes_cli/blueprints/config.py").read_text(encoding="utf-8")
        self.assertIn("set_stability_probe_enabled", cfg)


if __name__ == "__main__":
    unittest.main()
