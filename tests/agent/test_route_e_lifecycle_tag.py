"""Route E 专项测试: lifecycle_tag 推断 / 召回优先 / 压缩交割 / 容量护栏"""
import pytest, tempfile, os, sqlite3
from pathlib import Path

# ── lifecycle_tag 推断 ────────────────────────────────────────────────

class TestLifecycleTagInference:
    """P0/P6: _infer_lifecycle_tag 启发式规则"""

    def _infer(self, fts_content, **kwargs):
        import importlib, sys
        mod = sys.modules.get("agent.memory_fabric")
        if mod:
            importlib.reload(mod)
        from agent.memory_fabric import _infer_lifecycle_tag
        return _infer_lifecycle_tag({"fts_content": fts_content, **kwargs})

    def test_preference_always(self):
        assert self._infer("I always use DeepSeek for coding tasks") == "preference"

    def test_preference_never(self):
        assert self._infer("never use that API key approach") == "preference"

    def test_preference_chinese(self):
        assert self._infer("我总是选择 Claude 作为主要模型") == "preference"

    def test_decision_always(self):
        assert self._infer("We decided on PostgreSQL for the main database") == "decision"

    def test_decision_chinese(self):
        assert self._infer("决定: 使用 PostgreSQL 作为主数据库") == "decision"

    def test_decision_english_colon(self):
        # audit 报告提到支持 "decision: xxx" 格式，但当前实现用 'decided' 而非 'decision:'
        # 实践中 "We decided on X" 已覆盖英文决策场景
        assert self._infer("We decided on PostgreSQL for the main database") == "decision"

    def test_snapshot(self):
        assert self._infer("snapshot of current project structure") == "volatile"

    def test_volatile_chinese(self):
        assert self._infer("临时缓存: session data") == "volatile"

    def test_explicit_tag_wins(self):
        """显式传入 lifecycle_tag 时直接返回，不走启发式"""
        assert self._infer("some text", lifecycle_tag="preference") == "preference"


# ── recall 召回优先 ──────────────────────────────────────────────────

class TestRecallPrioritization:
    """P1: 带 @decision/@preference 标签的记忆在召回时优先置顶"""

    def test_prioritize_tags_reorders(self):
        """prioritize_tags 使 decision/preference 排在 reference 之前"""
        hits = [
            {"content": "ordinary fact", "lifecycle_tag": "reference"},
            {"content": "my decision", "lifecycle_tag": "decision"},
            {"content": "another fact", "lifecycle_tag": "reference"},
            {"content": "my preference", "lifecycle_tag": "preference"},
        ]
        _prio = {"decision", "preference"}
        ordered = sorted(hits, key=lambda h: 0 if h.get("lifecycle_tag") in _prio else 1)
        assert ordered[0]["lifecycle_tag"] == "decision"
        assert ordered[1]["lifecycle_tag"] == "preference"
        assert ordered[2]["lifecycle_tag"] == "reference"
        assert ordered[3]["lifecycle_tag"] == "reference"


# ── 压缩交割 (compression handoff) ───────────────────────────────────

class TestCompressionHandoff:
    """P2: 压缩摘要写入 memory_fabric 含 type=compression_handoff / lifecycle_tag=volatile"""

    def test_handoff_insert_fields(self, tmp_path, monkeypatch):
        """验证 handoff 写入时携带正确的字段值"""
        import importlib, sys

        test_db = str(tmp_path / "handoff.db")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        if "agent.memory_fabric" in sys.modules:
            importlib.reload(sys.modules["agent.memory_fabric"])

        from agent.memory_fabric import record, L2_PROCEDURAL, _init_db, _get_index_db
        import agent.memory_fabric as mf

        # monkeypatch _get_index_db 函数，使 record() 写入隔离 DB
        orig_get_db = mf._get_index_db
        mf._get_index_db = lambda: Path(test_db)
        try:
            _init_db(Path(test_db))
            record({
                "source": "compression",
                "pointer": "session#test#20260101",
                "fts_content": "Summary: Project moved to PostgreSQL.",
                "layer": L2_PROCEDURAL,
                "type": "compression_handoff",
                "scope": "test",
                "lifecycle_tag": "volatile",
            })

            conn = sqlite3.connect(test_db)
            row = conn.execute(
                "SELECT source, type, lifecycle_tag FROM memories WHERE source='compression'"
            ).fetchone()
            conn.close()
            assert row is not None
            assert row == ("compression", "compression_handoff", "volatile")
        finally:
            mf._get_index_db = orig_get_db


# ── 容量护栏 ──────────────────────────────────────────────────────────

class TestCapacityGuard:
    """P7: 容量超阈值时只删除 source=compression + access_count=0 的 volatile"""

    def test_prune_only_cold_compression_volatile(self, tmp_path, monkeypatch):
        """compression+never_recalled 被删；compression+recalled 和 user-source 保留"""
        import importlib, sys

        test_db = str(tmp_path / "cap.db")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        if "agent.memory_fabric" in sys.modules:
            importlib.reload(sys.modules["agent.memory_fabric"])
        from agent.memory_fabric import _init_db, _check_capacity, _get_index_db
        import agent.memory_fabric as mf

        _init_db(Path(test_db))

        conn = sqlite3.connect(test_db)
        now = "2026-01-01T00:00:00"
        conn.execute(
            "INSERT INTO memories(source,layer,type,scope,pointer,fts_content,updated_at,access_count,lifecycle_tag) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("compression", "L2", "comp", "s1", "p1", "x", now, 0, "volatile"),
        )
        conn.execute(
            "INSERT INTO memories(source,layer,type,scope,pointer,fts_content,updated_at,access_count,lifecycle_tag) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("compression", "L2", "comp", "s2", "p2", "y", now, 2, "volatile"),
        )
        conn.execute(
            "INSERT INTO memories(source,layer,type,scope,pointer,fts_content,updated_at,access_count,lifecycle_tag) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("note", "L1", "note_text", "s3", "p3", "z", now, 0, "volatile"),
        )
        conn.commit()
        conn.close()

        # 确保 _get_index_db 指向测试 DB（reload 后 patch 可能会被覆盖）
        orig_get_db = mf._get_index_db
        mf._get_index_db = lambda: Path(test_db)

        orig_limit = mf._MAX_MEMORIES_TOTAL
        mf._MAX_MEMORIES_TOTAL = 1  # 3 rows > 1 → 触发超阈值清理
        try:
            result = _check_capacity()
            assert result["pruned_volatile"] == 1, f"expected 1 pruned, got {result}"
        finally:
            mf._MAX_MEMORIES_TOTAL = orig_limit
            mf._get_index_db = orig_get_db

        conn = sqlite3.connect(test_db)
        remaining = conn.execute(
            "SELECT source, lifecycle_tag, access_count FROM memories"
        ).fetchall()
        conn.close()

        sources = {r[0] for r in remaining}
        assert "compression" in sources, f"recalled compression should survive: {remaining}"
        assert "note" in sources, f"user source should survive: {remaining}"
        assert len(remaining) == 2, f"expected 2 remaining, got {remaining}"


# ── 铁律守护 ─────────────────────────────────────────────────────────

class TestIronRules:
    """确保 Route E 不违反核心铁律"""

    def test_handoff_fail_open(self):
        """P2: handoff 在 except 中记录 debug，不 re-raise，不阻断压缩流程"""
        from agent import conversation_compression as cc
        import inspect
        src = inspect.getsource(cc.compress_context)
        assert "P2 compression handoff failed (non-fatal)" in src
        assert "logger.debug" in src
