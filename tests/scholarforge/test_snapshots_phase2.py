"""ScholarForge Phase 2 — 版本快照测试

测试覆盖：
1. create_project_snapshot — 自动快照创建
2. restore_snapshot — 快照恢复
3. list_snapshots — 列表
4. get_snapshot — 详情
5. delete_snapshot — 删除
6. auto_snapshot — project_context 包装
7. MAX_SNAPSHOTS_PER_PROJECT 淘汰
8. restore 不存在的快照
"""
import os
import sys
import json
import tempfile
import time

import pytest

# 确保 scholarforge 可导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """使用临时 DB 路径。"""
    db_path = str(tmp_path / "test_snapshots.db")
    import vermes_cli.scholarforge.database as db
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    yield db


@pytest.fixture
def sample_project(tmp_db):
    """创建一个测试项目。"""
    result = tmp_db.create_project(
        title="快照测试论文",
        paper_type="本科论文",
        target_words=8000,
    )
    pid = result["id"]
    # 写入大纲
    tmp_db.save_outline(pid, [
        {"id": "intro", "number": "1", "title": "引言", "wordCount": 500},
        {"id": "method", "number": "2", "title": "方法", "wordCount": 1000},
    ])
    # 写入章节内容
    tmp_db.save_section_content(pid, "intro", "这是引言的初始内容。")
    tmp_db.save_section_content(pid, "method", "这是方法的内容。")
    return pid


class TestCreateSnapshot:
    def test_create_project_snapshot_basic(self, tmp_db, sample_project):
        """create_project_snapshot 应捕获完整项目状态。"""
        sid = tmp_db.create_project_snapshot(sample_project, label="v1", note="初始版本")
        assert sid > 0

        snap = tmp_db.get_snapshot(sid)
        assert snap is not None
        assert snap["label"] == "v1"
        assert snap["note"] == "初始版本"

        payload = snap["payload"]
        assert payload["title"] == "快照测试论文"
        assert payload["paper_type"] == "本科论文"
        assert len(payload["outline"]) == 2
        assert "intro" in payload["contents"]
        assert "method" in payload["contents"]

    def test_create_snapshot_empty_project(self, tmp_db):
        """create_snapshot 对不存在的项目返回 0。"""
        sid = tmp_db.create_project_snapshot(9999, label="empty")
        assert sid == 0


class TestRestoreSnapshot:
    def test_restore_snapshot_basic(self, tmp_db, sample_project):
        """restore_snapshot 应恢复大纲和章节内容。"""
        # 创建快照
        sid = tmp_db.create_project_snapshot(sample_project, label="v1")
        assert sid > 0

        # 修改项目内容
        tmp_db.save_section_content(sample_project, "intro", "修改后的引言内容。")
        tmp_db.save_outline(sample_project, [
            {"section_key": "new", "section_number": "1", "section_title": "新大纲", "word_count": 200},
        ])

        # 验证修改已生效
        proj = tmp_db.get_project(sample_project)
        assert len(proj["outline"]) == 1
        assert proj["contents"]["intro"] == "修改后的引言内容。"

        # 恢复快照
        result = tmp_db.restore_snapshot(sid)
        assert result["restored"] is True
        assert result["outline_sections"] == 2
        # content_sections 包含模板创建的空 section
        assert result["content_sections"] >= 2

        # 验证恢复
        proj = tmp_db.get_project(sample_project)
        assert len(proj["outline"]) == 2
        assert proj["contents"]["intro"] == "这是引言的初始内容。"
        assert proj["contents"]["method"] == "这是方法的内容。"

    def test_restore_nonexistent_snapshot(self, tmp_db):
        """restore_snapshot 对不存在的快照返回错误。"""
        result = tmp_db.restore_snapshot(9999)
        assert "error" in result


