"""C2 — ⑨ 公开写端点鉴权收紧（真行为 + 契约 + claim 审计）。"""
from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"


class TestPathPublicForMethod(unittest.TestCase):
    def test_get_public_config_ok(self):
        from vermes_cli.web_server import _path_public_for_method
        self.assertTrue(_path_public_for_method("/api/status", "GET"))
        self.assertTrue(_path_public_for_method("/api/config", "GET"))
        self.assertTrue(_path_public_for_method("/api/providers/templates", "GET"))

    def test_sensitive_writes_require_token(self):
        """C 方案目标：公开读 ≠ 公开写；敏感写必须 token。"""
        from vermes_cli.web_server import _path_public_for_method
        cases = [
            ("/api/config", "PUT"),
            ("/api/config", "PATCH"),
            ("/api/config/raw", "PUT"),
            ("/api/provider/add", "POST"),
            ("/api/provider/verify", "POST"),
            ("/api/provider/sync-models", "POST"),
            ("/api/model/set", "POST"),
            ("/api/model/discover", "POST"),
            ("/api/update/download", "POST"),
            ("/api/update/apply", "POST"),
            ("/api/update/rollback", "POST"),
            ("/api/agent/update", "POST"),
            ("/api/v1/trust-gate", "PUT"),
            ("/api/agents/register-profile", "POST"),
            ("/api/dashboard/plugins/rescan", "POST"),
            ("/api/emergence/skill", "POST"),
            ("/api/evolution/self_modify_rollback", "POST"),
            ("/api/studio/providers", "POST"),
            ("/api/studio/providers/foo", "DELETE"),
        ]
        for path, method in cases:
            with self.subTest(path=path, method=method):
                self.assertFalse(_path_public_for_method(path, method))

    def test_write_allow_exempt(self):
        from vermes_cli.web_server import _path_public_for_method
        allow = [
            ("/api/claim", "POST"),
            ("/api/wechat/qrurl", "POST"),
            ("/api/wechat/poll", "GET"),
            ("/api/chat/completions", "POST"),
            ("/api/agent/run", "POST"),
            ("/api/gui/messages", "POST"),
            ("/api/gui/messages/s1", "DELETE"),
            ("/api/gui/sessions", "POST"),
            ("/api/sessions", "POST"),
            ("/api/sessions/batch", "DELETE"),
            ("/api/invoke", "POST"),
            ("/api/model-change", "POST"),
            ("/api/stop-generation", "POST"),
            ("/api/steer", "POST"),
            ("/api/mfgcad/upload", "POST"),
            ("/api/v1/artifacts/x/revert", "POST"),
            ("/api/tools/invoke", "POST"),
            ("/api/scholar/projects", "POST"),
        ]
        for path, method in allow:
            with self.subTest(path=path, method=method):
                self.assertTrue(_path_public_for_method(path, method))

    def test_non_public_read_still_denied(self):
        from vermes_cli.web_server import _path_public_for_method
        self.assertFalse(_path_public_for_method("/api/env", "GET"))
        self.assertFalse(_path_public_for_method("/api/credentials/health", "GET"))

    def test_non_public_write_denied(self):
        from vermes_cli.web_server import _path_public_for_method
        self.assertFalse(_path_public_for_method("/api/env", "PUT"))
        self.assertFalse(_path_public_for_method("/api/approve", "POST"))


class TestClaimAudit(unittest.TestCase):
    """/api/claim 保留公开的审计结论必须可核验。"""

    def test_claim_handler_does_not_mutate_local_secrets(self):
        import vermes_cli.blueprints.quota as quota
        src = inspect.getsource(quota.claim_trial_token)
        # 当前实现只回传 Agnes 公开元数据，不写 env / 不调 vbit 发 token
        self.assertNotIn("write_env", src)
        self.assertNotIn("save_env", src)
        self.assertIn("Agnes", src)
        self.assertIn("agnes", src.lower())

    def test_claim_present_in_write_allow_with_audit_comment(self):
        from vermes_cli.web_server import _PUBLIC_WRITE_ALLOW, _SESSION_HEADER_NAME  # noqa: F401
        self.assertIn("/api/claim", _PUBLIC_WRITE_ALLOW)
        src = (ROOT / "vermes_cli" / "web_server.py").read_text(encoding="utf-8")
        self.assertIn("claim 审计结论", src)


class TestMiddlewareUsesMethodAwareHelper(unittest.TestCase):
    def test_auth_middleware_calls_path_public_for_method(self):
        from vermes_cli.web_server import auth_middleware
        src = inspect.getsource(auth_middleware)
        self.assertIn("_path_public_for_method", src)
        self.assertIn("request.method", src)


