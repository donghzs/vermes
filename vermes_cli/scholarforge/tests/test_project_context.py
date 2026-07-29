"""
ScholarForge Phase 1 — 项目上下文注入测试

测试 project_context.py 的核心功能：
- load_project_context / format_project_context_prompt
- save_section / save_outline / save_papers
- list_active_projects / format_active_projects_prompt
- 19 个 schema 有 project_id 参数
- write/outline/search handler 的项目上下文注入 + 结果写回
"""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# 确保模块路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestProjectContext(unittest.TestCase):
    """project_context.py 核心功能测试"""

    def setUp(self):
        """用临时 DB 避免污染开发环境"""
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_scholarforge.db")
        # patch database 模块的 DB_PATH
        self._db_patcher = patch("vermes_cli.scholarforge.database.DB_PATH", self.db_path)
        self._db_patcher.start()
        # 初始化表结构
        from vermes_cli.scholarforge.database import init_db
        init_db()

    def tearDown(self):
        self._db_patcher.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_project_context_none(self):
        """project_id=0 或不存在时返回 None"""
        from vermes_cli.scholarforge.project_context import load_project_context
        self.assertIsNone(load_project_context(0))
        self.assertIsNone(load_project_context(-1))
        self.assertIsNone(load_project_context(99999))

    def test_load_project_context_exists(self):
        """创建项目后能正确加载"""
        from vermes_cli.scholarforge.database import create_project
        from vermes_cli.scholarforge.project_context import load_project_context

        proj_created = create_project("测试论文", "本科论文")
        pid = proj_created["id"]
        proj = load_project_context(pid)
        self.assertIsNotNone(proj)
        self.assertEqual(proj["title"], "测试论文")
        self.assertEqual(proj["paper_type"], "本科论文")

    def test_format_project_context_prompt_empty(self):
        """无项目时返回空字符串"""
        from vermes_cli.scholarforge.project_context import format_project_context_prompt
        self.assertEqual(format_project_context_prompt(0), "")
        self.assertEqual(format_project_context_prompt(99999), "")

    def test_format_project_context_prompt_has_title(self):
        """有项目时 prompt 包含标题"""
        from vermes_cli.scholarforge.database import create_project
        from vermes_cli.scholarforge.project_context import format_project_context_prompt

        proj_created = create_project("LLM教育应用研究", "硕士论文")
        pid = proj_created["id"]
        prompt = format_project_context_prompt(pid)
        self.assertIn("LLM教育应用研究", prompt)
        self.assertIn("硕士论文", prompt)

    def test_format_project_context_prompt_has_outline(self):
        """有大纲时 prompt 包含大纲"""
        from vermes_cli.scholarforge.database import create_project, get_conn
        from vermes_cli.scholarforge.project_context import format_project_context_prompt

        proj_created = create_project("测试大纲项目", "本科论文")
        pid = proj_created["id"]
        # 插入大纲
        with get_conn() as conn:
            import time
            now = int(time.time())
            conn.execute(
                "INSERT INTO outlines (project_id, section_key, section_number, section_title, word_count, status, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pid, "sec1", "1", "引言", 1000, "done", 0)
            )
            conn.execute(
                "INSERT INTO outlines (project_id, section_key, section_number, section_title, word_count, status, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pid, "sec2", "2", "方法", 2000, "pending", 1)
            )

        prompt = format_project_context_prompt(pid)
        self.assertIn("引言", prompt)
        self.assertIn("方法", prompt)

    def test_save_section(self):
        """save_section 写回 section_contents"""
        from vermes_cli.scholarforge.database import create_project, get_project
        from vermes_cli.scholarforge.project_context import save_section

        proj_created = create_project("测试写回", "本科论文")
        pid = proj_created["id"]
        ok = save_section(pid, "introduction", "这是引言内容...")
        self.assertTrue(ok)

        proj = get_project(pid)
        self.assertIn("introduction", proj["contents"])
        self.assertEqual(proj["contents"]["introduction"], "这是引言内容...")

    def test_save_section_upsert(self):
        """重复写同一段落应更新而非插入"""
        from vermes_cli.scholarforge.database import create_project, get_conn, get_project
        from vermes_cli.scholarforge.project_context import save_section

        proj_created = create_project("测试 upsert", "本科论文")
        pid = proj_created["id"]
        save_section(pid, "method", "第一版")
        save_section(pid, "method", "第二版")

        proj = get_project(pid)
        self.assertEqual(proj["contents"]["method"], "第二版")

        # 确认只有一行
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) c FROM section_contents WHERE project_id=? AND section_key=?",
                (pid, "method")
            ).fetchone()
            self.assertEqual(rows["c"], 1)

    def test_save_outline(self):
        """save_outline 写回 outlines 表"""
        from vermes_cli.scholarforge.database import create_project, get_project
        from vermes_cli.scholarforge.project_context import save_outline

        proj_created = create_project("测试大纲写回", "本科论文")
        pid = proj_created["id"]
        sections = [
            {"section_key": "sec1", "title": "引言", "word_count": 1000},
            {"section_key": "sec2", "title": "方法", "word_count": 2000},
            {"section_key": "sec3", "title": "结论", "word_count": 500},
        ]
        ok = save_outline(pid, sections)
        self.assertTrue(ok)

        proj = get_project(pid)
        self.assertGreaterEqual(len(proj["outline"]), 3)

    def test_save_papers(self):
        """save_papers 写回 literatures 表"""
        from vermes_cli.scholarforge.database import create_project, get_project
        from vermes_cli.scholarforge.project_context import save_papers

        proj_created = create_project("测试文献写回", "本科论文")
        pid = proj_created["id"]
        papers = [
            {"title": "Paper A", "authors": "Zhang", "year": "2024", "doi": "10.1/a"},
            {"title": "Paper B", "authors": "Li", "year": "2023", "doi": "10.2/b"},
        ]
        added = save_papers(pid, papers)
        self.assertEqual(added, 2)

        proj = get_project(pid)
        self.assertEqual(proj["literature_count"], 2)

    def test_save_papers_dedup(self):
        """重复保存相同文献应去重"""
        from vermes_cli.scholarforge.database import create_project
        from vermes_cli.scholarforge.project_context import save_papers

        proj_created = create_project("测试去重", "本科论文")
        pid = proj_created["id"]
        papers = [{"title": "Paper A", "doi": "10.1/a"}]
        self.assertEqual(save_papers(pid, papers), 1)
        self.assertEqual(save_papers(pid, papers), 0)  # 第二次不加

    def test_list_active_projects(self):
        """list_active_projects 返回列表"""
        from vermes_cli.scholarforge.database import create_project
        from vermes_cli.scholarforge.project_context import list_active_projects

        create_project("项目A", "本科论文")
        result = list_active_projects()
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 1)

    def test_format_active_projects_prompt(self):
        """format_active_projects_prompt 正常返回字符串"""
        from vermes_cli.scholarforge.database import create_project
        from vermes_cli.scholarforge.project_context import format_active_projects_prompt

        create_project("展示项目", "本科论文")
        result = format_active_projects_prompt()
        self.assertIsInstance(result, str)
        self.assertIn("展示项目", result)


