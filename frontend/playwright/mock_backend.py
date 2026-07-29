"""Minimal mock backend for the cross-restart e2e.

Reuses the REAL ``agent.session_plan_store`` module so the e2e genuinely exercises
``load_plan_state`` — the cross-restart recovery path closed by boundary #4 — without
needing the full Flask app or an LLM.

This is NOT production code; it exists only so the browser e2e can verify that a plan
persisted to SQLite by one process (the seed script) is recoverable by another process
(the mock backend) after a simulated restart, via the exact production persistence module.

Endpoints:
    GET  /api/session/<sid>/plan_snapshot  -> load_plan_state(sid)  (REAL module)
    POST /api/chat/completions             -> 200 {}  (frontend aborts this itself)

Usage:
    VERMES_HOME=/tmp/vermes-e2e python3 frontend/playwright/mock_backend.py [port]
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent.session_plan_store import load_plan_state  # noqa: E402

# Session id seeded by seed_session_plan.py. The frontend uses its own opaque id,
# so this mock serves the seeded plan for any request sid — the e2e only verifies the
# SQLite recovery *read* path through the real module (per-session keying is unit-tested).
SESSION_ID = "sess-cross-restart"


def _snapshot_payload() -> dict:
    state = load_plan_state(SESSION_ID)
    if state is None:
        return {"ok": True, "session_id": SESSION_ID, "plan": None, "todo_states": {}, "plan_emitted": False}
    return {
        "ok": True,
        "session_id": SESSION_ID,
        "plan": state.get("plan"),
        "todo_states": state.get("todo_states", {}),
        "plan_emitted": state.get("plan_emitted", False),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if "/api/session/" in self.path and self.path.endswith("/plan_snapshot"):
            self._send(200, _snapshot_payload())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        # chat/completions is aborted by the frontend itself in the test; return 200.
        self._send(200, {})

    def log_message(self, *args) -> None:  # quiet
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[mock_backend] listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
