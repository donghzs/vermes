"""E-P0-1 — tool_executor 各路径 invoke_with_retry 必须传入 circuit max_attempts。

背景（2026-09-18 真源）：
  agent/tool_executor.py 有三条执行路径都调用 harness.recoverable.invoke_with_retry：
    · 并发 worker（~L413）    —— 传 max_attempts=_max_att
    · spinner 顺序（~L1140）  —— 传 max_attempts=_max_att
    · 无 spinner 顺序（~L1201）—— 修复前算出 _max_att 却未传入，
      落到 invoke_with_retry 默认 max_attempts=2，熔断「skip_retry」在该路径失效。

本文件用 **源码 AST 接线契约** 锁住「凡 invoke_with_retry 必带 max_attempts」，
再用 **调用参数契约** 锁住「_max_att 会进入 invoke_with_retry」。
反向验证：去掉任一处 max_attempts= 关键字，本文件必须失败。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "agent" / "tool_executor.py").exists():
            return p
    return here.parents[2]


TOOL_EXECUTOR = _repo_root() / "agent" / "tool_executor.py"


def _invoke_with_retry_calls(tree: ast.AST) -> list[ast.Call]:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name == "invoke_with_retry":
            out.append(node)
    return out


class TestInvokeWithRetryWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = TOOL_EXECUTOR.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.src)
        cls.calls = _invoke_with_retry_calls(cls.tree)

    def test_tool_executor_defines_multiple_retry_paths(self):
        """真源至少 3 条路径；若只剩 1 条说明架构变了，需重审契约。"""
        self.assertGreaterEqual(
            len(self.calls), 3,
            f"tool_executor 应至少 3 处 invoke_with_retry，实际 {len(self.calls)}",
        )

    def test_every_invoke_with_retry_passes_max_attempts(self):
        """E-P0-1 接线契约：每个调用点都必须显式传 max_attempts。"""
        missing = []
        for i, call in enumerate(self.calls):
            kws = {k.arg for k in call.keywords}
            if "max_attempts" not in kws:
                missing.append((i, getattr(call, "lineno", "?"), kws))
        self.assertEqual(
            missing, [],
            f"存在未传 max_attempts 的 invoke_with_retry（路径熔断会失效）: {missing}",
        )

    def test_max_attempts_keyword_is_local_max_att(self):
        """关键字值应为 _max_att（与 circuit_breaker.max_attempts_for 接线）。"""
        bad = []
        for call in self.calls:
            for k in call.keywords:
                if k.arg != "max_attempts":
                    continue
                val = k.value
                name = getattr(val, "id", None)
                if name != "_max_att":
                    bad.append((getattr(call, "lineno", "?"), ast.dump(val)[:80]))
        self.assertEqual(bad, [], f"max_attempts 应绑定 _max_att: {bad}")

    def test_max_att_computed_on_each_retry_path(self):
        """每条路径附近都应有 _max_att = max_attempts_for(...) 赋值。"""
        n_assign = self.src.count("_max_att = max_attempts_for")
        n_calls = len(self.calls)
        self.assertGreaterEqual(
            n_assign, n_calls,
            f"_max_att 赋值次数 {n_assign} < invoke_with_retry 调用次数 {n_calls}",
        )


class TestCircuitMaxAttemptsSemantics(unittest.TestCase):
    """语义：invoke_with_retry 在熔断打开时应按 max_attempts=1 只调一次。"""

    def test_invoke_with_retry_honours_max_attempts(self):
        from harness.recoverable import invoke_with_retry

        calls = {"n": 0}

        def always_fail():
            calls["n"] += 1
            raise ConnectionError("net down")

        classify = lambda exc: ("network_error", "net down")  # noqa: E731
        with self.assertRaises(ConnectionError):
            invoke_with_retry(
                always_fail,
                "web_search",
                max_attempts=1,
                base_delay=0.001,
                classify=classify,
            )
        self.assertEqual(calls["n"], 1, "max_attempts=1 时不应重试")

        calls["n"] = 0
        with self.assertRaises(ConnectionError):
            invoke_with_retry(
                always_fail,
                "web_search",
                max_attempts=3,
                base_delay=0.001,
                classify=classify,
            )
        self.assertEqual(calls["n"], 3, "max_attempts=3 时应尝试 3 次")


if __name__ == "__main__":
    unittest.main()
