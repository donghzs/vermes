"""E-P0-4 对外文案纠偏 — 宣称 ≤ 实现（真源契约，防回潮）。"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class TestExternalCopyHonesty(unittest.TestCase):
    def test_no_false_30_plus_builtin_skills_claim(self):
        for rel in ("AGENTS.md", "README.md", "vermes_cli/tips.py"):
            text = _read(rel)
            self.assertNotIn("30+ 内置技能", text, rel)
            self.assertNotIn("内置 30+ 技能", text, rel)
            self.assertNotIn("Over 80 bundled skills", text, rel)

    def test_agents_and_readpoint_to_optional_and_runtime_dir(self):
        agents = _read("AGENTS.md")
        readme = _read("README.md")
        self.assertIn("optional-skills", agents)
        self.assertIn("~/.vermes/skills/", agents)
        self.assertIn("optional-skills", readme)

    def test_trustgate_copy_is_not_full_sandbox_claim(self):
        settings = _read("frontend/src/components/Settings.vue")
        self.assertIn("声明层", settings)
        self.assertIn("fail-open", settings)
        self.assertNotIn("中高危操作直接拒绝（不给建议），低危放行", settings)
        # 禁止把声明层门禁写成全量核验/沙箱（否定式提及也不出现在 UI 文案）
        self.assertNotIn("插件沙箱", settings)
        self.assertNotRegex(settings, r"全工具\s*outcome\s*核验")

    def test_wechat_free_tier_copy_honest(self):
        settings = _read("frontend/src/components/Settings.vue")
        guide = _read("frontend/src/components/WelcomeGuide.vue")
        self.assertNotIn("✅ 微信登录即用 · ✅ 无需 API Key · ✅ Agnes AI 免费驱动", settings)
        self.assertIn("默认模型 Agnes", settings)
        self.assertNotIn("✅ 扫码即用，无需其他注册", guide)

    def test_website_skills_catalog_not_pretending_full_builtin(self):
        catalog = _read("website/docs/reference/skills-catalog.md")
        self.assertIn("optional-skills", catalog)
        self.assertNotIn("large built-in skill library copied into", catalog)
        skills_doc = _read("website/docs/user-guide/features/skills.md")
        self.assertIn("optional", skills_doc.lower())
        self.assertNotIn("ships with a set of bundled skills in `skills/` inside the repo. On install", skills_doc)

    def test_harness_doc_does_not_claim_default_path_skips_import(self):
        harness = _read("harness/__init__.py")
        self.assertNotIn(
            "nothing in the default request path imports them",
            harness,
        )
        # docstring 可能跨行，断言语义片段而非整句
        self.assertIn("are imported", harness)
        self.assertIn("from production agent paths", harness)
        self.assertIn("default request path", harness)

    def test_forbidden_false_claims_absent_from_ui_and_docs(self):
        banned = [
            r"L1/L2/L3\s*压缩",
            r"自动\s*dream",
            r"记忆\s*dream",
            r"全工具\s*outcome\s*核验",
            r"插件沙箱",
        ]
        targets = [
            "README.md",
            "AGENTS.md",
            "frontend/src/components/Settings.vue",
            "frontend/src/components/WelcomeGuide.vue",
            "website/docs/reference/skills-catalog.md",
            "website/docs/user-guide/features/skills.md",
            "docs/2.5-frontend-event-contract.md",
        ]
        for rel in targets:
            text = _read(rel)
            for pat in banned:
                # 契约文档允许以「禁止宣称」形式出现
                if rel.endswith("2.5-frontend-event-contract.md") and (
                    "禁止" in text or "文案" in text
                ):
                    continue
                self.assertIsNone(
                    re.search(pat, text),
                    f"{rel} 仍含未实现宣称: {pat}",
                )


if __name__ == "__main__":
    unittest.main()
