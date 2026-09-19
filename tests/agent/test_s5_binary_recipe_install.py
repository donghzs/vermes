"""S5 — binary recipe 下载解包契约测试。"""
from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path


class TestBinaryInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s5-acp-"))
        os.environ["VERMES_HOME"] = str(self.tmp)

    def tearDown(self):
        os.environ.pop("VERMES_HOME", None)

    def test_list_binary_recipes_includes_cursor_kimi(self):
        from vermes_cli.a2a.binary_install import list_binary_recipe_names
        names = list_binary_recipe_names()
        self.assertIn("cursor", names)
        self.assertIn("kimi", names)
        self.assertGreaterEqual(len(names), 10)

    def test_missing_recipe_errors_honestly(self):
        from vermes_cli.a2a.binary_install import ensure_binary_recipe_installed
        out = ensure_binary_recipe_installed("not-a-real-recipe-xyz")
        self.assertFalse(out["ok"])
        self.assertIn("not found", out["error"])

    def test_npx_recipe_without_url_errors(self):
        from vermes_cli.a2a.binary_install import ensure_binary_recipe_installed
        # copilot 是 npx，无 binaries.url 时应明确报错而非假装安装
        out = ensure_binary_recipe_installed("copilot")
        self.assertFalse(out["ok"])
        self.assertTrue("not binary" in out["error"] or "no download url" in out["error"] or "download" in out["error"].lower(), out)

    def test_download_unpack_local_zip(self):
        from vermes_cli.a2a.binary_install import ensure_binary_recipe_installed

        # 造一个假 zip，内含 entry_point 文件 ./dist-package/fake-agent
        bin_dir = self.tmp / "src"
        bin_dir.mkdir()
        payload = bin_dir / "dist-package"
        payload.mkdir()
        exe = payload / "fake-agent"
        exe.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
        zpath = self.tmp / "fake.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.write(exe, "dist-package/fake-agent")

        # 伪 recipe：monkeypatch _load_recipe_raw
        import vermes_cli.a2a.binary_install as bi
        orig = bi._load_recipe_raw
        bi._load_recipe_raw = lambda name: {
            "name": name,
            "entry_point": "./dist-package/fake-agent",
            "description": "binary stub",
        }
        try:
            out = ensure_binary_recipe_installed(
                "cursor",
                download_url=f"file://{zpath}",
            )
        finally:
            bi._load_recipe_raw = orig
        self.assertTrue(out["ok"], out)
        path = Path(out["path"])
        self.assertTrue(path.exists(), out)
        self.assertTrue(os.access(path, os.X_OK), out)

    def test_sha256_mismatch_rejects(self):
        import vermes_cli.a2a.binary_install as bi
        bin_dir = self.tmp / "src2"
        bin_dir.mkdir()
        exe = bin_dir / "b"
        exe.write_text("x", encoding="utf-8")
        zpath = self.tmp / "bad.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.write(exe, "b")
        orig = bi._load_recipe_raw
        bi._load_recipe_raw = lambda name: {
            "name": name,
            "entry_point": "./b",
            "description": "binary",
        }
        try:
            out = bi.ensure_binary_recipe_installed(
                "kimi",
                download_url=f"file://{zpath}",
                sha256="00" * 32,
            )
        finally:
            bi._load_recipe_raw = orig
        self.assertFalse(out["ok"])
        self.assertIn("sha256", out["error"])


if __name__ == "__main__":
    unittest.main()
