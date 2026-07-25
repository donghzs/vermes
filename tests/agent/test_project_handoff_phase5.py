"""Phase 5 — 通用项目 Handoff 测试

验证跨域通用性：论文/剧本/小说项目都能 record → get_active → format。
验证 facade 解耦：continuity_facade 不再 import ScholarForge。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def tmp_memory_db(tmp_path, monkeypatch):
    """临时 memory_index.db。"""
    db_path = str(tmp_path / "test_handoff_memory.db")
    # 在文件创建前就设好
    db_path_obj = type("Path", (), {"__str__": lambda s: db_path})()

    import agent.project_handoff as ph
    monkeypatch.setattr(ph, "_db_path", lambda: db_path_obj)
    # 也 mock memory_fabric 的 index_db_path
    import agent.memory_fabric as mf
    monkeypatch.setattr(mf, "index_db_path", lambda: db_path_obj)
    yield ph


class TestRecordAndGet:
    def test_record_paper_project(self, tmp_memory_db):
        """记录论文项目 handoff。"""
        ph = tmp_memory_db
        ok = ph.record_project_handoff(
            domain="paper", project_id=1,
            title="基于深度学习的图像分类",
            status="writing",
            progress="3/9 章节，4500/12000 字",
            last_section="method",
            extra={"paper_type": "本科论文", "literatures": 15},
        )
        assert ok is True

        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        assert handoffs[0]["domain"] == "paper"
        assert handoffs[0]["title"] == "基于深度学习的图像分类"
        assert handoffs[0]["progress"] == "3/9 章节，4500/12000 字"
        assert handoffs[0]["extra"]["paper_type"] == "本科论文"

    def test_record_screenplay_project(self, tmp_memory_db):
        """记录剧本项目 handoff — 跨域通用性验证。"""
        ph = tmp_memory_db
        ok = ph.record_project_handoff(
            domain="screenplay", project_id=1,
            title="《北京折叠》",
            status="writing",
            progress="第 3 幕第 2 场",
            last_section="act3_scene2",
            extra={"genre": "科幻", "characters": 5},
        )
        assert ok is True

        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        assert handoffs[0]["domain"] == "screenplay"
        assert handoffs[0]["title"] == "《北京折叠》"

    def test_record_novel_project(self, tmp_memory_db):
        """记录小说项目 handoff。"""
        ph = tmp_memory_db
        ok = ph.record_project_handoff(
            domain="novel", project_id=1,
            title="《三体》同人",
            status="writing",
            progress="第 12 章",
            last_section="chapter12",
        )
        assert ok is True

        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        assert handoffs[0]["domain"] == "novel"

    def test_record_multiple_domains(self, tmp_memory_db):
        """多域项目共存。"""
        ph = tmp_memory_db
        ph.record_project_handoff(domain="paper", project_id=1, title="论文A")
        ph.record_project_handoff(domain="screenplay", project_id=1, title="剧本B")
        ph.record_project_handoff(domain="novel", project_id=1, title="小说C")

        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 3
        domains = {h["domain"] for h in handoffs}
        assert domains == {"paper", "screenplay", "novel"}

    def test_upsert_updates_existing(self, tmp_memory_db):
        """相同 (domain, project_id) 更新而非插入。"""
        ph = tmp_memory_db
        ph.record_project_handoff(domain="paper", project_id=1, title="旧标题")
        ph.record_project_handoff(
            domain="paper", project_id=1, title="新标题",
            progress="5/9 章节",
        )

        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        assert handoffs[0]["title"] == "新标题"
        assert handoffs[0]["progress"] == "5/9 章节"

    def test_done_status_excluded(self, tmp_memory_db):
        """status=done 的项目不出现在活跃列表。"""
        ph = tmp_memory_db
        ph.record_project_handoff(domain="paper", project_id=1, title="进行中", status="writing")
        ph.record_project_handoff(domain="paper", project_id=2, title="已完成", status="done")

        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        assert handoffs[0]["title"] == "进行中"

    def test_remove_handoff(self, tmp_memory_db):
        """删除项目 handoff。"""
        ph = tmp_memory_db
        ph.record_project_handoff(domain="paper", project_id=1, title="测试")
        assert len(ph.get_active_handoffs()) == 1

        ok = ph.remove_project_handoff("paper", 1)
        assert ok is True
        assert len(ph.get_active_handoffs()) == 0


class TestFormatPrompt:
    def test_format_with_paper(self, tmp_memory_db):
        """格式化论文项目。"""
        ph = tmp_memory_db
        ph.record_project_handoff(
            domain="paper", project_id=42,
            title="基于深度学习的图像分类",
            progress="3/9 章节",
            last_section="method",
        )
        prompt = ph.format_handoffs_prompt()
        assert "论文" in prompt
        assert "#42" in prompt
        assert "基于深度学习的图像分类" in prompt
        assert "3/9 章节" in prompt
        assert "method" in prompt

    def test_format_with_screenplay(self, tmp_memory_db):
        """格式化剧本项目 — 通用性验证。"""
        ph = tmp_memory_db
        ph.record_project_handoff(
            domain="screenplay", project_id=1,
            title="《北京折叠》",
            progress="第 3 幕第 2 场",
        )
        prompt = ph.format_handoffs_prompt()
        assert "剧本" in prompt
        assert "《北京折叠》" in prompt

    def test_format_empty(self, tmp_memory_db):
        """无项目时返回空字符串。"""
        ph = tmp_memory_db
        assert ph.format_handoffs_prompt() == ""

    def test_format_multi_domain_grouped(self, tmp_memory_db):
        """多域项目在格式化中分组显示。"""
        ph = tmp_memory_db
        ph.record_project_handoff(domain="paper", project_id=1, title="论文A")
        ph.record_project_handoff(domain="novel", project_id=1, title="小说B")

        prompt = ph.format_handoffs_prompt()
        assert "论文" in prompt
        assert "小说" in prompt
        assert "论文A" in prompt
        assert "小说B" in prompt


class TestFacadeDecoupled:
    """验证 continuity_facade 不再硬依赖 ScholarForge。"""

    def test_facade_no_scholarforge_import(self):
        """continuity_facade.py 不应 import 任何 scholarforge 模块。"""
        import pathlib
        facade_code = pathlib.Path("agent/continuity_facade.py").read_text()
        assert "scholarforge" not in facade_code.lower(), (
            "continuity_facade 仍包含 scholarforge 引用！"
        )

    def test_facade_uses_generic_project_handoff(self, tmp_memory_db):
        """facade 应通过 agent.project_handoff 加载项目状态。"""
        ph = tmp_memory_db
        ph.record_project_handoff(
            domain="paper", project_id=1,
            title="跨域测试论文",
            progress="2/9 章节",
        )

        from agent.continuity_facade import load_continuity_context
        ctx = load_continuity_context("继续写我的论文")

        assert "project_handoff" in ctx.sources_loaded
        assert "跨域测试论文" in (ctx.handoff_block or "")

    def test_facade_loads_screenplay(self, tmp_memory_db):
        """facade 能加载剧本项目 — 跨域通用性端到端验证。"""
        ph = tmp_memory_db
        ph.record_project_handoff(
            domain="screenplay", project_id=1,
            title="《北京折叠》",
            progress="第 3 幕",
        )

        from agent.continuity_facade import load_continuity_context
        ctx = load_continuity_context("继续写剧本")

        assert "project_handoff" in ctx.sources_loaded
        assert "北京折叠" in (ctx.handoff_block or "")

    def test_facade_cold_start_no_projects(self, tmp_memory_db):
        """无项目时 project_handoff 不报错。"""
        from agent.continuity_facade import load_continuity_context
        ctx = load_continuity_context("你好")

        assert "project_handoff" not in ctx.sources_failed
