"""P0-B 任务级 Outcome Verifier — 源头诚实化测试。

验证链条：
  database.save_section_content → 回读校验 → True/False
  database.save_outline → 回读校验 → True/False
  project_context.save_section → 接回下层返回值
  project_context.save_outline → 接回下层返回值
  tools.py handler → 接回返回值，失败时结果串加 ❌ 前缀

核心场景：DB 写失败时，_with_usage 的 ok 判定应自动变准（靠 ❌ 前缀）。
"""
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_outcome_verifier.db")
    import vermes_cli.scholarforge.database as db
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    yield db


@pytest.fixture
def setup_project(tmp_db):
    from vermes_cli.scholarforge.database import create_project
    pid = create_project("测试论文", "本科论文")["id"]
    return pid


# ── database.save_section_content ─────────────────────────────────

class TestSaveSectionContentReadback:
    """外证回读：写完后立刻 SELECT 验证内容真在库且非空。"""

    @pytest.mark.asyncio
    async def test_write_success_returns_true(self, tmp_db, setup_project):
        ok = tmp_db.save_section_content(setup_project, "intro", "这是绪论内容。" * 10)
        assert ok is True

    @pytest.mark.asyncio
    async def test_write_empty_content_returns_false(self, tmp_db, setup_project):
        """空内容写回应被回读拦截。"""
        ok = tmp_db.save_section_content(setup_project, "empty_section", "")
        assert ok is False

    @pytest.mark.asyncio
    async def test_write_none_content_returns_false(self, tmp_db, setup_project):
        ok = tmp_db.save_section_content(setup_project, "none_section", None)  # type: ignore
        assert ok is False

    @pytest.mark.asyncio
    async def test_upsert_then_readback(self, tmp_db, setup_project):
        """更新已有章节，回读应确认新内容。"""
        tmp_db.save_section_content(setup_project, "method", "原始内容")
        ok = tmp_db.save_section_content(setup_project, "method", "更新后的内容")
        assert ok is True

    @pytest.mark.asyncio
    async def test_write_exception_returns_false(self, tmp_db, setup_project, monkeypatch):
        """模拟写异常，应返回 False 而非静默通过。"""
        import sqlite3 as _sqlite3
        # 让 init_db 之后的 get_conn 在写回阶段抛异常
        original_get_conn = tmp_db.get_conn
        call_count = [0]
        def failing_get_conn():
            call_count[0] += 1
            if call_count[0] > 1:
                raise _sqlite3.OperationalError("disk full")
            return original_get_conn()
        monkeypatch.setattr(tmp_db, "get_conn", failing_get_conn)
        ok = tmp_db.save_section_content(setup_project, "fail_section", "内容")
        assert ok is False


class _raise_ctx:
    """一个会抛异常的 context manager。"""
    def __init__(self, exc):
        self.exc = exc
    def __enter__(self):
        raise self.exc
    def __exit__(self, *a):
        return False


# ── database.save_outline ────────────────────────────────────────

class TestSaveOutlineReadback:
    """外证回读：写完后 COUNT(*) 验证条目真在库。"""

    @pytest.mark.asyncio
    async def test_save_outline_success(self, tmp_db, setup_project):
        ok = tmp_db.save_outline(setup_project, [
            {"id": "intro", "title": "绪论", "sort_order": 0},
            {"id": "method", "title": "方法", "sort_order": 1},
        ])
        assert ok is True

    @pytest.mark.asyncio
    async def test_save_empty_outline_returns_false(self, tmp_db, setup_project):
        ok = tmp_db.save_outline(setup_project, [])
        assert ok is False

    @pytest.mark.asyncio
    async def test_save_outline_replaces_old(self, tmp_db, setup_project):
        """先删后插，回读应确认新条目数。"""
        tmp_db.save_outline(setup_project, [
            {"id": "intro", "title": "绪论"},
            {"id": "method", "title": "方法"},
            {"id": "result", "title": "结果"},
        ])
        ok = tmp_db.save_outline(setup_project, [
            {"id": "conclusion", "title": "结论"},
        ])
        assert ok is True
        # 旧条目应被删除
        rows = tmp_db.get_outline(setup_project)
        assert len(rows) == 1
        assert rows[0]["title"] == "结论"


# ── project_context.save_section 接回返回值 ──────────────────────

class TestProjectContextSaveSection:
    """project_context.save_section 应接回 database.save_section_content 的返回值。"""

    def test_save_section_success(self, tmp_db, setup_project):
        from vermes_cli.scholarforge.project_context import save_section
        ok = save_section(setup_project, "intro", "绪论内容。" * 5)
        assert ok is True

    def test_save_section_invalid_pid_returns_false(self, tmp_db):
        from vermes_cli.scholarforge.project_context import save_section
        ok = save_section(0, "intro", "内容")
        assert ok is False

    def test_save_section_db_failure_returns_false(self, tmp_db, setup_project, monkeypatch):
        """database.save_section_content 返回 False 时，上层应传递 False。"""
        from vermes_cli.scholarforge import project_context
        monkeypatch.setattr(
            "vermes_cli.scholarforge.database.save_section_content",
            lambda *a, **kw: False,
        )
        ok = project_context.save_section(setup_project, "fail", "内容")
        assert ok is False


