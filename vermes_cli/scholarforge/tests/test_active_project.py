"""P0-A 修复验证：激活项目机制 + 写回缺失 project_id 不静默成功。

覆盖用户批准的方案 C①+C②+A：
- C① scholarforge_list_projects 列出项目并标出激活项目；
- C② 前端激活 / 工具 set_active_project 把 project_id 种入后端，写回类工具回退到激活项目；
- A  写回类工具缺失 project_id 且无可回退激活项目时，明确 ❌ 报错（ok=0，绝不静默成功）。

为避免污染真实 SQLite（~/.vermes/scholarforge.db），所有 DB 触碰函数均 mock 到源模块。
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import vermes_cli.scholarforge.active_project as ap
from vermes_cli.scholarforge.tools import (
    _handle_scholarforge_write,
    _handle_scholarforge_list_projects,
    _handle_scholarforge_set_active_project,
    _handle_scholarforge_replace_citations,
)
import vermes_cli.scholarforge.tools as _sf_tools
from tools.registry import registry as _sf_registry

if _sf_registry.get_entry("scholarforge_list_projects") is None:
    _sf_tools.register_tools()


# ───────────────────────── C② 依赖：resolve 逻辑（纯函数，无 DB） ─────────────────────────
class TestActiveProjectResolve(unittest.TestCase):
    def setUp(self):
        ap.set_active_project(0)  # 每个用例前重置激活

    def test_explicit_wins_over_active(self):
        ap.set_active_project(7)
        self.assertEqual(ap.resolve_project_id({"project_id": 9}), 9)

    def test_fallback_to_active_when_absent(self):
        ap.set_active_project(7)
        self.assertEqual(ap.resolve_project_id({}), 7)

    def test_no_active_auto_creates_default_project(self):
        ap.set_active_project(0)
        with patch("vermes_cli.scholarforge.active_project.list_projects", return_value=[]), \
             patch("vermes_cli.scholarforge.active_project.create_project", return_value={"id": 7}) as m_create:
            pid = ap.resolve_project_id({})
        self.assertEqual(pid, 7)
        self.assertEqual(ap.get_active_project(), 7)  # 兜底项目被设为激活
        m_create.assert_called_once()

    def test_no_active_picks_most_recent_existing(self):
        ap.set_active_project(0)
        with patch("vermes_cli.scholarforge.active_project.list_projects",
                   return_value=[{"id": 12, "title": "Recent"}, {"id": 3, "title": "Old"}]), \
             patch("vermes_cli.scholarforge.active_project.create_project") as m_create:
            pid = ap.resolve_project_id({})
        self.assertEqual(pid, 12)  # 取最近（updated_at DESC 首位）
        m_create.assert_not_called()  # 已有项目则不新建

    def test_invalid_explicit_clamped_then_auto_default(self):
        # 非法显式值夹到 0，随后 A1 兜底默认项目（不返回裸 0）
        with patch("vermes_cli.scholarforge.active_project.list_projects", return_value=[]), \
             patch("vermes_cli.scholarforge.active_project.create_project", return_value={"id": 9}):
            self.assertEqual(ap.resolve_project_id({"project_id": "abc"}), 9)
            self.assertEqual(ap.resolve_project_id({"project_id": 0}), 9)
            self.assertEqual(ap.resolve_project_id({"project_id": -3}), 9)

    def test_session_isolation(self):
        ap.set_active_project(0)
        ap.set_active_project(5, session_id="sessA")
        ap.set_active_project(9, session_id="sessB")
        self.assertEqual(ap.get_active_project("sessA"), 5)
        self.assertEqual(ap.get_active_project("sessB"), 9)


# ───────────────────────── C① list_projects 标出激活项目 ─────────────────────────
class TestListProjectsMarksActive(unittest.TestCase):
    def test_active_marker_present(self):
        ap.set_active_project(42)
        fake_projects = [
            {"id": 1, "title": "P1", "paper_type": "本科论文", "section_count": 3, "total_words": 1000, "literature_count": 2},
            {"id": 42, "title": "Active Thesis", "paper_type": "硕士论文", "section_count": 10, "total_words": 50000, "literature_count": 0},
            {"id": 3, "title": "P3", "paper_type": "博士论文", "section_count": 5, "total_words": 2000, "literature_count": 1},
        ]
        with patch("vermes_cli.scholarforge.database.list_projects", return_value=fake_projects):
            result = asyncio.run(_handle_scholarforge_list_projects({}))
        self.assertIn("Active Thesis", result)
        self.assertIn("[激活]", result)
        # 非激活项目不带 [激活]
        self.assertNotIn("#1 《P1》 ➡️ [激活]", result)

    def test_no_projects_message(self):
        ap.set_active_project(0)
        with patch("vermes_cli.scholarforge.database.list_projects", return_value=[]):
            result = asyncio.run(_handle_scholarforge_list_projects({}))
        self.assertIn("没有任何论文项目", result)


# ───────────────────────── C② set_active_project 工具 ─────────────────────────
class TestSetActiveProjectHandler(unittest.TestCase):
    def setUp(self):
        ap.set_active_project(0)

    def test_sets_active_and_reports(self):
        with patch("vermes_cli.scholarforge.database.get_project", return_value={"id": 99, "title": "X"}):
            result = asyncio.run(_handle_scholarforge_set_active_project({"project_id": 99}))
        self.assertEqual(ap.get_active_project(), 99)
        self.assertIn("99", result)
        self.assertIn("激活项目", result)

    def test_missing_id_reports_error(self):
        result = asyncio.run(_handle_scholarforge_set_active_project({}))
        self.assertTrue(result.lstrip().startswith("❌"))

    def test_zero_id_reports_error(self):
        result = asyncio.run(_handle_scholarforge_set_active_project({"project_id": 0}))
        self.assertTrue(result.lstrip().startswith("❌"))

    def test_nonexistent_project_reports_error(self):
        with patch("vermes_cli.scholarforge.database.get_project", return_value=None):
            result = asyncio.run(_handle_scholarforge_set_active_project({"project_id": 12345}))
        self.assertTrue(result.lstrip().startswith("❌"))
        self.assertIn("不存在", result)


# ───────────────────────── A 缺失即 ❌（写回类工具不静默成功） ─────────────────────────
class TestWriteMissingProjectIdAutoDefault(unittest.TestCase):
    """A1：write 缺 project_id 且无激活项目 → 兜底默认项目，内容落库、无 ❌（不再静默丢内容）。"""

    def setUp(self):
        ap.set_active_project(0)

    def test_auto_default_saves_without_warning(self):
        fake_ctx = "【项目】上下文"
        with patch("vermes_cli.scholarforge.active_project.list_projects", return_value=[]), \
             patch("vermes_cli.scholarforge.active_project.create_project", return_value={"id": 7}), \
             patch("vermes_cli.scholarforge.tools._call_llm", return_value="# 引言\n正文段落。"), \
             patch("vermes_cli.scholarforge.project_context.auto_snapshot"), \
             patch("vermes_cli.scholarforge.project_context.format_project_context_prompt", return_value=fake_ctx), \
             patch("vermes_cli.scholarforge.project_context.load_project_context", return_value={"title": "T", "paper_type": "本科论文"}), \
             patch("vermes_cli.scholarforge.project_context.get_style_prompt", return_value=""), \
             patch("vermes_cli.scholarforge.project_context.save_section") as m_save, \
             patch("vermes_cli.scholarforge.quality_gate.run_quality_gate", return_value=("# 引言\n正文段落。", "", False)):
            result = asyncio.run(_handle_scholarforge_write({"topic": "t", "section_type": "abstract"}))
        self.assertFalse(result.lstrip().startswith("❌"), msg=f"默认项目应落库不 ❌，实际：{result[:80]!r}")
        self.assertIn("正文", result)
        m_save.assert_called_once()
        self.assertEqual(m_save.call_args[0][0], 7)

    def test_auto_default_via_registry_dispatch(self):
        with patch("vermes_cli.scholarforge.active_project.list_projects", return_value=[]), \
             patch("vermes_cli.scholarforge.active_project.create_project", return_value={"id": 7}), \
             patch("vermes_cli.scholarforge.tools._call_llm", return_value="# 引言\n正文。"):
            result = _sf_registry.dispatch("scholarforge_write", {"topic": "t", "section_type": "abstract"})
        self.assertFalse(result.lstrip().startswith("❌"), msg=f"registry 入口应透传成功结果，实际：{result[:80]!r}")


class TestEnsureDefaultProject(unittest.TestCase):
    """_ensure_default_project 兜底逻辑单测。"""

    def setUp(self):
        ap.set_active_project(0)

    def test_empty_db_creates_default(self):
        with patch("vermes_cli.scholarforge.active_project.list_projects", return_value=[]), \
             patch("vermes_cli.scholarforge.active_project.create_project", return_value={"id": 5}) as m_create:
            pid = ap._ensure_default_project()
        self.assertEqual(pid, 5)
        self.assertEqual(ap.get_active_project(), 5)
        m_create.assert_called_once_with("Vermes 默认项目")

    def test_nonempty_db_picks_recent_no_create(self):
        with patch("vermes_cli.scholarforge.active_project.list_projects",
                   return_value=[{"id": 12, "title": "R"}, {"id": 3, "title": "O"}]), \
             patch("vermes_cli.scholarforge.active_project.create_project") as m_create:
            pid = ap._ensure_default_project()
        self.assertEqual(pid, 12)
        m_create.assert_not_called()

    def test_db_error_returns_zero(self):
        with patch("vermes_cli.scholarforge.active_project.list_projects", side_effect=RuntimeError("boom")):
            self.assertEqual(ap._ensure_default_project(), 0)


class TestWriteFallsBackToActive(unittest.TestCase):
    """write 缺 project_id 但已 set_active_project：应解析到激活项目并落库（无 ❌）。"""

    def test_active_resolves_no_warning_and_saves(self):
        ap.set_active_project(52)
        fake_ctx = "【项目】硕士论文上下文"
        with patch("vermes_cli.scholarforge.tools._call_llm", return_value="# 引言\n正文段落。") as _m, \
             patch("vermes_cli.scholarforge.project_context.auto_snapshot") as m_snap, \
             patch("vermes_cli.scholarforge.project_context.format_project_context_prompt", return_value=fake_ctx), \
             patch("vermes_cli.scholarforge.project_context.load_project_context", return_value={"title": "T", "paper_type": "硕士论文"}), \
             patch("vermes_cli.scholarforge.project_context.get_style_prompt", return_value=""), \
             patch("vermes_cli.scholarforge.project_context.save_section") as m_save, \
             patch("vermes_cli.scholarforge.quality_gate.run_quality_gate",
                   return_value=("# 引言\n正文段落。", "", False)):
            result = asyncio.run(_handle_scholarforge_write({"topic": "t", "section_type": "abstract"}))

        self.assertFalse(result.lstrip().startswith("❌"), msg=f"激活项目应解析成功，不应 ❌，实际：{result[:80]!r}")
        self.assertIn("正文", result)
        # 写回应作用于激活项目 52（且确实调用了 save_section，证明落库路径打通）
        m_save.assert_called_once()
        self.assertEqual(m_save.call_args[0][0], 52)


class TestReplaceCitationsMissingProjectId(unittest.TestCase):
    """A1：replace_citations 缺 project_id 且无激活 → 兜底默认项目，引用替换后仍写回（无 ❌）。"""

    def setUp(self):
        ap.set_active_project(0)

    def test_auto_default_no_warning_and_still_replaces(self):
        draft = "Memory consolidation [1] helps learning."
        fake_papers = [MagicMockPaper("Initial Study Alpha", abstract="x")]

        async def fake_search_papers(keyword, limit=10):
            for p in fake_papers:
                yield p

        async def fake_llm(prompt, system="", **kwargs):
            if "关键短语" in prompt:
                return "memory consolidation"
            if "打分" in prompt:
                return "1: 0.9"
            return "ok"

        fake_verify = unittest.mock.MagicMock(score=10, accurate=True)

        def run():
            with patch("vermes_cli.scholarforge.active_project.list_projects", return_value=[]), \
                 patch("vermes_cli.scholarforge.active_project.create_project", return_value={"id": 7}), \
                 patch("vermes_cli.scholarforge.search.search_papers", fake_search_papers), \
                 patch("vermes_cli.scholarforge.tools._call_llm", side_effect=fake_llm), \
                 patch("vermes_cli.scholarforge.citation_verifier._fuzzy_verify", return_value=fake_verify), \
                 patch("vermes_cli.scholarforge.project_context.auto_snapshot"), \
                 patch("vermes_cli.scholarforge.database.add_literature"), \
                 patch("vermes_cli.scholarforge.database.list_literature", return_value=[]):
                return asyncio.run(_handle_scholarforge_replace_citations({"draft": draft}))

        report = run()
        self.assertFalse(report.lstrip().startswith("❌"), msg=f"默认项目应写回不 ❌，实际：{report[:80]!r}")
        self.assertIn("处理后正文", report)


class MagicMockPaper:
    """极简 PaperResult 替身，仅满足 replace_citations 在 project_id=0 路径所需属性。"""

    def __init__(self, title, abstract="", year="", venue="", authors=None, doi="", url="", source=""):
        self.title = title
        self.abstract = abstract
        self.year = year
        self.venue = venue
        self.authors = authors or ["X"]
        self.doi = doi
        self.url = url
        self.source = source
        self.paper_id = title


if __name__ == "__main__":
    unittest.main()
