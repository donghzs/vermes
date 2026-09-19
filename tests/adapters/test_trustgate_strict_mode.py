"""B2 — TrustGate 严格模式 gate_mode（default 零回归 / strict 收紧）。"""
from __future__ import annotations

import unittest

from vermes_cli.adapters.trust_gate import (
    ALLOW,
    ASK_USER,
    DENY,
    GATE_MODE_DEFAULT,
    GATE_MODE_STRICT,
    PermissionSpec,
    SANDBOX_NONE,
    TrustGate,
    get_gate_mode,
    get_gate_stats,
    is_strict,
    reset_gate_stats,
    set_gate_mode,
)


class TestTrustGateStrictMode(unittest.TestCase):
    def setUp(self):
        set_gate_mode(GATE_MODE_DEFAULT)
        try:
            reset_gate_stats(clear_persisted=True)
        except TypeError:
            reset_gate_stats()

    def tearDown(self):
        set_gate_mode(GATE_MODE_DEFAULT)

    def test_default_mode_cli_native_still_allows(self):
        """零回归：default 下 cli_native（exec、无 consent）仍 ALLOW。"""
        self.assertEqual(get_gate_mode(), GATE_MODE_DEFAULT)
        spec = TrustGate.default_for_mechanism("cli_native")
        res = TrustGate.check(spec)
        self.assertEqual(res.decision, ALLOW)
        self.assertEqual(res.rule, "default_allow")

    def test_strict_mode_elevates_exec_to_ask_user(self):
        set_gate_mode(GATE_MODE_STRICT)
        self.assertTrue(is_strict())
        spec = TrustGate.default_for_mechanism("cli_native")
        res = TrustGate.check(spec)
        self.assertEqual(res.decision, ASK_USER)
        self.assertEqual(res.rule, "strict_exec_consent")
        stats = get_gate_stats()
        self.assertGreaterEqual(stats["rules"].get("strict_exec_consent", 0), 1)

    def test_strict_mode_undeclared_still_deny(self):
        set_gate_mode(GATE_MODE_STRICT)
        res = TrustGate.check(None)
        self.assertEqual(res.decision, DENY)
        self.assertEqual(res.rule, "undeclared_deny")

    def test_strict_mode_read_only_fs_allows(self):
        set_gate_mode(GATE_MODE_STRICT)
        spec = PermissionSpec(reads_fs=True, writes_fs=True, exec_external=False, network=False)
        res = TrustGate.check(spec)
        self.assertEqual(res.decision, ALLOW)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            set_gate_mode("failclosed")
        self.assertEqual(get_gate_mode(), GATE_MODE_DEFAULT)

    def test_settings_toggle_syncs_gate_mode(self):
        from vermes_cli.credential_lifecycle import (
            is_trust_gate_strict,
            set_trust_gate_strict,
        )
        set_trust_gate_strict(True)
        self.assertTrue(is_trust_gate_strict())
        self.assertEqual(get_gate_mode(), GATE_MODE_STRICT)
        set_trust_gate_strict(False)
        self.assertFalse(is_trust_gate_strict())
        self.assertEqual(get_gate_mode(), GATE_MODE_DEFAULT)


if __name__ == "__main__":
    unittest.main()
