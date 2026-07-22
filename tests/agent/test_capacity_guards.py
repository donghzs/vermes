"""Tests for B-route memory hard capacity guards.

铁律红线验证：
  - 绝不物理删除 memories 行
  - 绝不 LLM 改写事实内容
  - 超阈值时只降级（降 limit、跳冷层）

Covers:
  - memory_fabric._check_capacity / _get_memory_count
  - memory_fabric.recall capacity degradation
  - cross_session_continuity snapshot trimming
  - compression_scheduler compression_exhausted
"""

import os
import sys
import tempfile
import sqlite3
import pytest

# Ensure project root on path
_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _proj not in sys.path:
    sys.path.insert(0, _proj)

from agent.memory_fabric import (
    _MAX_MEMORIES_TOTAL,
    _COLD_ACCESS_THRESHOLD,
    _check_capacity,
    _get_memory_count,
    recall,
    index_note,
    record,
    L1_NOTE,
    L3_EPISODIC,
)
from agent.compression_scheduler import CompressionScheduler


# ── memory_fabric capacity ───────────────────────────────────────────────


class TestMemoryFabricCapacity:
    """Test hard capacity guards on memory_fabric."""

    def test_capacity_check_under_limit(self, tmp_path, monkeypatch):
        """Under limit → over_capacity=False, limit_scale=1.0."""
        monkeypatch.setattr(
            "agent.memory_fabric._get_index_db",
            lambda: tmp_path / "test_index.db"
        )
        # Init DB with a few rows
        from agent.memory_fabric import _init_db
        _init_db(tmp_path / "test_index.db")

        # Insert some memories
        for i in range(10):
            index_note(f"note_{i}", f"content about topic {i}")

        cap = _check_capacity()
        assert not cap["over_capacity"]
        assert cap["limit_scale"] == 1.0
        assert not cap["skip_cold"]
        assert cap["total_count"] == 10

    def test_capacity_check_over_limit(self, tmp_path, monkeypatch):
        """Over limit → over_capacity=True, limit_scale<1.0, skip_cold=True."""
        monkeypatch.setattr(
            "agent.memory_fabric._get_index_db",
            lambda: tmp_path / "test_index.db"
        )
        monkeypatch.setattr("agent.memory_fabric._MAX_MEMORIES_TOTAL", 5)

        from agent.memory_fabric import _init_db
        _init_db(tmp_path / "test_index.db")

        for i in range(10):
            index_note(f"note_{i}", f"content about topic {i}")

        cap = _check_capacity()
        assert cap["over_capacity"]
        assert cap["limit_scale"] < 1.0
        assert cap["skip_cold"]
        assert cap["total_count"] == 10

    def test_recall_degrades_over_capacity(self, tmp_path, monkeypatch):
        """When over capacity, recall skips cold entries and reduces limit."""
        monkeypatch.setattr(
            "agent.memory_fabric._get_index_db",
            lambda: tmp_path / "test_index.db"
        )
        monkeypatch.setattr("agent.memory_fabric._MAX_MEMORIES_TOTAL", 3)
        monkeypatch.setattr("agent.memory_fabric._COLD_ACCESS_THRESHOLD", 0)

        from agent.memory_fabric import _init_db
        _init_db(tmp_path / "test_index.db")

        # Insert 5 memories — all with access_count=0 (cold)
        for i in range(5):
            index_note(f"note_{i}", f"unique content {i} topic")

        # Over capacity → all cold → recall returns []
        results = recall("unique content", limit=5)
        assert len(results) == 0  # All skipped because access_count <= threshold

    def test_recall_returns_warm_over_capacity(self, tmp_path, monkeypatch):
        """Over capacity but some entries are warm → return only warm ones."""
        monkeypatch.setattr(
            "agent.memory_fabric._get_index_db",
            lambda: tmp_path / "test_index.db"
        )
        monkeypatch.setattr("agent.memory_fabric._MAX_MEMORIES_TOTAL", 3)
        monkeypatch.setattr("agent.memory_fabric._COLD_ACCESS_THRESHOLD", 2)

        from agent.memory_fabric import _init_db, _get_conn
        _init_db(tmp_path / "test_index.db")

        # Insert 5 memories
        for i in range(5):
            index_note(f"note_{i}", f"unique topic {i} content")

        # Warm up first 2 (access_count > threshold)
        with _get_conn(str(tmp_path / "test_index.db")) as conn:
            conn.execute(
                "UPDATE memories SET access_count = 5 WHERE source IN ('note_0', 'note_1')"
            )
            conn.commit()

        results = recall("unique topic", limit=5)
        # Only warm entries returned (2), limit scaled down
        assert len(results) <= 2
        for r in results:
            assert r["access_count"] > 2

    def test_no_physical_deletion(self, tmp_path, monkeypatch):
        """铁律：recall 永远不删除 memories 行。"""
        monkeypatch.setattr(
            "agent.memory_fabric._get_index_db",
            lambda: tmp_path / "test_index.db"
        )
        monkeypatch.setattr("agent.memory_fabric._MAX_MEMORIES_TOTAL", 2)

        from agent.memory_fabric import _init_db, _get_conn
        _init_db(tmp_path / "test_index.db")

        for i in range(5):
            index_note(f"note_{i}", f"topic {i} content")

        count_before = _get_memory_count()
        assert count_before == 5

        # Call recall multiple times
        recall("topic", limit=10)
        recall("topic", limit=10)
        recall("topic", limit=10)

        count_after = _get_memory_count()
        assert count_after == 5  # 铁律：行数不变

    def test_get_memory_count_empty(self, tmp_path, monkeypatch):
        """_get_memory_count returns 0 for missing DB."""
        monkeypatch.setattr(
            "agent.memory_fabric._get_index_db",
            lambda: tmp_path / "nonexistent.db"
        )
        assert _get_memory_count() == 0


