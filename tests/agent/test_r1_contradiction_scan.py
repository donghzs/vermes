"""R1 矛盾校核测试：验证 @decision 记忆两两比对能发现矛盾并写入 flags"""
import sqlite3
from unittest.mock import patch


class TestR1ContradictionScan:
    """R1: 矛盾校核"""

    def _setup_db(self, tmp_path, monkeypatch, decisions):
        """初始化测试 DB：建表 + 写入 @decision 记忆"""
        from agent.memory_reflection import ensure_reflection_schema
        from agent import memory_fabric

        test_db = tmp_path / "test.db"

        # 建 memories 表
        conn = sqlite3.connect(str(test_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                layer TEXT NOT NULL,
                type TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT '',
                pointer TEXT NOT NULL,
                fts_content TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                lifecycle_tag TEXT NOT NULL DEFAULT 'reference'
            )
        """)
        # 建 memory_flags 表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL,
                flag_type TEXT NOT NULL,
                confidence REAL DEFAULT 0.0,
                evidence TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                source TEXT DEFAULT 'reflection'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_flags_status ON memory_flags(status, created_at)")

        # 写入 memories 数据
        for i, content in enumerate(decisions):
            conn.execute(
                """INSERT INTO memories
                   (source, layer, type, scope, pointer, fts_content, updated_at, access_count, lifecycle_tag)
                   VALUES ('test', 'L3', 'fact', 'global', ?, ?, '2026-07-23T00:00:00Z', 0, 'decision')""",
                (f"ptr_{i}", content),
            )
        conn.commit()
        conn.close()

        # patch _get_index_db 指向测试 DB
        monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: test_db)
        return test_db

    def test_no_contradiction_single_decision(self, tmp_path, monkeypatch):
        """只有一条 @decision 记忆时跳过"""
        self._setup_db(tmp_path, monkeypatch, ["使用 Python 3.11 作为主版本"])
        from agent.memory_reflection import _scan_contradictions

        flags = _scan_contradictions()
        assert flags == 0

    def test_no_contradiction_unrelated_decisions(self, tmp_path, monkeypatch):
        """两条不相关的 @decision 记忆不产生 flag"""
        self._setup_db(tmp_path, monkeypatch, [
            "使用 Python 3.11 作为主版本",
            "前端框架选择 Vue3",
        ])
        from agent.memory_reflection import _scan_contradictions

        flags = _scan_contradictions()
        assert flags == 0

    def test_contradiction_detected(self, tmp_path, monkeypatch):
        """两条矛盾的 @decision 记忆产生 flag"""
        self._setup_db(tmp_path, monkeypatch, [
            "不再使用 Python，改用 Go",
            "使用 Python 3.11 作为主版本",
        ])
        from agent.memory_reflection import _scan_contradictions, get_open_flags

        flags_created = _scan_contradictions()
        assert flags_created >= 1

        # 验证 flag 写入
        open_flags = get_open_flags()
        contradiction_flags = [f for f in open_flags if f["flag_type"] == "contradiction"]
        assert len(contradiction_flags) >= 1
        assert "矛盾" in contradiction_flags[0]["evidence"]

    def test_contradiction_deduplication(self, tmp_path, monkeypatch):
        """重复运行不产生重复 flag"""
        self._setup_db(tmp_path, monkeypatch, [
            "不再使用 Python，改用 Go",
            "使用 Python 3.11 作为主版本",
        ])
        from agent.memory_reflection import _scan_contradictions, get_open_flags

        # 第一次扫描
        _scan_contradictions()
        first_count = len(get_open_flags())

        # 第二次扫描
        _scan_contradictions()
        second_count = len(get_open_flags())

        # 应该没有新增（去重）
        assert second_count == first_count

    def test_non_decision_memories_ignored(self, tmp_path, monkeypatch):
        """非 @decision 标签的记忆不参与矛盾校核"""
        from agent.memory_reflection import _scan_contradictions, get_open_flags
        from agent import memory_fabric

        test_db = tmp_path / "test.db"

        # 建 memories + memory_flags 表
        conn = sqlite3.connect(str(test_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL, layer TEXT NOT NULL, type TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT '', pointer TEXT NOT NULL,
                fts_content TEXT NOT NULL, updated_at TEXT NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                lifecycle_tag TEXT NOT NULL DEFAULT 'reference'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL, flag_type TEXT NOT NULL,
                confidence REAL DEFAULT 0.0, evidence TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL, source TEXT DEFAULT 'reflection'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_flags_status ON memory_flags(status, created_at)")

        # 写入一条 @decision 和一条 @reference
        conn.execute(
            """INSERT INTO memories
               (source, layer, type, scope, pointer, fts_content, updated_at, access_count, lifecycle_tag)
               VALUES ('test', 'L3', 'fact', 'global', 'ptr_0', '不再使用 Python，改用 Go', '2026-07-23T00:00:00Z', 0, 'decision')"""
        )
        conn.execute(
            """INSERT INTO memories
               (source, layer, type, scope, pointer, fts_content, updated_at, access_count, lifecycle_tag)
               VALUES ('test', 'L3', 'fact', 'global', 'ptr_1', '使用 Python 3.11 作为主版本', '2026-07-23T00:00:00Z', 0, 'reference')"""
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: test_db)

        # 只有 1 条 @decision，不会触发比对
        flags = _scan_contradictions()
        assert flags == 0

    def test_memories_table_not_modified(self, tmp_path, monkeypatch):
        """R1 只读 memories，不修改"""
        self._setup_db(tmp_path, monkeypatch, [
            "不再使用 Python，改用 Go",
            "使用 Python 3.11 作为主版本",
        ])

        # 记录原始数据
        from agent import memory_fabric
        test_db = memory_fabric._get_index_db()
        conn = sqlite3.connect(str(test_db))
        before = conn.execute("SELECT id, fts_content FROM memories").fetchall()
        conn.close()

        from agent.memory_reflection import _scan_contradictions
        _scan_contradictions()

        # 验证 memories 表未被修改
        conn = sqlite3.connect(str(test_db))
        after = conn.execute("SELECT id, fts_content FROM memories").fetchall()
        conn.close()

        assert before == after
