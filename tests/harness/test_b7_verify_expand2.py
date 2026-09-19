"""B7 — verify_fn 再扩容：kanban_complete/block + scholarforge snapshot create。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path


def _root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "tools" / "kanban_tools.py").exists():
            return p
    return here.parents[2]


ROOT = _root()


class TestVerifyExpansion2Wiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kanban = (ROOT / "tools/kanban_tools.py").read_text(encoding="utf-8")
        cls.sf = (ROOT / "vermes_cli/scholarforge/tools.py").read_text(encoding="utf-8")

    def test_kanban_lifecycle_verify_registered(self):
        self.assertIn("def _verify_kanban_complete", self.kanban)
        self.assertIn("def _verify_kanban_block", self.kanban)
        self.assertIn("verify_fn=_verify_kanban_complete", self.kanban)
        self.assertIn("verify_fn=_verify_kanban_block", self.kanban)
        self.assertIn("verify_fn=_verify_kanban_create", self.kanban)

    def test_snapshot_create_verify_registered(self):
        self.assertIn("def _verify_scholarforge_manage_snapshots", self.sf)
        self.assertIn("verify_fn=_verify_scholarforge_manage_snapshots", self.sf)

    def test_snapshot_verify_skips_non_create(self):
        src = (ROOT / "vermes_cli/scholarforge/tools.py").read_text(encoding="utf-8")
        start = src.index("def _verify_scholarforge_manage_snapshots")
        end = src.index("\n    registry.register(", start)
        ns: dict = {}
        exec(src[start:end], ns)
        vf = ns["_verify_scholarforge_manage_snapshots"]
        ok, reason = vf("scholarforge_manage_snapshots", {"action": "list"}, "暂无快照", False)
        self.assertTrue(ok)
        self.assertIn("skip", reason)
        ok_err, _ = vf("scholarforge_manage_snapshots", {"action": "create"}, "❌ 创建快照失败", False)
        self.assertFalse(ok_err)

    def test_kanban_complete_rejects_error_text(self):
        src = (ROOT / "tools/kanban_tools.py").read_text(encoding="utf-8")
        start = src.index("def _verify_kanban_lifecycle")
        end = src.index("\nregistry.register(", start)
        ns = {"json": json, "os": __import__("os")}
        exec(src[start:end], ns)
        vf = ns["_verify_kanban_complete"]
        ok, reason = vf("kanban_complete", {}, "❌ could not complete", False)
        self.assertFalse(ok)
        self.assertIn("error", reason.lower())


class TestVerifyCountFloor(unittest.TestCase):
    def test_at_least_eight_registered_verify_fns(self):
        import re
        texts = [
            (ROOT / "tools/file_tools.py").read_text(encoding="utf-8"),
            (ROOT / "tools/kanban_tools.py").read_text(encoding="utf-8"),
            (ROOT / "vermes_cli/scholarforge/tools.py").read_text(encoding="utf-8"),
        ]
        count = 0
        for t in texts:
            count += len(re.findall(r"verify_fn=_verify_", t))
        self.assertGreaterEqual(count, 8, f"verify_fn registrations={count}")


if __name__ == "__main__":
    unittest.main()