class TestSchemaHasProjectId(unittest.TestCase):
    """验证 19 个 schema 都有 project_id 参数"""

    def test_all_schemas_have_project_id(self):
        """所有 ScholarForge schema 的 properties 中都包含 project_id"""
        from vermes_cli.scholarforge import tools

        schemas = [
            attr for attr in dir(tools)
            if attr.startswith("SCHOLARFORGE_") and attr.endswith("_SCHEMA")
        ]

        self.assertGreaterEqual(len(schemas), 19, f"Expected >=19 schemas, got {len(schemas)}")

        # 例外：发现工具 list_projects 本身不绑定 project_id（否则无法先发现 id）。
        # 其余所有写回/操作类工具都必须带 project_id。
        EXEMPT = {"SCHOLARFORGE_LIST_PROJECTS_SCHEMA"}

        for schema_name in schemas:
            if schema_name in EXEMPT:
                continue
            schema = getattr(tools, schema_name)
            self.assertIsInstance(schema, dict, f"{schema_name} is not a dict")
            # schema 结构: {"parameters": {"properties": {...}}}
            props = schema.get("properties", {})
            if not props:
                props = schema.get("parameters", {}).get("properties", {})
            self.assertIn(
                "project_id",
                props,
                f"{schema_name} missing 'project_id' in properties"
            )


class TestHandlerProjectIdParam(unittest.TestCase):
    """验证关键 handler 能正确处理 project_id 参数"""

    def setUp(self):
        """用临时 DB"""
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_scholarforge.db")
        self._db_patcher = patch("vermes_cli.scholarforge.database.DB_PATH", self.db_path)
        self._db_patcher.start()
        from vermes_cli.scholarforge.database import init_db
        init_db()

    def tearDown(self):
        self._db_patcher.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_handler_no_project_id(self):
        """write handler 无 project_id 时正常执行"""
        from vermes_cli.scholarforge.tools import _handle_scholarforge_write

        async def fake_llm(prompt, system=""):
            return "## 引言\n这是引言内容..."

        async def go():
            with patch("vermes_cli.scholarforge.tools._call_llm", side_effect=fake_llm):
                result = await _handle_scholarforge_write({
                    "topic": "测试主题",
                    "section_type": "introduction",
                })
                return result

        result = asyncio.run(go())
        self.assertIn("引言", result)

    def test_write_handler_writes_back_to_db(self):
        """write handler 带 project_id 时结果写回 DB"""
        from vermes_cli.scholarforge.database import create_project, get_project
        from vermes_cli.scholarforge.tools import _handle_scholarforge_write

        proj_created = create_project("测试写回", "本科论文")
        pid = proj_created["id"]

        async def fake_llm(prompt, system=""):
            return "## 引言\n这是项目上下文注入测试..."

        async def go():
            with patch("vermes_cli.scholarforge.tools._call_llm", side_effect=fake_llm):
                result = await _handle_scholarforge_write({
                    "topic": "测试主题",
                    "section_type": "introduction",
                    "project_id": pid,
                })
                return result

        result = asyncio.run(go())
        self.assertIn("引言", result)

        # 验证写回 DB
        proj = get_project(pid)
        self.assertIn("introduction", proj["contents"])
        self.assertIn("项目上下文注入测试", proj["contents"]["introduction"])

    def test_outline_handler_with_project_id(self):
        """outline handler 带 project_id 时大纲写回 DB"""
        from vermes_cli.scholarforge.database import create_project, get_project
        from vermes_cli.scholarforge.tools import _handle_scholarforge_outline

        proj_created = create_project("大纲测试", "本科论文")
        pid = proj_created["id"]

        async def fake_llm(prompt, system=""):
            return "## 摘要\n...\n## 引言\n...\n## 方法\n...\n## 结论\n..."

        async def go():
            with patch("vermes_cli.scholarforge.tools._call_llm", side_effect=fake_llm):
                result = await _handle_scholarforge_outline({
                    "topic": "测试主题",
                    "project_id": pid,
                })
                return result

        result = asyncio.run(go())
        self.assertIn("引言", result)

        # 验证大纲写回 DB
        proj = get_project(pid)
        self.assertGreaterEqual(len(proj["outline"]), 3)


if __name__ == "__main__":
    unittest.main()