class TestListSnapshots:
    def test_list_snapshots_order(self, tmp_db, sample_project):
        """list_snapshots 应按时间倒序。"""
        sid1 = tmp_db.create_project_snapshot(sample_project, label="v1")
        time.sleep(1.1)
        sid2 = tmp_db.create_project_snapshot(sample_project, label="v2")
        time.sleep(1.1)
        sid3 = tmp_db.create_project_snapshot(sample_project, label="v3")

        snaps = tmp_db.list_snapshots(sample_project)
        assert len(snaps) == 3
        assert snaps[0]["label"] == "v3"
        assert snaps[2]["label"] == "v1"

    def test_list_snapshots_empty(self, tmp_db, sample_project):
        """list_snapshots 对无快照项目返回空列表。"""
        snaps = tmp_db.list_snapshots(sample_project)
        assert snaps == []


class TestDeleteSnapshot:
    def test_delete_snapshot(self, tmp_db, sample_project):
        """delete_snapshot 应删除指定快照。"""
        sid = tmp_db.create_project_snapshot(sample_project, label="v1")
        assert tmp_db.delete_snapshot(sid) is True

        snap = tmp_db.get_snapshot(sid)
        assert snap is None

    def test_delete_nonexistent_snapshot(self, tmp_db):
        """delete_snapshot 对不存在的快照不报错。"""
        assert tmp_db.delete_snapshot(9999) is True


class TestMaxSnapshots:
    def test_max_snapshots_eviction(self, tmp_db, sample_project, monkeypatch):
        """超出 MAX_SNAPSHOTS_PER_PROJECT 时应淘汰最旧。"""
        monkeypatch.setattr("vermes_cli.scholarforge.database.MAX_SNAPSHOTS_PER_PROJECT", 3)
        ids = []
        for i in range(5):
            sid = tmp_db.create_project_snapshot(sample_project, label=f"v{i}")
            ids.append(sid)
            time.sleep(0.05)

        snaps = tmp_db.list_snapshots(sample_project)
        assert len(snaps) == 3
        # 最旧的两个应被淘汰
        labels = [s["label"] for s in snaps]
        assert "v0" not in labels
        assert "v1" not in labels
        assert "v2" in labels
        assert "v3" in labels
        assert "v4" in labels


class TestProjectContextSnapshot:
    def test_auto_snapshot_wrapper(self, sample_project):
        """project_context.auto_snapshot 应创建快照。"""
        from vermes_cli.scholarforge.project_context import auto_snapshot
        sid = auto_snapshot(sample_project, label="test_auto", note="自动快照测试")
        assert sid > 0

    def test_auto_snapshot_invalid_project(self):
        """auto_snapshot 对无效项目返回 0。"""
        from vermes_cli.scholarforge.project_context import auto_snapshot
        sid = auto_snapshot(0, label="invalid")
        assert sid == 0

        sid = auto_snapshot(-1, label="invalid")
        assert sid == 0

    def test_restore_snapshot_wrapper(self, sample_project):
        """project_context.restore_snapshot 包装正常工作。"""
        from vermes_cli.scholarforge.project_context import auto_snapshot, restore_snapshot
        sid = auto_snapshot(sample_project, label="test_restore")
        result = restore_snapshot(sid)
        assert result.get("restored") is True

    def test_list_snapshots_wrapper(self, sample_project):
        """project_context.list_snapshots 包装正常工作。"""
        from vermes_cli.scholarforge.project_context import auto_snapshot, list_snapshots
        auto_snapshot(sample_project, label="test_list")
        snaps = list_snapshots(sample_project)
        assert len(snaps) >= 1

    def test_delete_snapshot_wrapper(self, sample_project):
        """project_context.delete_snapshot 包装正常工作。"""
        from vermes_cli.scholarforge.project_context import auto_snapshot, delete_snapshot
        sid = auto_snapshot(sample_project, label="test_delete")
        assert delete_snapshot(sid) is True

    def test_get_snapshot_detail_wrapper(self, sample_project):
        """project_context.get_snapshot_detail 包装正常工作。"""
        from vermes_cli.scholarforge.project_context import auto_snapshot, get_snapshot_detail
        sid = auto_snapshot(sample_project, label="test_get")
        snap = get_snapshot_detail(sid)
        assert snap.get("label") == "test_get"
