"""E-P0-2 — harness fail-open 失败可观测（warning + 计数）。

契约：
1. `_harness_fail_log` 会打 `[harness-obs]` warning 并累加组件计数。
2. `get_harness_fail_counts()` 返回副本；`reset_harness_fail_counts()` 清空。
3. 真源接线：tool_executor 中 harness/进化/验证类 except 必须调用 `_harness_fail_log`
   或已是 warning 级日志；禁止对这些组件「仅 debug / 裸 pass」。
"""
from __future__ import annotations

import logging
import unittest
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "agent" / "tool_executor.py").exists():
            return p
    return here.parents[2]


TOOL_EXECUTOR = _repo_root() / "agent" / "tool_executor.py"


class TestHarnessFailLogUnit(unittest.TestCase):
    def setUp(self):
        import agent.tool_executor as te
        te.reset_harness_fail_counts()
        self.te = te

    def tearDown(self):
        self.te.reset_harness_fail_counts()

    def test_counts_and_warning(self):
        with self.assertLogs("agent.tool_executor", level=logging.DEBUG) as cm:
            self.te._harness_fail_log("circuit_breaker", RuntimeError("boom"), tool="web_search")
            self.te._harness_fail_log("circuit_breaker", RuntimeError("boom2"))
            self.te._harness_fail_log("self_validator", ValueError("x"))
        counts = self.te.get_harness_fail_counts()
        self.assertEqual(counts.get("circuit_breaker"), 2)
        self.assertEqual(counts.get("self_validator"), 1)
        joined = "\n".join(cm.output)
        self.assertIn("[harness-obs] circuit_breaker failed", joined)
        self.assertIn("tool=web_search", joined)
        self.assertIn("[harness-obs] self_validator failed", joined)
        # P2 降噪：同组件续报应是 debug 级，不再刷 warning
        warn_lines = [l for l in cm.output if "circuit_breaker" in l and "WARNING" in l]
        self.assertEqual(len(warn_lines), 1, "circuit_breaker 仅首报 WARNING，续报应 DEBUG")

    def test_reset(self):
        self.te._harness_fail_log("a", Exception("1"))
        self.te.reset_harness_fail_counts()
        self.assertEqual(self.te.get_harness_fail_counts(), {})

    def test_counts_are_copy(self):
        self.te._harness_fail_log("z", Exception("1"))
        snap = self.te.get_harness_fail_counts()
        snap["z"] = 99
        self.assertEqual(self.te.get_harness_fail_counts().get("z"), 1)


class TestToolExecutorWiring(unittest.TestCase):
    """源码契约：关键 harness 组件不再只 debug/pass。"""

    HARNESS_COMPONENTS = (
        "circuit_breaker",
        "tool_precheck",
        "failure_learning.record",
        "evolution.record_tool_outcome",
        "self_validator",
        "result_validator",
        "outcome_verifier.verify",
        "verified_signal.record",
        "precision_matrix",
        "stability_hotpath",
    )

    @classmethod
    def setUpClass(cls):
        cls.src = TOOL_EXECUTOR.read_text(encoding="utf-8")

    def test_harness_fail_log_defined(self):
        self.assertIn("def _harness_fail_log", self.src)
        self.assertIn("def get_harness_fail_counts", self.src)
        self.assertIn("[harness-obs]", self.src)

    def test_key_components_use_harness_obs(self):
        for comp in self.HARNESS_COMPONENTS:
            self.assertIn(
                f'_harness_fail_log("{comp}"',
                self.src,
                f"tool_executor 应对 {comp} 使用 _harness_fail_log",
            )

    def test_no_silent_pass_on_result_validator(self):
        self.assertNotIn("pass  # H3.1 永不阻塞", self.src)
        self.assertNotIn("pass  # H4.1 注入永不阻塞", self.src)


if __name__ == "__main__":
    unittest.main()