class TestFrontendWriteTokens(unittest.TestCase):
    """敏感写调用点必须注入 session token；禁止残留覆盖式双 headers。"""

    def _read(self, rel: str) -> str:
        return (FRONTEND / rel).read_text(encoding="utf-8")

    def test_env_helper_exports_with_session_token(self):
        src = self._read("utils/env.js")
        self.assertIn("export function withSessionToken", src)
        self.assertIn("X-Vermes-Session-Token", src)

    def test_settings_sensitive_writes_carry_token(self):
        ui = self._read("components/Settings.vue")
        for needle in (
            "withSessionToken",
            "/api/provider/add",
            "/api/provider/sync-models",
            "/api/model/set",
            "stability_probe",
        ):
            self.assertIn(needle, ui)
        # cron monitor 走 PUT（与 blueprints/cron_jobs.py 对齐），不是误改成的 POST
        self.assertIn("toggleCronMonitor", ui)
        monitor_src_start = ui.index("async function toggleCronMonitor")
        monitor_src = ui[monitor_src_start: monitor_src_start + 800]
        self.assertIn("method: 'PUT'", monitor_src)
        self.assertIn("withSessionToken", monitor_src)

    def test_update_store_writes_carry_token(self):
        src = self._read("stores/update.js")
        self.assertIn("import { withSessionToken }", src)
        for path in ("/api/update/download", "/api/update/apply", "/api/update/rollback", "/api/agent/update"):
            self.assertIn(path, src)
        # 修复历史事故：插入 token 后仍残留旧 headers 会把 token 盖掉
        self.assertNotIn("headers: withSessionToken({ 'Content-Type': 'application/json' }),\n        method: 'POST',\n        headers: { 'Content-Type': 'application/json' }", src)

    def test_scholar_store_imports_ref_and_tokens(self):
        src = self._read("stores/scholar.js")
        self.assertIn("import { ref } from 'vue'", src)
        self.assertIn("import { withSessionToken }", src)
        self.assertNotIn("method: 'POST',\n      headers: withSessionToken({ 'Content-Type': 'application/json' }),\n      method: 'POST',", src)

    def test_evolution_panel_fetch_helper_is_single_function(self):
        src = self._read("components/EvolutionPanel.vue")
        self.assertIn("function _fetchWithTimeout", src)
        self.assertIn("withSessionToken", src)
        # 禁止函数体被截断后在顶层残留 AbortController
        helper = src[src.index("function _fetchWithTimeout"):]
        helper = helper[: helper.index("function _fail")]
        self.assertNotIn("return fetch(url, opts)\n}\n  const ctrl", helper)
        self.assertIn("AbortController", helper)
        self.assertIn("ctrl.signal", helper)

    def test_studio_provider_writes_carry_token(self):
        src = self._read("components/StudioChat.vue")
        self.assertIn("import { withSessionToken }", src)
        self.assertIn("withSessionToken({ 'Content-Type': 'application/json' })", src)
        self.assertIn("method: 'DELETE',\n      headers: withSessionToken(),", src)

    def test_api_build_headers_falls_back_to_window_token(self):
        src = self._read("services/api.js")
        self.assertIn("window.__VERMES_SESSION_TOKEN__", src)
        self.assertIn("withSessionToken", src)

    def test_no_duplicate_method_headers_pattern_in_key_files(self):
        """JS 对象字面量后键覆盖前键——双 method/headers 会让 token 注入失效。"""
        pattern = "headers: withSessionToken"
        offenders = []
        for rel in (
            "stores/chat.js",
            "stores/scholar.js",
            "stores/update.js",
            "components/Settings.vue",
        ):
            text = self._read(rel)
            # 粗检：withSessionToken 之后 80 字符内又出现裸 headers: { 'Content-Type'
            idx = 0
            while True:
                i = text.find(pattern, idx)
                if i < 0:
                    break
                window = text[i: i + 160]
                if "headers: { 'Content-Type'" in window and "withSessionToken" not in window.split("headers: {")[0]:
                    # 第二次 headers 无 withSessionToken 包裹
                    after = window[window.index("headers: {"):]
                    if "withSessionToken" not in after[:80]:
                        offenders.append(rel)
                        break
                idx = i + 1
        self.assertEqual(offenders, [])

    def test_frontend_js_parses(self):
        """关键 .js 文件必须能被 Node 解析（比括号计数可靠）。"""
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            for cand in (
                Path.home() / ".local/bin/node",
                Path.home() / ".volta/bin/node",
                Path("/opt/homebrew/bin/node"),
                Path("/usr/local/bin/node"),
            ):
                if cand.exists():
                    node = str(cand)
                    break
        if not node:
            self.skipTest("node not found")
        for rel in (
            "stores/chat.js",
            "stores/scholar.js",
            "stores/update.js",
            "utils/env.js",
            "services/api.js",
            "utils/invokeTool.js",
            "services/invoke.js",
        ):
            path = FRONTEND / rel
            proc = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"{rel} parse failed: {proc.stderr}")


if __name__ == "__main__":
    unittest.main()
