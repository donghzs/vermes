"""ScholarForge Phase 3 — 项目级 handoff 测试

验证 continuity_facade 能加载活跃论文项目状态。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """临时 ScholarForge DB。"""
    db_path = str(tmp_path / "test_handoff.db")
    import hermes_cli.scholarforge.database as db
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    yield db


class TestProjectHandoff:
    def test_handoff_with_active_project(self, tmp_db):
        """有活跃项目时，format_active_projects_prompt 应返回非空。"""
        result = tmp_db.create_project(
            title="手风琴演奏对大脑认知的影响",
            paper_type="本科论文",
            target_words=8000,
        )
        pid = result["id"]

        from hermes_cli.scholarforge.project_context import format_active_projects_prompt
        prompt = format_active_projects_prompt()
        assert prompt != ""
        assert "手风琴演奏对大脑认知的影响" in prompt
        assert f"#{pid}" in prompt

    def test_handoff_no_projects(self, tmp_db):
        """无项目时返回空字符串。"""
        from hermes_cli.scholarforge.project_context import format_active_projects_prompt
        prompt = format_active_projects_prompt()
        assert prompt == ""

    def test_handoff_multiple_projects(self, tmp_db):
        """多项目时都应显示。"""
        tmp_db.create_project(title="论文A", paper_type="本科论文")
        tmp_db.create_project(title="论文B", paper_type="硕士论文")

        from hermes_cli.scholarforge.project_context import format_active_projects_prompt
        prompt = format_active_projects_prompt()
        assert "论文A" in prompt
        assert "论文B" in prompt

    def test_continuity_facade_loads_project_handoff(self, tmp_db):
        """continuity_facade 应加载 project_handoff 源。"""
        tmp_db.create_project(title="快照论文", paper_type="本科论文")

        from agent.continuity_facade import load_continuity_context
        ctx = load_continuity_context("继续写我的论文")
        
        # project_handoff 应在 sources_loaded 中
        assert "project_handoff" in ctx.sources_loaded
        # handoff_block 应包含项目信息
        assert "快照论文" in ctx.handoff_block

    def test_continuity_facade_cold_start_no_projects(self, tmp_db):
        """无项目时 project_handoff block 为空，不阻断其他源。"""
        from agent.continuity_facade import load_continuity_context
        ctx = load_continuity_context("你好")

        # project_handoff 不在 sources_failed 中（即未报错）
        assert "project_handoff" not in ctx.sources_failed
        # 整体不阻断
        assert ctx.is_empty or ctx.sources_loaded
