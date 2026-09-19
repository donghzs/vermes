"""S6 — 鉴权收紧：敏感端点不进 public 白名单 + session-token 仅回环 Host 可取。"""
from __future__ import annotations

import unittest
from pathlib import Path


def _root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "vermes_cli" / "web_server.py").exists():
            return p
    return here.parents[2]


ROOT = _root()


class TestAuthTightening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "vermes_cli" / "web_server.py").read_text(encoding="utf-8")

    def test_session_token_handler_requires_loopback(self):
        idx = self.src.index('"/api/session-token"')
        fn = self.src[idx:idx + 900]
        self.assertIn("_LOOPBACK_HOST_VALUES", fn)
        self.assertIn("loopback", fn.lower())
        self.assertIn("session_token_refresh", fn)

    def test_env_not_marked_public_uncommented(self):
        # /api/env 允许出现在 public 注释里（历史说明），但不能作为未注释的白名单项
        start = self.src.index("_PUBLIC_API_PATHS")
        end = self.src.index("def _has_valid_session_token", start)
        block = self.src[start:end]
        for line in block.splitlines():
            s = line.strip()
            if s.startswith("#"):
                continue
            if s.startswith('"/api/env"') or s.startswith('"/api/session-token"'):
                self.fail(f"sensitive path listed public: {s}")
            if s.startswith('"/api/credentials'):
                self.fail(f"credentials path listed public: {s}")

    def test_auth_middleware_401(self):
        self.assertIn("auth_middleware", self.src)
        self.assertIn('status_code=401', self.src)
        self.assertIn("_has_valid_session_token", self.src)
        self.assertIn("hmac.compare_digest", self.src)

    def test_session_token_refresh_callable_contract(self):
        import tempfile
        import os
        from types import SimpleNamespace
        os.environ["VERMES_HOME"] = tempfile.mkdtemp(prefix="s6-auth-")
        # 源码级：handler 签名收 request
        self.assertIn("async def session_token_refresh(request: Request)", self.src)


if __name__ == "__main__":
    unittest.main()
