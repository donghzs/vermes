"""Tests for event_time recency weighting."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from agent.memory_fabric import (
    _get_index_db, _init_db, index_note,
    recall_hierarchical, _apply_recency_weight,
    _RECENCY_7D, _RECENCY_30D, _RECENCY_90D, _RECENCY_OLD,
)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "memory_index.db"
    monkeypatch.setattr("agent.memory_fabric._get_index_db", lambda: db_path)
    _init_db(db_path)
    return db_path


class TestApplyRecencyWeight:

    def test_7d_weight(self):
        now = datetime.now(timezone.utc).isoformat()
        hits = [{"content": "recent", "score": 1.0, "event_time": now}]
        result = _apply_recency_weight(hits)
        assert result[0]["score"] == pytest.approx(1.0 * _RECENCY_7D)

    def test_30d_weight(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        hits = [{"content": "medium", "score": 1.0, "event_time": ts}]
        result = _apply_recency_weight(hits)
        assert result[0]["score"] == pytest.approx(1.0 * _RECENCY_30D)

    def test_90d_weight(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        hits = [{"content": "old", "score": 1.0, "event_time": ts}]
        result = _apply_recency_weight(hits)
        assert result[0]["score"] == pytest.approx(1.0 * _RECENCY_90D)

    def test_very_old_weight(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        hits = [{"content": "ancient", "score": 1.0, "event_time": ts}]
        result = _apply_recency_weight(hits)
        assert result[0]["score"] == pytest.approx(1.0 * _RECENCY_OLD)

    def test_null_event_time_falls_back_to_updated_at(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        hits = [{"content": "fallback", "score": 2.0, "event_time": None, "updated_at": ts}]
        result = _apply_recency_weight(hits)
        assert result[0]["score"] == pytest.approx(2.0 * _RECENCY_7D)

    def test_null_both_defaults_old(self):
        hits = [{"content": "no timestamps", "score": 1.0, "event_time": None, "updated_at": None}]
        result = _apply_recency_weight(hits)
        assert result[0]["score"] == pytest.approx(1.0 * _RECENCY_OLD)

    def test_parse_failure_no_weight(self):
        hits = [{"content": "bad ts", "score": 1.0, "event_time": "not-a-date"}]
        result = _apply_recency_weight(hits)
        assert result[0]["score"] == 1.0  # unchanged

    def test_naive_datetime_treated_as_utc(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
        hits = [{"content": "naive", "score": 1.0, "event_time": ts}]
        result = _apply_recency_weight(hits)
        assert result[0]["score"] == pytest.approx(1.0 * _RECENCY_7D)


class TestEventTimeColumn:

    def test_column_exists_after_init(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        cols = [r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()]
        assert "event_time" in cols
        conn.close()

    def test_write_fills_event_time(self, tmp_db):
        index_note("test_target", "test content for event_time")
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT event_time FROM memories WHERE source='note'").fetchone()
        assert row[0] is not None
        assert len(row[0]) > 10  # ISO format
        conn.close()

    def test_existing_rows_backfilled(self, tmp_path, monkeypatch):
        """Existing rows without event_time get backfilled from updated_at."""
        db_path = tmp_path / "memory_index.db"
        # First init to create tables
        _init_db(db_path)
        # Insert a row without event_time (simulate pre-migration)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO memories(source, layer, type, pointer, fts_content, updated_at, lifecycle_tag) "
            "VALUES ('note', 'note', 'note_text', 'test#backfill', 'backfill test', '2026-07-01T00:00:00', 'reference')"
        )
        # Manually clear event_time to simulate pre-migration state
        conn.execute("UPDATE memories SET event_time = NULL WHERE pointer='test#backfill'")
        conn.commit()
        conn.close()

        # Simulate fresh process: drop schema_meta so migrations re-run
        conn2 = sqlite3.connect(str(db_path))
        conn2.execute("DELETE FROM schema_meta WHERE key='skill_demote_done'")
        conn2.execute("UPDATE memories SET event_time = NULL WHERE pointer='test#backfill'")
        conn2.commit()
        conn2.close()

        # Re-init triggers the backfill
        _init_db(db_path)

        conn3 = sqlite3.connect(str(db_path))
        row = conn3.execute("SELECT event_time FROM memories WHERE pointer='test#backfill'").fetchone()
        assert row[0] == '2026-07-01T00:00:00'  # backfilled from updated_at
        conn3.close()

    def test_recall_returns_event_time(self, tmp_db):
        index_note("recall_test", "unique recall test content")
        results = recall_hierarchical("unique recall test", limit=5)
        assert len(results) > 0
        assert results[0].get("event_time") is not None
