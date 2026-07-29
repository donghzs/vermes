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

    def test_no_active_returns_zero(self):
        ap.set_active_project(0)
        self.assertEqual(ap.resolve_project_id({}), 0)

    def test_invalid_explicit_clamped_to_zero(self):
        self.assertEqual(ap.resolve_project_id({"project_id": "abc"}), 0)
        self.assertEqual(ap.resolve_project_id({"project_id": 0}), 0)
        self.assertEqual(ap.resolve_project_id({"project_id": -3}), 0)

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
class TestWriteMissingProjectId(unittest.TestCase):
    """write 缺 project_id 且无激活项目：必须明确 ❌，但内容仍生成（不静默丢弃）。"""

    def setUp(self):
        ap.set_active_project(0)

    def test_missing_pid_emits_warning_not_silent(self):
        with patch("vermes_cli.scholarforge.tools._call_llm", return_value="# 引言\n这是正文内容。"):
            result = asyncio.run(_handle_scholarforge_write({"topic": "t", "section_type": "abstract"}))
        self.assertTrue(result.lstrip().startswith("❌"), msg=f"期望 ❌ 前缀，实际：{result[:80]!r}")
        self.assertIn("未关联 project_id", result)
        # 内容仍生成（供用户知道工具跑通了，只是没落库）
        self.assertIn("正文", result)

    def test_missing_pid_via_registry_dispatch(self):
        # 经真实入口（_with_usage 包装）调用，验证 ❌ 前缀透传（埋点据此记 ok=0）
        with patch("vermes_cli.scholarforge.tools._call_llm", return_value="# 引言\n正文。"):
            result = _sf_registry.dispatch("scholarforge_write", {"topic": "t", "section_type": "abstract"})
        self.assertTrue(result.lstrip().startswith("❌"), msg=f"registry 入口应透传 ❌，实际：{result[:80]!r}")


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
    """replace_citations 缺 project_id 且无激活：仍生成替换结果但不静默成功（❌ 前缀）。"""

    def setUp(self):
        ap.set_active_project(0)

    def test_missing_pid_emits_warning_and_still_replaces(self):
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
            with patch("vermes_cli.scholarforge.search.search_papers", fake_search_papers), \
                 patch("vermes_cli.scholarforge.tools._call_llm", side_effect=fake_llm), \
                 patch("vermes_cli.scholarforge.citation_verifier._fuzzy_verify", return_value=fake_verify):
                return asyncio.run(_handle_scholarforge_replace_citations({"draft": draft}))

        report = run()
        self.assertTrue(report.lstrip().startswith("❌"), msg=f"期望 ❌ 前缀，实际：{report[:80]!r}")
        self.assertIn("未关联 project_id", report)
        # 替换仍发生（内容未静默丢弃）
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
