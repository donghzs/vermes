"""B6 — Settings TrustGate 严格模式联动 registry fail_closed。"""
from __future__ import annotations

import unittest
from pathlib import Path


def _root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "tools" / "registry.py").exists():
            return p
    return here.parents[2]


ROOT = _root()


class TestStrictModeLinksFailClosed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.life = (ROOT / "vermes_cli/credential_lifecycle.py").read_text(encoding="utf-8")
        cls.settings = (ROOT / "frontend/src/components/Settings.vue").read_text(encoding="utf-8")

    def test_set_trust_gate_strict_updates_dispatch_mode(self):
        self.assertIn("set_dispatch_gate_mode", self.life)
        self.assertIn('"fail_closed" if enabled else "fail_open"', self.life)

    def test_settings_copy_mentions_block_behavior(self):
        self.assertIn("fail-closed", self.settings)

    def test_runtime_toggle_roundtrip(self):
        from tools.registry import registry
        from vermes_cli.credential_lifecycle import (
            is_trust_gate_strict,
            set_trust_gate_strict,
        )
        from vermes_cli.adapters.trust_gate import (
            PermissionSpec,
            TrustGate,
            ALLOW,
            ASK_USER,
            set_gate_mode,
            GATE_MODE_DEFAULT,
        )
        # 先回 default 基线
        set_trust_gate_strict(False)
        self.assertFalse(is_trust_gate_strict())
        self.assertEqual(registry.dispatch_gate_mode, "fail_open")
        spec = TrustGate.default_for_mechanism("cli_native")
        self.assertEqual(TrustGate.check(spec).decision, ALLOW)

        set_trust_gate_strict(True)
        self.assertTrue(is_trust_gate_strict())
        self.assertEqual(registry.dispatch_gate_mode, "fail_closed")
        self.assertEqual(TrustGate.check(spec).decision, ASK_USER)

        set_trust_gate_strict(False)
        self.assertFalse(is_trust_gate_strict())
        self.assertEqual(registry.dispatch_gate_mode, "fail_open")
        self.assertEqual(TrustGate.check(spec).decision, ALLOW)
        set_gate_mode(GATE_MODE_DEFAULT)


if __name__ == "__main__":
    unittest.main()
