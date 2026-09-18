"""U-P0-5 — tool_step.harness 时间线契约（E-P0-5 v2.5-s1）。"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


def _repo_root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "vermes_cli" / "blueprints" / "chat.py").exists():
            return p
    return here.parents[2]


ROOT = _repo_root()


class TestToolStepHarnessSignal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executor = (ROOT / "agent/tool_executor.py").read_text(encoding="utf-8")
        cls.chat = (ROOT / "vermes_cli/blueprints/chat.py").read_text(encoding="utf-8")
        cls.chat_js = (ROOT / "frontend/src/stores/chat.js").read_text(encoding="utf-8")
        cls.msg_list = (ROOT / "frontend/src/components/MessageList.vue").read_text(encoding="utf-8")
        cls.drawer = (ROOT / "frontend/src/components/TaskDrawer.vue").read_text(encoding="utf-8")

    def test_executor_builds_harness_dict(self):
        self.assertIn("def _build_tool_harness_signal", self.executor)
        self.assertIn('"precheck"', self.executor)
        self.assertIn('"outcome"', self.executor)
        self.assertIn("unverified_tool", self.executor)
        self.assertIn("harness=_harness_out", self.executor)

    def test_executor_both_paths_attach_harness(self):
        compact = re.sub(r"\s+", "", self.executor)
        self.assertGreaterEqual(compact.count("harness=_harness_out"), 2)

    def test_chat_tool_end_forwards_contract_fields(self):
        self.assertIn('"contract": "v2.5-s1"', self.chat)
        self.assertIn("kwargs.get(\"harness\")", self.chat)
        self.assertIn('"harness": _harness', self.chat)
        self.assertIn('"duration_ms"', self.chat)
        self.assertIn('"phase"', self.chat)

    def test_frontend_store_consumes_harness(self):
        self.assertIn("harness: data.harness || null", self.chat_js)
        self.assertIn("phase: data.phase", self.chat_js)

    def test_messagelist_no_fake_green_without_signal(self):
        self.assertIn("function toolRunIcon", self.msg_list)
        self.assertIn("function harnessBadge", self.msg_list)
        self.assertIn("未独立核验", self.msg_list)
        self.assertIn("暂无信号", self.msg_list)
        # 无 harness 时不得一律 ✅
        self.assertIn("if (!h) return '●'", self.msg_list)

    def test_taskdrawer_respects_harness_outcome(self):
        self.assertIn("act.harness?.outcome === 'verified'", self.drawer)
        self.assertIn("act.harness?.outcome === 'unverified_tool'", self.drawer)

    def test_helper_maps_precheck_and_outcome(self):
        self.assertIn('h["precheck"] = "blocked"', self.executor)
        self.assertIn('h["outcome"] = "verified"', self.executor)
        self.assertIn('h["outcome"] = "verify_failed"', self.executor)


class TestHarnessSignalHelperUnit(unittest.TestCase):
    def test_helper_behavior_isolated(self):
        # 不 import agent 包（避免 yaml 等运行时依赖），直接 exec 源码片段
        src = (ROOT / "agent/tool_executor.py").read_text(encoding="utf-8")
        start = src.index("def _build_tool_harness_signal")
        end = src.index("def _build_tool_artifacts")
        ns: dict = {}
        exec(src[start:end], ns)  # noqa: S102 — 测试隔离加载纯函数
        f = ns["_build_tool_harness_signal"]

        class P:
            passed = True
            block = False
            warning = None

        class PW:
            passed = False
            block = False
            warning = "careful"

        class PB:
            passed = False
            block = True
            warning = "nope"

        h = f("read_file", precheck=P(), ov_ok=True, max_attempts=2)
        self.assertEqual(h["precheck"], "ok")
        self.assertIn(h["outcome"], {"verified", "unverified_tool", "unknown"})

        h2 = f("x", precheck=PB())
        self.assertEqual(h2["precheck"], "blocked")
        self.assertEqual(h2["outcome"], "unknown")

        h3 = f("x", precheck=PW(), ov_ok=False, ov_reason="disk missing")
        self.assertEqual(h3["precheck"], "warning")

        h4 = f("x", is_error=True, ov_ok=True)
        self.assertEqual(h4["outcome"], "unknown")

        h5 = f("x", ov_ok=True, ov_reason="verifier error: boom")
        self.assertEqual(h5["outcome"], "verifier_error")


if __name__ == "__main__":
    unittest.main()
