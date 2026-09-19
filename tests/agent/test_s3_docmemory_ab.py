"""S3 — ⑮ 文档记忆腿 A/B：僵尸 anti_patterns 清理 + handoff 多域发射。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


def _root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "agent" / "evolution_injector.py").exists():
            return p
    return here.parents[2]


ROOT = _root()


class TestLegAZombieCleared(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.injector = (ROOT / "agent/evolution_injector.py").read_text(encoding="utf-8")
        cls.evo = (ROOT / "agent/evolution_manager.py").read_text(encoding="utf-8")
        cls.recall = (ROOT / "agent/memory_recall.py").read_text(encoding="utf-8")

    def test_injector_no_longer_selects_zombie_table(self):
        fn = self.injector[self.injector.index("def _load_anti_patterns"):]
        fn = fn[: fn.index("def _is_too_generic")]
        self.assertNotIn("FROM anti_patterns", fn)
        self.assertIn("return []", fn)

    def test_evolution_manager_no_sql_on_zombie(self):
        self.assertNotIn("FROM anti_patterns", self.evo)
        self.assertNotIn("COUNT(*) FROM anti_patterns", self.evo)
        self.assertIn("anti_patterns_count = 0", self.evo)

    def test_memory_recall_skips_zombie_edges(self):
        self.assertNotIn("FROM anti_patterns WHERE id", self.recall)
        self.assertIn("anti_pattern", self.recall)

    def test_load_anti_patterns_always_empty(self):
        import sqlite3
        from agent.evolution_injector import _load_anti_patterns

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # 故意建表也不再读
        conn.execute(
            "CREATE TABLE anti_patterns (id INTEGER, pattern TEXT, correct TEXT, domain TEXT, frequency INTEGER)"
        )
        conn.execute(
            "INSERT INTO anti_patterns VALUES (1, 'do-not-do-this-thing', 'do-that', 'code', 9)"
        )
        self.assertEqual(_load_anti_patterns(conn), [])


class TestLegBMultiDomainHandoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kanban = (ROOT / "tools/kanban_tools.py").read_text(encoding="utf-8")
        cls.chat = (ROOT / "vermes_cli/blueprints/chat.py").read_text(encoding="utf-8")
        # 注意：不可覆盖 TestCase.run（会变成字符串导致 unittest 崩溃）
        cls.run_agent_src = (ROOT / "run_agent.py").read_text(encoding="utf-8")
        cls.handoff = (ROOT / "agent/project_handoff.py").read_text(encoding="utf-8")

    def test_generic_entry_exists(self):
        self.assertIn("def record_generic_handoff", self.handoff)
        self.assertIn('domain="generic"', self.handoff)

    def test_cli_session_end_hooks_generic(self):
        self.assertIn("emit_generic_handoff_from_session", self.run_agent_src)

    def test_kanban_complete_emits_generic(self):
        self.assertIn("record_generic_handoff", self.kanban)
        self.assertIn("kanban_complete", self.kanban)

    def test_gui_delivery_emits_generic(self):
        self.assertIn("record_generic_handoff", self.chat)
        self.assertIn('task_key=f"gui:', self.chat)

    def test_generic_handoff_roundtrip_multi_task(self):
        import os
        import importlib
        from agent import project_handoff as ph
        importlib.reload(ph)

        tmp = Path(tempfile.mkdtemp(prefix="handoff-s3-"))
        old_home = os.environ.get("VERMES_HOME")
        os.environ["VERMES_HOME"] = str(tmp)
        try:
            self.assertTrue(ph.record_generic_handoff("kanban:t-1", title="蜂群任务", status="done"))
            self.assertTrue(ph.record_generic_handoff("gui:sess-9", title="会话交付", status="done"))
            self.assertTrue(ph.record_generic_handoff("docs:roadmap", title="文档路线", status="active"))
            # get_active_handoffs 过滤 status!='done' → 至少看到 docs:roadmap
            active = ph.get_active_handoffs(limit=20) or []
            self.assertGreaterEqual(len(active), 1)
            self.assertTrue(any(h.get("title") == "文档路线" for h in active))
        finally:
            if old_home is None:
                os.environ.pop("VERMES_HOME", None)
            else:
                os.environ["VERMES_HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
