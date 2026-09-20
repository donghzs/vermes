"""W-L4 / W-L5 / M7 token 阈值 — 真行为测试（董董 2026-09-20 批示清单）。"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestWL4EditingGuardrails(unittest.TestCase):
    def test_constant_has_three_disciplines(self):
        from agent.prompt_builder import EDITING_GUARDRAILS_GUIDANCE as g
        self.assertIn("path:line", g)
        self.assertIn("Do not refactor", g)
        self.assertIn("git commit", g)
        self.assertIn("git push", g)

    def test_processor_fallback_registered(self):
        from agent.prompt_builder import EDITING_GUARDRAILS_GUIDANCE
        import agent.system_prompt as sp
        self.assertIs(sp._PROCESSOR_FALLBACK.get("editing_guardrails"), EDITING_GUARDRAILS_GUIDANCE)

    def test_normal_git_commit_not_blocked_by_approval(self):
        """W-L4 提示层；硬闸后置 —— 普通 commit 不进 approval 危险清单。"""
        import tools.approval as ap
        src = Path(ap.__file__).read_text(encoding="utf-8")
        self.assertIn("git reset --hard", src)
        # 不应出现对普通 `git commit` 的拦截正则
        self.assertNotIn(r"r'\bgit\s+commit\b'", src)


class TestWL5WorkspaceBlock(unittest.TestCase):
    def test_git_repo_block_contains_root_branch_status(self):
        from agent.workspace_facts import build_workspace_block
        repo = Path(tempfile.mkdtemp(prefix="wl5-git-"))
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], capture_output=True)
        (repo / "README.md").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], capture_output=True)
        (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (repo / "dirty.txt").write_text("d", encoding="utf-8")
        block = build_workspace_block(repo, platform="cli")
        self.assertIn("Workspace", block)
        self.assertIn("Root:", block)
        self.assertIn("Branch:", block)
        self.assertIn("Status:", block)
        self.assertNotIn("Status: clean", block)  # dirty.txt
        self.assertIn("pyproject.toml", block)

    def test_non_git_dir_empty(self):
        from agent.workspace_facts import build_workspace_block
        plain = Path(tempfile.mkdtemp(prefix="wl5-plain-"))
        self.assertEqual(build_workspace_block(plain, platform="cli"), "")

    def test_messaging_without_terminal_cwd_skips(self):
        from agent.workspace_facts import build_workspace_block
        os.environ.pop("TERMINAL_CWD", None)
        repo = Path(tempfile.mkdtemp(prefix="wl5-msg-"))
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        # platform=messaging + 无 TERMINAL_CWD → SKIP（即使进程 cwd 也可能是安装目录）
        self.assertEqual(build_workspace_block(None, platform="telegram"), "")

    def test_terminal_cwd_wins_even_on_messaging(self):
        """IM 渠道 + TERMINAL_CWD 指向 git 区 → 注入（判据=git 区，不是渠道）。"""
        from agent.workspace_facts import build_workspace_block
        repo = Path(tempfile.mkdtemp(prefix="wl5-tc-"))
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        old = os.environ.get("TERMINAL_CWD")
        os.environ["TERMINAL_CWD"] = str(repo)
        try:
            block = build_workspace_block(None, platform="telegram")
            self.assertIn("Root:", block)
        finally:
            if old is None:
                os.environ.pop("TERMINAL_CWD", None)
            else:
                os.environ["TERMINAL_CWD"] = old

    def test_prompt_builder_wrapper_failopen(self):
        from agent.prompt_builder import build_workspace_block
        self.assertEqual(build_workspace_block("/no/such/dir", platform="cli"), "")


class TestM7TokenThreshold(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="m7-th-"))
        os.environ["VERMES_HOME"] = str(self.home)
        import vermes_cli.config as cfg_mod
        self.cfg_mod = cfg_mod
        self._real = cfg_mod.load_config
        for k in list(os.environ):
            if k.endswith("_HOME_CHANNEL"):
                os.environ.pop(k, None)

    def tearDown(self):
        self.cfg_mod.load_config = self._real

    def _set_agent(self, **kw):
        cfg_mod = self.cfg_mod
        payload = {"agent": {"compact_skill_categories": "off", **kw}}
        cfg_mod.load_config = lambda *a, **k: payload

    def test_off_never_demotes(self):
        from agent.prompt_builder import resolve_compact_skill_categories
        self._set_agent(compact_skill_categories="off")
        self.assertIsNone(resolve_compact_skill_categories(platform="cli"))

    def test_auto_under_threshold_no_demote(self):
        from agent.prompt_builder import resolve_compact_skill_categories
        self._set_agent(
            compact_skill_categories="auto",
            skill_index_compact_threshold_bytes=10**9,
        )
        self.assertIsNone(resolve_compact_skill_categories(platform="cli"))
        # 与渠道无关：阈值未超 → IM 也不降级
        self.assertIsNone(resolve_compact_skill_categories(platform="telegram"))

    def test_auto_over_threshold_demotes_even_on_im(self):
        from agent import prompt_builder as pb
        self._set_agent(
            compact_skill_categories="auto",
            skill_index_compact_threshold_bytes=1,
        )
        real_est = pb.estimate_skills_index_bytes
        pb.estimate_skills_index_bytes = lambda: 99999
        try:
            cats = pb.resolve_compact_skill_categories(platform="telegram")
        finally:
            pb.estimate_skills_index_bytes = real_est
        self.assertIsNotNone(cats)
        self.assertIn("creative", cats)

    def test_on_always_demotes(self):
        from agent.prompt_builder import resolve_compact_skill_categories
        self._set_agent(compact_skill_categories="on", skill_index_compact_threshold_bytes=10**9)
        self.assertIsNotNone(resolve_compact_skill_categories(platform="telegram"))

    def test_default_config_threshold_present(self):
        from vermes_cli.config import DEFAULT_CONFIG
        self.assertIn("skill_index_compact_threshold_bytes", DEFAULT_CONFIG["agent"])
        self.assertEqual(DEFAULT_CONFIG["agent"]["compact_skill_categories"], "off")


class TestSettingsCopy(unittest.TestCase):
    def test_auto_copy_mentions_threshold_not_coding_dir(self):
        src = (Path(__file__).resolve().parents[2] /
               "frontend/src/components/Settings.vue").read_text(encoding="utf-8")
        self.assertIn("阈值", src)
        self.assertIn("skill_index_compact_threshold", src.replace(" ", "") or src)  # optional
        self.assertIn("compact_skill_categories", src)


if __name__ == "__main__":
    unittest.main()
