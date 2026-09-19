"""S7 — 神魔堂 org → 蜂群沙箱：接线契约守卫（真源 + 行为）。

审计口径：org_engine 的 executor 是 LLM 岗位角色；**执行层**是否下沉
蜂群沙箱由 `_org_runner_factory` 三路 gating 决定。本文件钉死该接线，
防「以为进了沙箱其实仍内联」回潮。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


def _root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "vermes_cli" / "org_sandbox.py").exists():
            return p
    return here.parents[2]


ROOT = _root()


class TestOrgSandboxWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = (ROOT / "vermes_cli/blueprints/chat.py").read_text(encoding="utf-8")
        cls.engine = (ROOT / "vermes_cli/botmode/org_engine.py").read_text(encoding="utf-8")
        cls.sandbox = (ROOT / "vermes_cli/org_sandbox.py").read_text(encoding="utf-8")

    def test_factory_three_way_gating(self):
        self.assertIn("def _org_runner_factory", self.chat)
        self.assertIn("org_sandbox_enabled", self.chat)
        self.assertIn("run_org_subtask_in_sandbox", self.chat)
        # ACP 永不进沙箱
        self.assertIn('tr != "acp"', self.chat)

    def test_engine_passes_use_sandbox_flag(self):
        self.assertIn("use_sandbox", self.engine)
        self.assertIn('"use_sandbox": getattr(ctx, "use_sandbox", False)', self.engine)

    def test_room_optin_reads_use_sandbox_column(self):
        self.assertIn("use_sandbox=bool((room or {}).get(\"use_sandbox\"))", self.chat)

    def test_sandbox_rejects_acp_dispatch(self):
        os.environ["VERMES_HOME"] = tempfile.mkdtemp(prefix="s7-org-")
        from vermes_cli import kanban_db as kb
        from vermes_cli.org_sandbox import dispatch_org_subtask_to_sandbox
        kb.init_db()
        with kb.connect() as conn:
            with self.assertRaises(ValueError):
                dispatch_org_subtask_to_sandbox(
                    conn,
                    org_task_id="org-acp",
                    profile={"id": "p"},
                    instruction="x",
                    transport="acp",
                )

    def test_sandbox_disabled_by_default(self):
        from vermes_cli.org_sandbox import org_sandbox_enabled
        class Ctx:
            use_sandbox = False
        self.assertFalse(org_sandbox_enabled(Ctx()))
        class CtxOn:
            use_sandbox = True
        self.assertTrue(org_sandbox_enabled(CtxOn()))


if __name__ == "__main__":
    unittest.main()