# ── cross_session_continuity snapshot trimming ───────────────────────────


class TestSnapshotTrimming:
    """Test snapshot capacity trimming."""

    def test_snapshot_trim_over_limit(self, tmp_path, monkeypatch):
        """Snapshots exceeding _MAX_SNAPSHOTS get trimmed (oldest first)."""
        monkeypatch.setattr("agent.cross_session_continuity._MAX_SNAPSHOTS", 3)

        from agent.cross_session_continuity import CrossSessionContinuity

        csc = CrossSessionContinuity(str(tmp_path / "continuity.db"))
        csc.ensure_tables()

        # Save 5 snapshots
        for i in range(5):
            csc.save_snapshot(f"session_{i}")

        # Verify only 3 remain
        conn = sqlite3.connect(str(tmp_path / "continuity.db"))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cluster_snapshots")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 3

    def test_snapshot_no_trim_under_limit(self, tmp_path, monkeypatch):
        """Under limit → no trimming."""
        monkeypatch.setattr("agent.cross_session_continuity._MAX_SNAPSHOTS", 10)

        from agent.cross_session_continuity import CrossSessionContinuity

        csc = CrossSessionContinuity(str(tmp_path / "continuity.db"))
        csc.ensure_tables()

        for i in range(3):
            csc.save_snapshot(f"session_{i}")

        conn = sqlite3.connect(str(tmp_path / "continuity.db"))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cluster_snapshots")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 3


# ── compression_scheduler capacity ──────────────────────────────────────


class TestCompressionExhausted:
    """Test compression round limit."""

    def test_not_exhausted_initially(self):
        scheduler = CompressionScheduler(provider="anthropic")
        assert not scheduler.compression_exhausted()

    def test_exhausted_after_max_rounds(self):
        scheduler = CompressionScheduler(provider="anthropic")
        scheduler._max_compression_rounds = 3

        for _ in range(3):
            scheduler.record_compression()

        assert scheduler.compression_exhausted()

    def test_not_exhausted_below_max(self):
        scheduler = CompressionScheduler(provider="anthropic")
        scheduler._max_compression_rounds = 5

        for _ in range(3):
            scheduler.record_compression()

        assert not scheduler.compression_exhausted()

    def test_compression_rounds_count(self):
        scheduler = CompressionScheduler(provider="anthropic")
        assert scheduler._compression_rounds == 0

        scheduler.record_compression()
        assert scheduler._compression_rounds == 1

        scheduler.record_compression()
        assert scheduler._compression_rounds == 2


# ── continuity_facade capacity_status ────────────────────────────────────


class TestContinuityFacadeCapacity:
    """Test capacity_status field in ContinuityContext."""

    def test_capacity_status_populated(self, tmp_path, monkeypatch):
        """load_continuity_context populates capacity_status."""
        monkeypatch.setattr(
            "agent.memory_fabric._get_index_db",
            lambda: tmp_path / "test_index.db"
        )
        from agent.memory_fabric import _init_db
        _init_db(tmp_path / "test_index.db")

        # Patch all 4 sources to avoid real DB dependencies
        monkeypatch.setattr(
            "agent.session_handoff.load_handoff_for_new_session",
            lambda msg: None,
            raising=False
        )
        monkeypatch.setattr(
            "agent.evolution_injector.load_and_format_evolution",
            lambda msg: "",
            raising=False
        )
        monkeypatch.setattr(
            "agent.memory_recall.load_and_format_recall",
            lambda msg: "",
            raising=False
        )
        monkeypatch.setattr(
            "agent.cross_session_continuity.get_continuity_prompt",
            lambda db: "",
            raising=False
        )

        from agent.continuity_facade import load_continuity_context
        ctx = load_continuity_context("test message")

        assert "total_memories" in ctx.capacity_status
        assert "over_capacity" in ctx.capacity_status
        assert isinstance(ctx.capacity_status.get("over_capacity"), bool)
