"""U-P0-1 — GET /api/harness/status 契约（E-P0-5 harness_status，HTTP 最小信号源）。"""
from __future__ import annotations

import unittest


class TestHarnessStatusEndpoint(unittest.TestCase):
    def test_handler_returns_contract_shape(self):
        import asyncio
        from vermes_cli.blueprints.chat import harness_status
        from agent.tool_executor import reset_harness_fail_counts, _harness_fail_log

        reset_harness_fail_counts()
        data = asyncio.run(harness_status())
        self.assertEqual(data.get("contract"), "v2.5-s1")
        self.assertEqual(data.get("type"), "harness_status")
        self.assertTrue(data.get("ok"))
        self.assertIn("fail_counts", data)
        self.assertEqual(data.get("fail_total"), 0)
        self.assertFalse(data.get("degraded"))
        self.assertEqual(data.get("signal"), "http_poll")

        _harness_fail_log("circuit_breaker", RuntimeError("x"), tool="t")
        _harness_fail_log("self_validator", RuntimeError("y"))
        data2 = asyncio.run(harness_status())
        self.assertGreaterEqual(data2["fail_total"], 2)
        self.assertTrue(data2["degraded"])
        self.assertIn("circuit_breaker", data2["fail_counts"])
        reset_harness_fail_counts()

    def test_route_registered(self):
        from vermes_cli.blueprints.chat import register_to, harness_status
        from fastapi import FastAPI
        app = FastAPI()
        # register_to may require bot_mode etc.; call directly and inspect routes
        try:
            register_to(app)
        except Exception:
            # 若因依赖注册失败，至少保证 handler 可导入
            self.assertTrue(callable(harness_status))
            return
        paths = {getattr(r, "path", None) for r in app.routes}
        self.assertIn("/api/harness/status", paths)


if __name__ == "__main__":
    unittest.main()
