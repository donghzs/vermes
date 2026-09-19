"""S2 — A2A http/mcp/subprocess transport 真接线契约。"""
from __future__ import annotations

import unittest
from pathlib import Path


def _root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "agent" / "a2a" / "transports_http.py").exists():
            return p
    return here.parents[2]


ROOT = _root()


class TestTransportNoLongerStub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = (ROOT / "agent/a2a/transports_http.py").read_text(encoding="utf-8")
        cls.mcp = (ROOT / "agent/a2a/transports_mcp.py").read_text(encoding="utf-8")
        cls.sub = (ROOT / "agent/a2a/transports_subprocess.py").read_text(encoding="utf-8")

    def test_http_uses_httpx_and_endpoint_resolution(self):
        self.assertIn("httpx.post", self.http)
        self.assertIn("payload.endpoint", self.http)
        self.assertIn("transport_ref", self.http)
        self.assertIn("no HTTP endpoint", self.http)

    def test_mcp_declares_error_when_call_missing(self):
        self.assertIn("call_tool_by_name", self.mcp)
        self.assertIn("mcp target incomplete", self.mcp)
        self.assertNotIn('"kind": envelope.kind,\n            "to":', self.mcp)

    def test_subprocess_spawns_codex_client(self):
        self.assertIn("CodexAppServerClient", self.sub)
        self.assertIn("initialize", self.sub)
        self.assertIn("thread/start", self.sub)
        self.assertIn("turn/start", self.sub)
        self.assertIn("no spawn plan", self.sub)


class TestHttpTransportBehavior(unittest.TestCase):
    def test_missing_endpoint_returns_error_not_fake_success(self):
        from agent.a2a.transports_http import HttpTransport
        from agent.a2a.types import A2AEnvelope, MessageKind

        env = A2AEnvelope(from_handle="@a", to_handle="@unknown-agent", kind=MessageKind.MESSAGE.value, payload={"text": "hi"})
        out = HttpTransport().send(env)
        self.assertIn("error", out)
        self.assertNotIn("ok", out)

    def test_non_dispatchable_kind_passthrough(self):
        from agent.a2a.transports_http import HttpTransport
        from agent.a2a.types import A2AEnvelope, MessageKind

        env = A2AEnvelope(from_handle="@a", to_handle="@b", kind=MessageKind.RESULT.value)
        out = HttpTransport().send(env)
        self.assertEqual(out["kind"], MessageKind.RESULT.value)

    def test_payload_endpoint_posts_via_httpx(self):
        import sys
        from agent.a2a.transports_http import HttpTransport
        from agent.a2a.types import A2AEnvelope, MessageKind

        class FakeResp:
            status_code = 200
            is_success = True
            text = '{"ok":true}'

            def json(self):
                return {"ok": True}

        class FakeHttpx:
            @staticmethod
            def post(url, json=None, timeout=None):
                return FakeResp()

        sys.modules["httpx"] = FakeHttpx  # type: ignore
        try:
            env = A2AEnvelope(
                from_handle="@a",
                to_handle="@b",
                kind=MessageKind.MESSAGE.value,
                payload={"endpoint": "https://example.com/a2a", "text": "hi"},
            )
            out = HttpTransport().send(env)
            self.assertTrue(out["ok"])
            self.assertEqual(out["status"], 200)
        finally:
            sys.modules.pop("httpx", None)


class TestSubprocessTransportBehavior(unittest.TestCase):
    def test_no_spawn_plan_errors(self):
        from agent.a2a.transports_subprocess import SubprocessTransport
        from agent.a2a.types import A2AEnvelope, MessageKind

        env = A2AEnvelope(from_handle="@a", to_handle="@nope", kind=MessageKind.MESSAGE.value, payload={"text": "hi"})
        out = SubprocessTransport().send(env)
        self.assertIn("error", out)

    def test_spawn_bin_dispatches_via_mock_codex_client(self):
        import sys
        import types
        from agent.a2a.transports_subprocess import SubprocessTransport
        from agent.a2a.types import A2AEnvelope, MessageKind

        calls = []

        class FakeClient:
            def __init__(self, codex_bin="codex", extra_args=None):
                calls.append(("init_ctor", codex_bin, extra_args))

            def initialize(self, client_name="Vermes"):
                calls.append(("initialize", client_name))
                return {"ok": True}

            def request(self, method, params, timeout=10.0):
                calls.append((method, params))
                if method == "thread/start":
                    return {"threadId": "t1"}
                return {"result": {"text": "done"}}

            def close(self):
                calls.append(("close",))

        fake_mod = types.ModuleType("agent.transports.codex_app_server")
        fake_mod.CodexAppServerClient = FakeClient
        sys.modules["agent.transports.codex_app_server"] = fake_mod
        try:
            env = A2AEnvelope(
                from_handle="@a",
                to_handle="@codex",
                kind=MessageKind.TASK.value,
                payload={"text": "do it", "spawn_bin": "codex"},
            )
            out = SubprocessTransport().send(env)
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(out.get("result"), "done")
            self.assertTrue(any(c[0] == "thread/start" for c in calls))
            self.assertTrue(any(c[0] == "turn/start" for c in calls))
        finally:
            sys.modules.pop("agent.transports.codex_app_server", None)


class TestMcpTransportBehavior(unittest.TestCase):
    def test_incomplete_target_errors(self):
        from agent.a2a.transports_mcp import McpTransport
        from agent.a2a.types import A2AEnvelope, MessageKind

        env = A2AEnvelope(from_handle="@a", to_handle="@unknown", kind=MessageKind.TOOL.value, payload={"text": "x"})
        out = McpTransport().send(env)
        self.assertIn("error", out)

    def test_server_tool_dispatch_via_entrypoint(self):
        import sys
        import types
        from agent.a2a.transports_mcp import McpTransport
        from agent.a2a.types import A2AEnvelope, MessageKind

        fake = types.ModuleType("tools.mcp_tool")
        fake.call_tool_by_name = lambda server, tool, arguments: {"server": server, "tool": tool, "args": arguments}
        sys.modules["tools.mcp_tool"] = fake
        try:
            env = A2AEnvelope(
                from_handle="@a",
                to_handle="@openclaw",
                kind=MessageKind.TOOL.value,
                payload={"server": "openclaw", "tool": "echo", "arguments": {"q": "hi"}},
            )
            out = McpTransport().send(env)
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(out["result"]["tool"], "echo")
        finally:
            sys.modules.pop("tools.mcp_tool", None)


if __name__ == "__main__":
    unittest.main()
