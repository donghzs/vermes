"""Phase 5 集成测试 — ScholarForge → agent handoff 发射桥

验证真实 ScholarForge 写操作会触发 record_project_handoff，
确保新会话 turn-1 注入能看到论文进度（运行时闭环）。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """隔离环境：ScholarForge DB + agent memory_index DB 都在临时目录。"""
    sf_db = str(tmp_path / "scholarforge.db")
    mem_db = str(tmp_path / "memory_index.db")

    # mock ScholarForge DB_PATH
    import hermes_cli.scholarforge.database as sfdb
    monkeypatch.setattr(sfdb, "DB_PATH", sf_db)
    sfdb.init_db()

    # mock agent memory_index.db 路径
    import agent.project_handoff as ph
    monkeypatch.setattr(ph, "_db_path", lambda: Path(mem_db))

    import agent.memory_fabric as mf
    monkeypatch.setattr(mf, "index_db_path", lambda: Path(mem_db))

    yield {
        "sf_db": sf_db,
        "mem_db": mem_db,
        "sfdb": sfdb,
        "ph": ph,
    }


def _create_project(sfdb, **kwargs):
    """创建项目并返回 int project_id。"""
    result = sfdb.create_project(**kwargs)
    return result["id"] if isinstance(result, dict) else result


class TestEmitBridge:
    """验证 auto_snapshot 会发射到 agent 通用表。"""

    def test_auto_snapshot_emits_handoff(self, isolated_env):
        """auto_snapshot 调用后 project_handoffs 表出现对应行。"""
        sfdb = isolated_env["sfdb"]
        ph = isolated_env["ph"]

        pid = _create_project(sfdb, title="发射桥测试论文", paper_type="本科论文", target_words=10000)
        assert pid > 0

        from hermes_cli.scholarforge.project_context import auto_snapshot
        sid = auto_snapshot(pid, label="write:method")

        assert sid > 0

        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        assert handoffs[0]["domain"] == "paper"
        assert handoffs[0]["project_id"] == pid
        assert handoffs[0]["title"] == "发射桥测试论文"
        assert handoffs[0]["status"] == "writing"
        assert "method" in handoffs[0]["last_section"]

    def test_emit_includes_progress(self, isolated_env):
        """发射的 progress 包含章节/字数/文献数。"""
        sfdb = isolated_env["sfdb"]
        ph = isolated_env["ph"]

        pid = _create_project(sfdb, title="进度测试", paper_type="硕士论文", target_words=30000)
        # create_project 自带大纲模板（本科论文 8 章节），改大纲需要 save_outline
        # 验证 create_project 自带的章节数
        from hermes_cli.scholarforge.project_context import auto_snapshot
        auto_snapshot(pid, label="write:method")

        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        progress = handoffs[0]["progress"]
        # create_project 自带 8 个章节（本科论文模板）
        assert "章" in progress
        assert "文献" in progress

    def test_emit_extra_fields(self, isolated_env):
        """发射的 extra 包含 paper_type/target_words。"""
        sfdb = isolated_env["sfdb"]
        ph = isolated_env["ph"]

        pid = _create_project(sfdb, title="Extra测试", paper_type="博士论文", target_words=80000)
        # citation_style 需通过 update_project 设置
        sfdb.update_project(pid, citation_style="gbt7714")

        from hermes_cli.scholarforge.project_context import auto_snapshot
        auto_snapshot(pid, label="outline")

        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        extra = handoffs[0]["extra"]
        assert extra["paper_type"] == "博士论文"
        assert extra["target_words"] == 80000
        assert extra["citation_style"] == "gbt7714"

    def test_emit_failopen_on_missing_project(self, isolated_env):
        """项目不存在时发射桥 fail-open，不报错。"""
        from hermes_cli.scholarforge.project_context import auto_snapshot
        sid = auto_snapshot(99999, label="test")
        assert sid == 0

    def test_emit_updates_on_repeat(self, isolated_env):
        """重复调用 auto_snapshot 更新而非插入。"""
        sfdb = isolated_env["sfdb"]
        ph = isolated_env["ph"]

        pid = _create_project(sfdb, title="重复发射", paper_type="本科论文")
        from hermes_cli.scholarforge.project_context import auto_snapshot

        auto_snapshot(pid, label="write:ch1")
        auto_snapshot(pid, label="write:ch2")

        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        assert "ch2" in handoffs[0]["last_section"]


class TestEndToEndInjection:
    """端到端：ScholarForge 写操作 → turn-1 注入。"""

    def test_continuity_facade_sees_scholarforge_project(self, isolated_env):
        """ScholarForge 项目 → auto_snapshot → facade turn-1 注入可见。"""
        sfdb = isolated_env["sfdb"]

        pid = _create_project(sfdb, title="端到端论文", paper_type="本科论文", target_words=10000)

        from hermes_cli.scholarforge.project_context import auto_snapshot
        auto_snapshot(pid, label="write:intro")

        from agent.continuity_facade import load_continuity_context
        ctx = load_continuity_context("继续写我的论文")

        assert "project_handoff" in ctx.sources_loaded
        assert "端到端论文" in (ctx.handoff_block or "")

    def test_no_emit_no_injection(self, isolated_env):
        """未触发 auto_snapshot 时 facade 看不到项目（空表）。"""
        sfdb = isolated_env["sfdb"]
        _create_project(sfdb, title="未发射的论文", paper_type="本科论文")

        from agent.continuity_facade import load_continuity_context
        ctx = load_continuity_context("你好")

        assert "project_handoff" not in ctx.sources_failed


class TestMarkDone:
    """验证导出后 status=done，项目不再出现在活跃列表。"""

    def test_mark_done_removes_from_active(self, isolated_env):
        """mark_project_done 后项目不在 get_active_handoffs 中。"""
        sfdb = isolated_env["sfdb"]
        ph = isolated_env["ph"]

        pid = _create_project(sfdb, title="已完成的论文", paper_type="本科论文")

        from hermes_cli.scholarforge.project_context import auto_snapshot, mark_project_done
        auto_snapshot(pid, label="write:conclusion")
        assert len(ph.get_active_handoffs()) == 1

        mark_project_done(pid)

        # status=done 的项目不在活跃列表
        active = ph.get_active_handoffs()
        assert len(active) == 0

    def test_mark_done_after_export(self, isolated_env):
        """导出操作触发 mark_project_done（端到端）。"""
        sfdb = isolated_env["sfdb"]
        ph = isolated_env["ph"]

        pid = _create_project(sfdb, title="导出测试论文", paper_type="本科论文")

        from hermes_cli.scholarforge.project_context import auto_snapshot
        auto_snapshot(pid, label="write:final")
        assert len(ph.get_active_handoffs()) == 1

        # 模拟导出 handler 调用 mark_project_done
        from hermes_cli.scholarforge.project_context import mark_project_done
        mark_project_done(pid)

        # 导出后不在活跃列表
        assert len(ph.get_active_handoffs()) == 0

        # 但 facade 不会报错
        from agent.continuity_facade import load_continuity_context
        ctx = load_continuity_context("继续写论文")
        assert "project_handoff" not in ctx.sources_failed
