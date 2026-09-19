"""B3 — harness_status SSE 推送 + HTTP 轮询兜底（契约 v2.5-s1）。"""
from __future__ import annotations

import unittest
from pathlib import Path


def _root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "agent" / "tool_executor.py").exists():
            return p
    return here.parents[2]


ROOT = _root()


class TestHarnessSseWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exec = (ROOT / "agent/tool_executor.py").read_text(encoding="utf-8")
        cls.chat = (ROOT / "vermes_cli/blueprints/chat.py").read_text(encoding="utf-8")
        cls.transport = (ROOT / "frontend/src/services/chat-transport.js").read_text(encoding="utf-8")
        cls.chat_js = (ROOT / "frontend/src/stores/chat.js").read_text(encoding="utf-8")
        cls.header = (ROOT / "frontend/src/components/ChatHeader.vue").read_text(encoding="utf-8")

    def test_executor_has_sse_hook(self):
        self.assertIn("def set_harness_status_hook", self.exec)
        self.assertIn("_emit_harness_status_sse", self.exec)
        self.assertIn('"signal": "sse"', self.exec)
        self.assertIn("set_harness_status_hook", self.exec)

    def test_chat_stream_registers_and_clears_hook(self):
        self.assertIn("set_harness_status_hook(_on_harness_status)", self.chat)
        self.assertIn("set_harness_status_hook(None)", self.chat)
        self.assertIn('"signal": "sse"', self.chat)

    def test_frontend_consumes_harness_status(self):
        self.assertIn("onHarnessStatus", self.transport)
        self.assertIn("data.type === 'harness_status'", self.transport)
        self.assertIn("currentHarnessStatus", self.chat_js)
        self.assertIn("onHarnessStatus:", self.chat_js)

    def test_header_prefers_sse_keeps_poll_fallback(self):
        self.assertIn("currentHarnessStatus", self.header)
        self.assertIn("signal === 'sse'", self.header)
        self.assertIn("useVisiblePoll(fetchHarnessStatus, 45000)", self.header)


class TestHarnessSseHookBehavior(unittest.TestCase):
    def test_fail_log_invokes_hook(self):
        src = (ROOT / "agent/tool_executor.py").read_text(encoding="utf-8")
        import logging
        import threading
        ns: dict = {
            "threading": threading,
            "logger": logging.getLogger("b3-test"),
            "_HARNESS_FAIL_COUNTS": {},
            "_HARNESS_FAIL_WARNED": set(),
            "_HARNESS_STATUS_HOOK": None,
        }
        chunk = src[src.index("_HARNESS_FAIL_WARNED"):src.index("def _budget_for_agent")]
        exec(chunk, ns)
        got = []
        ns["set_harness_status_hook"](got.append)
        ns["reset_harness_fail_counts"]()
        ns["_harness_fail_log"]("tool_precheck", RuntimeError("boom"), tool="read_file")
        self.assertTrue(got)
        evt = got[0]
        self.assertEqual(evt["type"], "harness_status")
        self.assertEqual(evt["signal"], "sse")
        self.assertTrue(evt["degraded"])
        self.assertGreaterEqual(evt["fail_total"], 1)
        ns["set_harness_status_hook"](None)
        got.clear()
        ns["_harness_fail_log"]("tool_precheck", RuntimeError("again"), tool="read_file")
        self.assertFalse(got)


if __name__ == "__main__":
    unittest.main()
