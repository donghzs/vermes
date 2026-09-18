"""E-P0-3 — write_file / patch 外证 verify_fn 契约。

真源：tools/file_tools.py 注册 verify_fn=_verify_file_write_outcome。
outcome_verifier 对无 verify_fn 的工具默认 (True, "")；本测试锁住写回类
必须挂上外证核验，且行为符合「磁盘存在 → ok / 缺失 → fail / 无 path → skip」。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest


class TestWriteFileVerifyFnRegistered(unittest.TestCase):
    def test_file_tools_registers_verify_fn_in_source(self):
        """源码契约：write_file / patch 注册必须带 verify_fn（防再漂移）。"""
        import inspect
        import tools.file_tools as ft
        src = inspect.getsource(ft)
        self.assertIn("verify_fn=_verify_file_write_outcome", src)
        self.assertGreaterEqual(src.count("verify_fn=_verify_file_write_outcome"), 2)

    def test_registry_entry_after_re_register(self):
        """运行时契约：用当前源码 re-register(override=True) 后 entry 带 verify_fn。

        说明：pytest 全局 registry 可能被其它夹具/插件先写入无 verify_fn 的旧
        entry；这里显式以 file_tools 源码中的注册参数覆盖，断言 ToolEntry 字段。
        """
        import tools.file_tools as ft
        from tools.registry import registry as reg
        reg.register(
            name="write_file",
            toolset="file",
            schema=ft.WRITE_FILE_SCHEMA,
            handler=ft._handle_write_file,
            check_fn=ft._check_file_reqs,
            verify_fn=ft._verify_file_write_outcome,
            emoji="✍️",
            max_result_size_chars=100_000,
            override=True,
        )
        reg.register(
            name="patch",
            toolset="file",
            schema=ft.PATCH_SCHEMA,
            handler=ft._handle_patch,
            check_fn=ft._check_file_reqs,
            verify_fn=ft._verify_file_write_outcome,
            emoji="🔧",
            max_result_size_chars=100_000,
            override=True,
        )
        for name in ("write_file", "patch"):
            entry = reg.get_entry(name)
            self.assertIsNotNone(entry, f"{name} not registered")
            vfn = getattr(entry, "verify_fn", None)
            self.assertIs(
                vfn,
                ft._verify_file_write_outcome,
                f"{name} verify_fn 未绑定 file_tools._verify_file_write_outcome",
            )


class TestWriteFileVerifyBehavior(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools.file_tools import _verify_file_write_outcome
        # 绑定为静态函数，避免 unittest 把 self 传入 verify 签名
        cls.verify = staticmethod(_verify_file_write_outcome)

    def test_file_exists_ok(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "a.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("hello")
            result = json.dumps({"bytes_written": 5})
            ok, reason = TestWriteFileVerifyBehavior.verify(
                "write_file", {"path": path, "content": "hello"}, result, False
            )
            self.assertTrue(ok, reason)
            self.assertIn("file exists", reason)

    def test_missing_path_on_disk_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "missing.txt")
            result = json.dumps({"bytes_written": 1})
            ok, reason = TestWriteFileVerifyBehavior.verify(
                "write_file", {"path": path, "content": "x"}, result, False
            )
            self.assertFalse(ok)
            self.assertIn("not found", reason)

    def test_json_error_result_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "b.txt")
            with open(path, "w") as f:
                f.write("x")
            result = json.dumps({"error": "permission denied"})
            ok, reason = TestWriteFileVerifyBehavior.verify(
                "write_file", {"path": path}, result, False
            )
            self.assertFalse(ok)

    def test_no_path_skips(self):
        ok, reason = TestWriteFileVerifyBehavior.verify("write_file", {}, "{}", False)
        self.assertTrue(ok)
        self.assertIn("skip", reason.lower())

    def test_is_error_false_when_file_absent(self):
        ok, reason = TestWriteFileVerifyBehavior.verify(
            "write_file", {"path": "/no/such/vermes-path-xyz"}, "{}", True
        )
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
