"""B1 verify_fn 扩容 — 写类工具外证接线（kanban_create + scholarforge 三写回）。"""
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


class TestVerifyFnExpansionWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kanban = (ROOT / "tools/kanban_tools.py").read_text(encoding="utf-8")
        cls.sf = (ROOT / "vermes_cli/scholarforge/tools.py").read_text(encoding="utf-8")

    def test_kanban_create_has_verify_fn(self):
        self.assertIn("def _verify_kanban_create", self.kanban)
        self.assertIn("verify_fn=_verify_kanban_create", self.kanban)
        self.assertIn("kanban.db", self.kanban)

    def test_scholarforge_writebacks_have_verify_fn(self):
        for needle in (
            "verify_fn=_verify_scholarforge_export",
            "verify_fn=_verify_scholarforge_save_cards",
            "verify_fn=_verify_scholarforge_set_active_project",
        ):
            self.assertIn(needle, self.sf, needle)
        # 现有 write/outline 仍保留
        self.assertIn("verify_fn=_verify_scholarforge_write", self.sf)
        self.assertIn("verify_fn=_verify_scholarforge_outline", self.sf)

    def test_export_verify_checks_disk_path(self):
        self.assertIn("export file exists", self.sf)
        self.assertIn("no export path in result", self.sf)

    def test_save_cards_reads_literature_table(self):
        self.assertIn("literature_cards", self.sf)
        self.assertIn("table is empty after save", self.sf)

    def test_set_active_reads_active_project(self):
        self.assertIn("get_active_project", self.sf)
        self.assertIn("active project ==", self.sf)


class TestVerifyFnBehaviors(unittest.TestCase):
    def test_kanban_create_rejects_error_text(self):
        src = (ROOT / "tools/kanban_tools.py").read_text(encoding="utf-8")
        # isolate _verify_kanban_create + _connect stub
        start = src.index("def _verify_kanban_create")
        end = src.index("registry.register", start)
        stub = '''
def _connect(board=None):
    class _KB:
        @staticmethod
        def get_task(conn, tid):
            return None if tid == "missing" else type("T", (), {"id": tid})()
    class _Conn:
        def close(self):
            pass
    return _KB, _Conn()
'''
        exec(src[start:end] + stub, ns)
        vf = ns["_verify_kanban_create"]
        ok, reason = vf("kanban_create", {}, json.dumps({"ok": True, "task_id": "missing"}), False)
        self.assertFalse(ok)
        self.assertIn("not found", reason)
        ok2, _ = vf("kanban_create", {}, json.dumps({"ok": True, "task_id": "t1"}), False)
        self.assertTrue(ok2)
        ok3, r3 = vf("kanban_create", {}, "❌ title is required", False)
        self.assertFalse(ok3)
        self.assertTrue(r3)

    def test_export_verify_missing_path(self):
        src = (ROOT / "vermes_cli/scholarforge/tools.py").read_text(encoding="utf-8")
        start = src.index("def _verify_scholarforge_export")
        end = src.index("def _verify_scholarforge_save_cards")
        ns: dict = {}
        exec(src[start:end], ns)
        vf = ns["_verify_scholarforge_export"]
        ok, reason = vf("scholarforge_export", {}, "✅ PDF 已导出", False)
        self.assertFalse(ok)
        self.assertIn("no export path", reason)
        ok_err, _ = vf("scholarforge_export", {}, "❌ 导出失败: boom", False)
        self.assertFalse(ok_err)


if __name__ == "__main__":
    unittest.main()
