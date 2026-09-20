"""P1 — 技能索引 names-only 降级（真行为，非读源码自证）。

纪律：
* compact_categories=None → 输出含完整描述、无 [names only]（回归基线）
* compact 含 creative → 该类目折叠，**条目名仍保留**
* 配置默认 off / 非代码目录 auto → 不降级
* deny-list 含本地补充 daily/content-marketing/openclaw-imports
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


def _tmp_home() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="skills-p1-"))
    os.environ["VERMES_HOME"] = str(tmp)
    (tmp / "skills").mkdir(parents=True, exist_ok=True)
    return tmp


def _write_skill(skills_root: Path, category: str, name: str, desc: str) -> None:
    """技能类目来自**目录结构**（snapshot: parts[:-2]），不是 frontmatter category。"""
    d = skills_root / category / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\ncategory: {category}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


class TestIsCodingDir(unittest.TestCase):
    def test_markers_detect_code_workspace(self):
        from agent.prompt_builder import is_coding_dir
        tmp = Path(tempfile.mkdtemp(prefix="coding-yes-"))
        (tmp / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        self.assertTrue(is_coding_dir(tmp))
        empty = Path(tempfile.mkdtemp(prefix="coding-no-"))
        self.assertFalse(is_coding_dir(empty))

    def test_non_dir_false(self):
        from agent.prompt_builder import is_coding_dir
        self.assertFalse(is_coding_dir("/no/such/dir/vermes-p1"))


class TestResolveCompactGate(unittest.TestCase):
    def setUp(self):
        self.home = _tmp_home()
        self.code_dir = Path(tempfile.mkdtemp(prefix="p1-code-"))
        (self.code_dir / "package.json").write_text("{}", encoding="utf-8")
        self.plain_dir = Path(tempfile.mkdtemp(prefix="p1-plain-"))

    def _write_config(self, mode: str | None):
        import yaml
        cfg_path = self.home / "config.yaml"
        data = {"agent": {"compact_skill_categories": mode}} if mode is not None else {}
        cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    def test_default_missing_config_is_off(self):
        from agent.prompt_builder import resolve_compact_skill_categories
        self._write_config(None)
        self.assertIsNone(resolve_compact_skill_categories(self.code_dir, platform="cli"))

    def test_off_never_demotes(self):
        from agent.prompt_builder import resolve_compact_skill_categories
        self._write_config("off")
        self.assertIsNone(resolve_compact_skill_categories(self.code_dir, platform="cli"))

    def test_auto_token_threshold_not_coding_dir(self):
        """终局：auto = token 阈值，不再用 is_coding_dir 作门控。"""
        from agent import prompt_builder as pb
        import vermes_cli.config as cfg_mod
        real_load = cfg_mod.load_config
        real_est = pb.estimate_skills_index_bytes
        try:
            cfg_mod.load_config = lambda *a, **k: {
                "agent": {
                    "compact_skill_categories": "auto",
                    "skill_index_compact_threshold_bytes": 1,
                }
            }
            pb.estimate_skills_index_bytes = lambda: 99999
            cats = pb.resolve_compact_skill_categories(self.plain_dir)
            self.assertIsNotNone(cats)
            self.assertIn("creative", cats)
            cfg_mod.load_config = lambda *a, **k: {
                "agent": {
                    "compact_skill_categories": "auto",
                    "skill_index_compact_threshold_bytes": 10**9,
                }
            }
            self.assertIsNone(pb.resolve_compact_skill_categories(self.code_dir))
        finally:
            cfg_mod.load_config = real_load
            pb.estimate_skills_index_bytes = real_est

    def test_unknown_mode_fails_safe_off(self):
        from agent.prompt_builder import resolve_compact_skill_categories
        self._write_config("maybe")
        self.assertIsNone(resolve_compact_skill_categories(self.code_dir, platform="cli"))


class TestSkillsIndexRender(unittest.TestCase):
    def setUp(self):
        self.home = _tmp_home()
        self.skills = self.home / "skills"
        _write_skill(self.skills, "creative", "draw-skill", "画一张图（很长的描述）")
        # 嵌套类目 creative/nested → 父 creative 降级时跟随
        _write_skill(self.skills, "creative/nested", "nested-art", "嵌套创作技能描述")
        _write_skill(self.skills, "devops", "k8s-deploy", "Kubernetes 部署指南描述")
        # 真实 VERMES_HOME 技能目录（get_skills_dir 读 ~/.vermes/skills）
        self.real_skills = self.home / "skills"
        # get_skills_dir 可能指向 VERMES_HOME/skills — 已写入 self.skills==that path
        from vermes_constants import get_skills_dir
        self.assertEqual(Path(get_skills_dir()), self.skills)
        # 强制下次扫描（清 snapshot + LRU）
        from agent import prompt_builder as pb
        pb._SKILLS_PROMPT_CACHE.clear()
        snap = pb._skills_prompt_snapshot_path()
        if snap.exists():
            snap.unlink()
        os.environ["VERMES_SKILLS_DIR"] = str(self.skills)

    def _clear(self):
        from agent import prompt_builder as pb
        pb._SKILLS_PROMPT_CACHE.clear()
        pb._LAST_COMPACT_SKILL_CATEGORIES = None
        snap = pb._skills_prompt_snapshot_path()
        if snap.exists():
            snap.unlink()

    def test_none_is_baseline_full_descriptions(self):
        from agent.prompt_builder import build_skills_system_prompt
        self._clear()
        out = build_skills_system_prompt(compact_categories=None)
        self.assertNotIn("[names only]", out)
        self.assertIn("Kubernetes 部署指南描述", out)
        self.assertIn("画一张图（很长的描述）", out)
        self.assertIn("web_search or terminal", out)

    def test_compact_demotes_but_keeps_names(self):
        from agent.prompt_builder import build_skills_system_prompt
        self._clear()
        out = build_skills_system_prompt(
            compact_categories=frozenset({"creative"}),
            available_tools={"skill_view"},
        )
        self.assertIn("[names only]", out)
        self.assertIn("creative [names only]", out)
        self.assertIn("draw-skill", out)  # 名保留
        self.assertNotIn("画一张图（很长的描述）", out)  # 描述省略
        self.assertIn("k8s-deploy", out)
        self.assertIn("Kubernetes 部署指南描述", out)  # 编码类目仍全量
        self.assertIn("skill_view(name) as usual", out)  # hidden_note
        self.assertIn("basic tools like terminal", out)  # P1-6 无 web_search
        self.assertNotIn("nested-art", out.split("creative")[0])  # smoke

    def test_nested_category_follows_parent(self):
        from agent.prompt_builder import build_skills_system_prompt
        self._clear()
        out = build_skills_system_prompt(compact_categories=frozenset({"creative"}))
        # nested 技能名仍出现（不隐藏），其描述不应再单独成行
        self.assertIn("nested-art", out)
        self.assertNotIn("嵌套创作技能描述", out)

    def test_cache_key_separates_compact_modes(self):
        from agent.prompt_builder import build_skills_system_prompt
        self._clear()
        a = build_skills_system_prompt(compact_categories=None)
        b = build_skills_system_prompt(compact_categories=frozenset({"creative"}))
        self.assertNotEqual(a, b)
        # 再取 None 应命中缓存仍是基线
        c = build_skills_system_prompt(compact_categories=None)
        self.assertEqual(a, c)

    def test_deny_list_local_extras(self):
        from agent.prompt_builder import _NON_CODING_SKILL_CATEGORIES
        for name in ("daily", "content-marketing", "openclaw-imports", "creative", "gaming"):
            self.assertIn(name, _NON_CODING_SKILL_CATEGORIES)
        self.assertNotIn("research", _NON_CODING_SKILL_CATEGORIES)


if __name__ == "__main__":
    unittest.main()
