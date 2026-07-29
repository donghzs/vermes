"""测试 read_section 工具和 export 自动组装功能。"""
import pytest
import os
import sys

# 确保用测试 DB
os.environ["VERMES_HOME"] = os.path.expanduser("~/.Vermes")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_read_section.db")
    import vermes_cli.scholarforge.database as db
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    yield db


@pytest.fixture
def setup_project(tmp_db):
    """创建测试项目和章节内容。"""
    from vermes_cli.scholarforge.database import create_project, save_outline, save_section_content
    pid = create_project("测试论文", "本科论文")["id"]
    save_outline(pid, [
        {"id": "intro", "title": "绪论", "sort_order": 0},
        {"id": "method", "title": "方法", "sort_order": 1},
        {"id": "result", "title": "结果", "sort_order": 2},
    ])
    save_section_content(pid, "intro", "这是绪论内容，约200字。" * 10)
    save_section_content(pid, "method", "这是方法部分。" * 5)
    return pid


class TestReadSection:
    @pytest.mark.asyncio
    async def test_read_single_section(self, setup_project):
        from vermes_cli.scholarforge.tools import _handle_scholarforge_read_section
        out = await _handle_scholarforge_read_section({"project_id": setup_project, "section_key": "intro"})
        assert "绪论内容" in out
        assert "intro" in out

    @pytest.mark.asyncio
    async def test_read_empty_section(self, setup_project):
        from vermes_cli.scholarforge.tools import _handle_scholarforge_read_section
        out = await _handle_scholarforge_read_section({"project_id": setup_project, "section_key": "result"})
        assert "尚未写入" in out

    @pytest.mark.asyncio
    async def test_read_all_sections_overview(self, setup_project):
        from vermes_cli.scholarforge.tools import _handle_scholarforge_read_section
        out = await _handle_scholarforge_read_section({"project_id": setup_project})
        assert "章节概览" in out
        assert "绪论" in out
        assert "方法" in out
        assert "总计" in out

    @pytest.mark.asyncio
    async def test_read_section_no_project_id(self):
        from vermes_cli.scholarforge.tools import _handle_scholarforge_read_section
        out = await _handle_scholarforge_read_section({})
        assert "project_id" in out


class TestExportAutoAssemble:
    @pytest.mark.asyncio
    async def test_export_auto_assemble_from_db(self, setup_project):
        """content 为空时自动从 DB 组装。"""
        from vermes_cli.scholarforge.tools import _handle_scholarforge_export
        out = await _handle_scholarforge_export({
            "project_id": setup_project,
            "title": "测试论文",
            "content": "",  # 空内容，触发自动组装
            "format": "markdown",
        })
        # 应该成功导出并包含 DB 中的内容
        assert "✅" in out or "导出" in out

    @pytest.mark.asyncio
    async def test_export_no_content_no_db(self, tmp_db):
        """无 content 且 DB 中也无内容时报错。"""
        from vermes_cli.scholarforge.database import create_project
        from vermes_cli.scholarforge.tools import _handle_scholarforge_export
        pid = create_project("空项目", "本科论文")["id"]
        out = await _handle_scholarforge_export({
            "project_id": pid,
            "title": "空项目",
            "content": "",
            "format": "markdown",
        })
        assert "❌" in out
