"""SkillRouter prefetch — Phase 1 L2 tests (cache-safe, fail-open, isolated FTS)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _tmp_home() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="skill-router-"))
    os.environ["VERMES_HOME"] = str(tmp)
    (tmp / "skills").mkdir(parents=True, exist_ok=True)
    return tmp


def _write_skill(root: Path, category: str, name: str, desc: str) -> None:
    d = root / category / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


class TestSkillRouterFTS(unittest.TestCase):
    def setUp(self):
        self.home = _tmp_home()
        self.skills = self.home / "skills"
        _write_skill(
            self.skills,
            "scholarforge",
            "scholarforge-thesis-pipeline",
            "学术论文全链路写作：选题、结构、引用与润色",
        )
        _write_skill(self.skills, "docx", "docx", "创建与编辑 Word 文档")
        _write_skill(self.skills, "weather", "weather", "查询天气预报")
        from agent import skill_router as sr
        self.sr = sr
        # reset index cache + point DB into tmp home
        sr._index_state["built_at"] = 0
        sr._index_state["count"] = 0
        sr._conn_cache.clear()

    def tearDown(self):
        self.sr._conn_cache.clear()
        self.sr._index_state["built_at"] = 0
        self.sr._index_state["count"] = 0

    def test_search_hits_thesis_skill(self):
        hits = self.sr.search_skills("帮我写一篇学术论文", limit=3)
        names = [h["name"] for h in hits]
        self.assertIn("scholarforge-thesis-pipeline", names)

    def test_prefetch_formats_bounded_hints(self):
        text = self.sr.SkillRouter().prefetch("写论文需要学术写作技能")
        self.assertTrue(text)
        self.assertIn("[相关技能]", text)
        self.assertIn("skill_view", text)
        # ≤3 bullets + header + footer
        bullets = [ln for ln in text.splitlines() if ln.startswith("- ")]
        self.assertLessEqual(len(bullets), 3)

    def test_empty_query_returns_empty(self):
        self.assertEqual(self.sr.SkillRouter().prefetch(""), "")
        self.assertEqual(self.sr.SkillRouter().prefetch("   "), "")

    def test_index_isolated_from_memory_rag_db(self):
        """Skill FTS lives in skills.db — not memory documents.db chunks_fts."""
        self.sr.rebuild_index(force=True)
        skills_db = self.sr._get_skills_db()
        self.assertEqual(skills_db.name, "skills.db")
        self.assertNotEqual(skills_db.parent.name, "skills")  # under rag/
        self.assertTrue(skills_db.exists())
        rag_db = self.home / "rag" / "documents.db"
        self.assertNotEqual(skills_db, rag_db)
        # skills_fts table exists only in skills.db
        conn = self.sr._get_conn(str(skills_db))
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("skills_fts", tables)
        self.assertIn("skills", tables)
        self.assertNotIn("chunks_fts", tables)

    def test_prefetch_fail_open(self):
        with patch.object(self.sr, "search_skills", side_effect=RuntimeError("boom")):
            self.assertEqual(self.sr.SkillRouter().prefetch("写论文"), "")

    def test_no_tools_schema(self):
        self.assertEqual(self.sr.SkillRouter().get_tool_schemas(), [])

    def test_provider_registered_first_party(self):
        from agent.memory_manager import _FIRST_PARTY_PROVIDER_NAMES
        self.assertIn("skill_router", _FIRST_PARTY_PROVIDER_NAMES)

    def test_description_truncated_to_60(self):
        long_desc = "很长的描述" * 40
        _write_skill(self.skills, "long", "long-skill", long_desc)
        self.sr.rebuild_index(force=True)
        text = self.sr.format_hints(
            [{"name": "long-skill", "category": "long", "description": long_desc}]
        )
        for line in text.splitlines():
            if line.startswith("- "):
                # " - name (cat)：desc"
                self.assertLessEqual(len(line), 20 + 60 + 10)

    def test_search_hits_trigger_short_query(self):
        """QClaw audit A: frontmatter trigger must answer short Chinese intents."""
        d = self.skills / "research" / "scholarforge-thesis-pipeline"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            "---\n"
            "name: scholarforge-thesis-pipeline\n"
            "description: Academic thesis pipeline for research writing\n"
            "trigger: 写论文, 学位论文, thesis, 学术写作\n"
            "---\n\n# thesis\n",
            encoding="utf-8",
        )
        self.sr.rebuild_index(force=True)
        hits = self.sr.search_skills("写论文", limit=5)
        names = [h["name"] for h in hits]
        self.assertIn("scholarforge-thesis-pipeline", names)
        # trigger surfaced in hit payload
        hit = next(h for h in hits if h["name"] == "scholarforge-thesis-pipeline")
        self.assertIn("写论文", hit.get("trigger") or "")

    def test_like_fallback_rejects_single_word(self):
        """QClaw round2 B: real weather description has BOTH 'data' and
        'analysis' inside a negation scope ("NOT for: ...") — must not
        pass the score>=2 filter on 'data analysis'."""
        # Verbatim from ~/.vermes/skills weather SKILL.md (audit fixture)
        _write_skill(
            self.skills,
            "weather",
            "weather",
            "NOT for: historical weather data, severe weather alerts, "
            "or detailed meteorological analysis.",
        )
        _write_skill(
            self.skills,
            "data",
            "data-analysis-toolkit",
            "data analysis pipelines statistics visualization",
        )
        self.sr.rebuild_index(force=True)
        hits = self.sr.search_skills("data analysis", limit=8)
        names = [h["name"] for h in hits]
        self.assertIn("data-analysis-toolkit", names)
        self.assertNotIn(
            "weather",
            names,
            f"negation-scope false positive: {names}",
        )


    def test_default_skill_router_enabled_is_false(self):
        from vermes_cli.config import DEFAULT_CONFIG
        self.assertIs(
            DEFAULT_CONFIG["agent"].get("skill_router_enabled"),
            False,
        )


if __name__ == "__main__":
    unittest.main()