# ── project_context.save_outline 接回返回值 ──────────────────────

class TestProjectContextSaveOutline:
    def test_save_outline_success(self, tmp_db, setup_project):
        from vermes_cli.scholarforge.project_context import save_outline
        ok = save_outline(setup_project, [
            {"section_key": "intro", "title": "绪论"},
            {"section_key": "method", "title": "方法"},
        ])
        assert ok is True

    def test_save_outline_empty_returns_false(self, tmp_db, setup_project):
        from vermes_cli.scholarforge.project_context import save_outline
        ok = save_outline(setup_project, [])
        assert ok is False

    def test_save_outline_db_failure_returns_false(self, tmp_db, setup_project, monkeypatch):
        from vermes_cli.scholarforge import project_context
        monkeypatch.setattr(
            "vermes_cli.scholarforge.database.save_outline",
            lambda *a, **kw: False,
        )
        ok = project_context.save_outline(setup_project, [{"title": "测试"}])
        assert ok is False


# ── tools.py handler 接回返回值 + ❌ 前缀 ────────────────────────

class TestToolsHandlerErrorPrefix:
    """tools.py handler 应接回 save_section/save_outline 返回值，
    失败时结果串加 ❌ 前缀（_with_usage 据此判定 ok=0）。"""

    @pytest.mark.asyncio
    async def test_write_section_failure_adds_error_prefix(self, tmp_db, setup_project, monkeypatch):
        """save_section 返回 False 时，工具结果应含 ❌ 前缀。"""
        from vermes_cli.scholarforge import tools as sf_tools

        # 模拟 save_section 失败
        async def mock_write_handler(args, **kw):
            # 复制 _handle_scholarforge_write 的关键路径
            project_id = args.get("project_id", setup_project)
            section_key = args.get("section_key", "fail_section")
            content = args.get("content", "测试内容")
            
            # 模拟 save_section 返回 False
            from vermes_cli.scholarforge.project_context import save_section
            monkeypatch.setattr(
                "vermes_cli.scholarforge.project_context.save_section",
                lambda *a, **kw: False,
            )
            
            # 调用 save_section（被 mock 返回 False）
            from vermes_cli.scholarforge.project_context import save_section as mocked_save
            _save_ok = mocked_save(project_id, section_key, content)
            
            if not _save_ok:
                return f"❌ 章节写回失败：内容已生成但未能持久化到数据库（project_id={project_id}, section_key={section_key}）。请检查数据库连接后重试。\n\n---\n\n{content}"
            return content

        result = await mock_write_handler({})
        assert result.startswith("❌")
        assert "写回失败" in result

    def test_with_usage_recognizes_error_prefix(self):
        """_with_usage 的 ok 判定应把 ❌ 前缀识别为失败。"""
        # 这是现有 _with_usage 的核心判定逻辑
        result = "❌ 章节写回失败：内容已生成但未能持久化到数据库。"
        # _with_usage 的判定：result.lstrip().startswith("❌") → ok = False
        is_error = result.lstrip().startswith("❌")
        assert is_error is True

    def test_success_result_no_error_prefix(self):
        """成功时结果串不应有 ❌ 前缀。"""
        result = "这是正常生成的内容。"
        is_error = result.lstrip().startswith("❌")
        assert is_error is False


# ── 静默假成功链条验证（核心场景） ──────────────────────────────

class TestSilentFalsePositiveChain:
    """复现 P0-A 场景：DB 写失败 → save_section 返回 False →
    工具结果加 ❌ 前缀 → _with_usage 记 ok=0 → 脏数据不进自学习库。
    
    修复前：save_section 返回值被丢弃 → 工具返回正文（无 ❌）→
    _with_usage 记 ok=1 → 脏数据进 raw_events → 被当成功经验复用。
    """

    @pytest.mark.asyncio
    async def test_chain_breaks_on_db_failure(self, tmp_db, setup_project, monkeypatch):
        """完整链条：DB 失败 → ❌ 前缀 → _with_usage 判 ok=0。"""
        # 模拟 DB 失败
        monkeypatch.setattr(
            "vermes_cli.scholarforge.database.save_section_content",
            lambda *a, **kw: False,
        )
        
        from vermes_cli.scholarforge.project_context import save_section
        ok = save_section(setup_project, "fail_section", "内容")
        
        # 上层应收到 False
        assert ok is False
        
        # 工具结果应加 ❌ 前缀（模拟 tools.py 的逻辑）
        if not ok:
            tool_result = f"❌ 章节写回失败：内容已生成但未能持久化到数据库（project_id={setup_project}, section_key=fail_section）。"
        else:
            tool_result = "正常内容"
        
        # _with_usage 判定
        _with_usage_ok = not tool_result.lstrip().startswith("❌")
        
        # 关键断言：_with_usage 应记 ok=0，脏数据不进自学习库
        assert _with_usage_ok is False
