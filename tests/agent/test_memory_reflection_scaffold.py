"""R0 脚手架测试：建表 + 空闲门控 + 状态持久化"""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestReflectionScaffold:
    """R0: 脚手架验证"""

    def test_ensure_reflection_schema_creates_table(self, tmp_path, monkeypatch):
        """建表成功：memory_flags 表 + 索引"""
        from agent.memory_reflection import ensure_reflection_schema

        test_db = tmp_path / "test.db"
        ensure_reflection_schema(test_db)

        conn = sqlite3.connect(str(test_db))
        try:
            # 验证表存在
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_flags'"
            ).fetchall()
            assert len(tables) == 1, "memory_flags table should exist"

            # 验证索引存在
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_flags_status'"
            ).fetchall()
            assert len(indexes) == 1, "idx_flags_status index should exist"

            # 验证列定义
            cols = conn.execute("PRAGMA table_info(memory_flags)").fetchall()
            col_names = [c[1] for c in cols]
            assert "id" in col_names
            assert "memory_id" in col_names
            assert "flag_type" in col_names
            assert "confidence" in col_names
            assert "evidence" in col_names
            assert "status" in col_names
            assert "created_at" in col_names
            assert "source" in col_names
        finally:
            conn.close()

    def test_ensure_reflection_schema_idempotent(self, tmp_path):
        """幂等性：重复调用不报错"""
        from agent.memory_reflection import ensure_reflection_schema

        test_db = tmp_path / "test.db"

        # 第一次调用
        ensure_reflection_schema(test_db)

        # 第二次调用（幂等）
        ensure_reflection_schema(test_db)

        # 验证表仍只有一个
        conn = sqlite3.connect(str(test_db))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_flags'"
        ).fetchall()
        conn.close()
        assert len(tables) == 1

    def test_state_persistence(self, tmp_path, monkeypatch):
        """状态持久化：.reflection_state 文件"""
        from agent.memory_reflection import _load_state, _save_state, _reflection_state_path

        # Mock Vermes home
        monkeypatch.setattr(
            "agent.memory_reflection.get_vermes_home",
            lambda: tmp_path
        )

        # 初始状态
        state = _load_state()
        assert state["last_run_at"] is None
        assert state["paused"] is False

        # 更新状态
        state["last_run_at"] = "2026-07-23T00:00:00Z"
        state["paused"] = True
        _save_state(state)

        # 重新加载
        state2 = _load_state()
        assert state2["last_run_at"] == "2026-07-23T00:00:00Z"
        assert state2["paused"] is True

        # 验证文件存在
        state_file = _reflection_state_path()
        assert state_file.exists()

    def test_maybe_run_reflection_skips_when_paused(self, tmp_path, monkeypatch):
        """暂停状态：maybe_run_reflection 跳过"""
        from agent.memory_reflection import maybe_run_reflection, _save_state

        monkeypatch.setattr(
            "agent.memory_reflection.get_vermes_home",
            lambda: tmp_path
        )

        # 设置暂停
        _save_state({"last_run_at": None, "paused": True, "last_summary": ""})

        # Mock run_reflection_review
        with patch("agent.memory_reflection.run_reflection_review") as mock_run:
            maybe_run_reflection()
            mock_run.assert_not_called()

    def test_maybe_run_reflection_runs_when_idle(self, tmp_path, monkeypatch):
        """空闲状态：maybe_run_reflection 执行"""
        from agent.memory_reflection import maybe_run_reflection, _save_state

        monkeypatch.setattr(
            "agent.memory_reflection.get_vermes_home",
            lambda: tmp_path
        )

        # 取消暂停
        _save_state({"last_run_at": None, "paused": False, "last_summary": ""})

        # Mock 空闲检测和实际执行
        with patch("agent.memory_reflection._is_idle_enough", return_value=True):
            with patch("agent.memory_reflection.run_reflection_review") as mock_run:
                maybe_run_reflection()
                mock_run.assert_called_once()

    def test_maybe_run_reflection_fail_open(self, tmp_path, monkeypatch):
        """fail-open：反思失败不影响主会话"""
        from agent.memory_reflection import maybe_run_reflection, _save_state

        monkeypatch.setattr(
            "agent.memory_reflection.get_vermes_home",
            lambda: tmp_path
        )

        _save_state({"last_run_at": None, "paused": False, "last_summary": ""})

        # Mock 执行失败
        with patch("agent.memory_reflection._is_idle_enough", return_value=True):
            with patch("agent.memory_reflection.run_reflection_review", side_effect=Exception("boom")):
                # 不应抛异常
                try:
                    maybe_run_reflection()
                except:
                    assert False, "should not raise (fail-open)"

    def test_write_flag_success(self, tmp_path, monkeypatch):
        """写入 flag 成功"""
        from agent.memory_reflection import write_flag, ensure_reflection_schema
        from agent import memory_fabric

        test_db = tmp_path / "test.db"
        ensure_reflection_schema(test_db)

        monkeypatch.setattr(
            memory_fabric,
            "_get_index_db",
            lambda: test_db
        )

        flag_id, is_new = write_flag(
            memory_id="mem_123",
            flag_type="contradiction",
            evidence="Test contradiction",
            confidence=0.9
        )

        assert flag_id > 0
        assert is_new is True

        # 验证数据库
        conn = sqlite3.connect(str(test_db))
        row = conn.execute(
            "SELECT memory_id, flag_type, confidence, evidence, status FROM memory_flags WHERE id=?",
            (flag_id,)
        ).fetchone()
        conn.close()

        assert row[0] == "mem_123"
        assert row[1] == "contradiction"
        assert row[2] == 0.9
        assert row[3] == "Test contradiction"
        assert row[4] == "open"

    def test_write_flag_deduplication(self, tmp_path, monkeypatch):
        """flag 去重：同 memory_id + flag_type 已 open 则跳过"""
        from agent.memory_reflection import write_flag, ensure_reflection_schema
        from agent import memory_fabric

        test_db = tmp_path / "test.db"
        ensure_reflection_schema(test_db)

        monkeypatch.setattr(
            memory_fabric,
            "_get_index_db",
            lambda: test_db
        )

        # 第一次写入
        id1, is_new1 = write_flag("mem_123", "contradiction", "Test 1", 0.8)
        assert is_new1 is True

        # 第二次写入（同 memory_id + flag_type）
        id2, is_new2 = write_flag("mem_123", "contradiction", "Test 2", 0.9)
        assert is_new2 is False

        # 应返回相同 ID（已存在）
        assert id1 == id2

        # 验证数据库只有一条
        conn = sqlite3.connect(str(test_db))
        count = conn.execute(
            "SELECT COUNT(*) FROM memory_flags WHERE memory_id='mem_123' AND flag_type='contradiction'"
        ).fetchone()[0]
        conn.close()
        assert count == 1

    def test_get_open_flags(self, tmp_path, monkeypatch):
        """获取 open flags"""
        from agent.memory_reflection import write_flag, get_open_flags, ensure_reflection_schema
        from agent import memory_fabric

        test_db = tmp_path / "test.db"
        ensure_reflection_schema(test_db)

        monkeypatch.setattr(
            memory_fabric,
            "_get_index_db",
            lambda: test_db
        )

        # 写入 3 条 flags
        write_flag("mem_1", "contradiction", "Test 1", 0.8)
        write_flag("mem_2", "outdated", "Test 2", 0.7)
        write_flag("mem_3", "duplicate", "Test 3", 0.9)

        # 获取 open flags
        flags = get_open_flags()
        assert len(flags) == 3
        # 所有返回的 flag 都是 open（WHERE status='open'）

    def test_format_flags_for_context(self):
        """格式化 flags 供注入"""
        from agent.memory_reflection import format_flags_for_context

        flags = [
            {"id": 1, "memory_id": "mem_1", "flag_type": "contradiction", "confidence": 0.8, "evidence": "Test", "created_at": "2026-07-23T00:00:00Z"},
            {"id": 2, "memory_id": "mem_2", "flag_type": "outdated", "confidence": 0.7, "evidence": "Test", "created_at": "2026-07-23T00:00:00Z"},
        ]

        output = format_flags_for_context(flags)
        assert "[Reflection] 潜在记忆问题：" in output
        assert "矛盾: 1 条" in output
        assert "过时: 1 条" in output
        assert "/resolve_flag" in output
