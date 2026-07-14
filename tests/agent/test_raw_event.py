"""Tests for agent/raw_event.py — zero-classification event recording."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from agent.raw_event import (
    RawEvent,
    cleanup_raw_events,
    ensure_raw_events_table,
    get_raw_event_stats,
    get_recent_raw_events,
    get_unclustered_count,
    record_raw_event,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _insert_raw_row(conn, timestamp, tool_name, session_id, success=1, protected=0, cluster_id=None):
    conn.execute(
        """INSERT INTO raw_events
           (timestamp, tool_name, args_preview, result_preview, success, duration, session_id, cluster_id, protected)
           VALUES (?, ?, 'args', 'result', ?, 0.1, ?, ?, ?)""",
        (timestamp, tool_name, success, session_id, cluster_id, protected),
    )


def _mock_db(return_path):
    """Patch agent.evolution_manager.get_self_model_db to return return_path."""
    return mock.patch(
        "agent.evolution_manager.get_self_model_db",
        return_value=Path(return_path),
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def conn(temp_db):
    c = sqlite3.connect(temp_db)
    c.execute("PRAGMA journal_mode=WAL")
    ensure_raw_events_table(c)
    return c


# ── RawEvent Dataclass ────────────────────────────────────────────────────────

class TestRawEvent:
    def test_basic_creation(self):
        event = RawEvent(
            timestamp="2026-07-13T19:00:00.123",
            tool_name="terminal",
            args_preview='{"command": "ls -la"}',
            result_preview="drwxr-xr-x  3 user staff ...",
            success=True,
            duration=0.5,
            session_id="sess-001",
            turn_number=3,
        )
        assert event.tool_name == "terminal"
        assert event.success is True
        assert event.cluster_id is None
        assert event.protected is False

    def test_defaults(self):
        event = RawEvent(
            timestamp="2026-07-13T19:00:00",
            tool_name="web_search",
            args_preview="",
            result_preview="",
            success=True,
            duration=0.0,
            session_id="sess-002",
        )
        assert event.turn_number == 0
        assert event.cluster_id is None
        assert event.embedding_id is None
        assert event.protected is False

    def test_to_db_row(self):
        event = RawEvent(
            timestamp="2026-07-13T19:00:00",
            tool_name="read_file",
            args_preview='{"path": "/tmp/test.py"}',
            result_preview="print('hello')",
            success=True,
            duration=0.1,
            session_id="sess-003",
            turn_number=1,
            cluster_id=5,
            protected=True,
        )
        row = event.to_db_row()
        assert row[0] == "2026-07-13T19:00:00"
        assert row[1] == "read_file"
        assert row[6] == "sess-003"
        assert row[7] == 1
        assert row[8] == 5
        assert row[10] == 1

    def test_from_db_row(self, conn):
        conn.execute(
            """INSERT INTO raw_events
               (timestamp, tool_name, args_preview, result_preview, success,
                duration, session_id, turn_number, cluster_id, embedding_id, protected)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("2026-07-13T19:00:00", "write_file", "{path}", "ok", 1, 0.2, "sess-004", 10, None, None, 0),
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM raw_events LIMIT 1").fetchone()
        event = RawEvent.from_db_row(row)
        assert event.tool_name == "write_file"
        assert event.session_id == "sess-004"
        assert event.turn_number == 10
        assert event.success is True


# ── Table Management ──────────────────────────────────────────────────────────

class TestRawEventsTable:
    def test_ensure_table_creates(self, temp_db):
        c = sqlite3.connect(temp_db)
        ensure_raw_events_table(c)
        tables = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_events'"
        ).fetchall()
        assert len(tables) == 1

    def test_ensure_table_idempotent(self, temp_db):
        c = sqlite3.connect(temp_db)
        ensure_raw_events_table(c)
        ensure_raw_events_table(c)
        tables = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_events'"
        ).fetchall()
        assert len(tables) == 1

    def test_table_has_required_columns(self, conn):
        columns = {row[1] for row in conn.execute("PRAGMA table_info(raw_events)")}
        required = {
            "id", "timestamp", "tool_name", "args_preview", "result_preview",
            "success", "duration", "session_id", "turn_number",
            "cluster_id", "embedding_id", "protected",
        }
        assert required.issubset(columns)


# ── Write: record_raw_event ──────────────────────────────────────────────────

class TestRecordRawEvent:
    def test_record_basic_event(self, temp_db):
        with _mock_db(temp_db):
            rowid = record_raw_event(
                tool_name="terminal",
                tool_args={"command": "git status"},
                result="On branch main\nnothing to commit",
                is_error=False,
                duration=1.5,
                session_id="sess-001",
                turn_number=5,
            )
        assert rowid is not None and rowid > 0

        c = sqlite3.connect(temp_db)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM raw_events WHERE id = ?", (rowid,)).fetchone()
        assert row["tool_name"] == "terminal"
        assert row["success"] == 1
        assert row["duration"] == 1.5
        assert row["session_id"] == "sess-001"
        assert row["turn_number"] == 5
        assert row["args_preview"]
        assert row["result_preview"]
        assert row["cluster_id"] is None
        c.close()

    def test_record_error_event(self, temp_db):
        with _mock_db(temp_db):
            rowid = record_raw_event(
                tool_name="write_file",
                tool_args={"path": "/root/secret.txt", "content": "x"},
                result="Permission denied",
                is_error=True,
                duration=0.1,
                session_id="sess-002",
                turn_number=1,
            )
        assert rowid is not None

        c = sqlite3.connect(temp_db)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM raw_events WHERE id = ?", (rowid,)).fetchone()
        assert row["success"] == 0
        assert "Permission denied" in row["result_preview"]
        c.close()

    def test_record_truncates_long_previews(self, temp_db):
        long_result = "x" * 1000
        with _mock_db(temp_db):
            rowid = record_raw_event(
                tool_name="terminal",
                tool_args={"command": "cat large.txt"},
                result=long_result,
                is_error=False,
                duration=0.5,
                session_id="sess-003",
            )
        c = sqlite3.connect(temp_db)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM raw_events WHERE id = ?", (rowid,)).fetchone()
        assert len(row["result_preview"]) <= 500
        c.close()

    def test_multiple_events_different_sessions(self, temp_db):
        with _mock_db(temp_db):
            for i in range(5):
                record_raw_event(
                    tool_name="web_search",
                    tool_args={"query": f"test_{i}"},
                    result=f"Result {i}",
                    is_error=False,
                    duration=0.5,
                    session_id=f"sess-{i}",
                    turn_number=i,
                )
        c = sqlite3.connect(temp_db)
        count = c.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
        assert count == 5
        c.close()

    def test_empty_result_handled(self, temp_db):
        with _mock_db(temp_db):
            rowid = record_raw_event(
                tool_name="web_search",
                tool_args={},
                result="",
                is_error=False,
                duration=0.1,
                session_id="sess-001",
            )
        assert rowid is not None


# ── Query: get_recent_raw_events ─────────────────────────────────────────────

class TestGetRecentRawEvents:
    def test_all_recent(self, conn):
        ts = datetime.now().isoformat()
        for i in range(5):
            _insert_raw_row(conn, ts, "web_search", "sess-001")
        conn.commit()
        events = get_recent_raw_events(str(conn.execute("PRAGMA database_list").fetchone()[2]), limit=3)
        assert len(events) == 3
        assert all(isinstance(e, RawEvent) for e in events)

    def test_filter_by_session(self, conn):
        ts = datetime.now().isoformat()
        for i in range(3):
            _insert_raw_row(conn, ts, "terminal", "sess-A")
        _insert_raw_row(conn, ts, "terminal", "sess-B")
        conn.commit()
        events = get_recent_raw_events(
            str(conn.execute("PRAGMA database_list").fetchone()[2]),
            session_id="sess-A",
        )
        assert len(events) == 3
        assert all(e.session_id == "sess-A" for e in events)

    def test_filter_by_tool(self, conn):
        ts = datetime.now().isoformat()
        _insert_raw_row(conn, ts, "terminal", "sess-001")
        _insert_raw_row(conn, ts, "web_search", "sess-001")
        conn.commit()
        events = get_recent_raw_events(
            str(conn.execute("PRAGMA database_list").fetchone()[2]),
            tool_name="web_search",
        )
        assert len(events) == 1
        assert events[0].tool_name == "web_search"

    def test_empty_when_no_events(self, conn):
        events = get_recent_raw_events(
            str(conn.execute("PRAGMA database_list").fetchone()[2]),
        )
        assert events == []


# ── Query: get_unclustered_count ─────────────────────────────────────────────

class TestGetUnclusteredCount:
    def test_all_unclustered_initially(self, conn):
        ts = datetime.now().isoformat()
        for i in range(5):
            _insert_raw_row(conn, ts, "terminal", "sess-001")
        conn.commit()
        count = get_unclustered_count(
            str(conn.execute("PRAGMA database_list").fetchone()[2]),
        )
        assert count == 5

    def test_some_clustered(self, conn):
        ts = datetime.now().isoformat()
        for i in range(5):
            _insert_raw_row(conn, ts, "terminal", "sess-001")
        conn.commit()
        conn.execute("UPDATE raw_events SET cluster_id = 1 WHERE rowid IN (1, 2)")
        conn.commit()
        count = get_unclustered_count(
            str(conn.execute("PRAGMA database_list").fetchone()[2]),
        )
        assert count == 3


# ── Query: get_raw_event_stats ───────────────────────────────────────────────

class TestGetRawEventStats:
    def test_basic_stats(self, conn):
        ts = datetime.now().isoformat()
        for i in range(4):
            _insert_raw_row(conn, ts, "terminal", "sess-001", success=1)
        _insert_raw_row(conn, ts, "write_file", "sess-001", success=0)
        conn.commit()
        stats = get_raw_event_stats(
            str(conn.execute("PRAGMA database_list").fetchone()[2]),
        )
        assert stats["total"] == 5
        assert stats["success_count"] == 4
        assert stats["error_count"] == 1
        assert stats["success_rate"] == 80.0
        assert stats["sessions"] == 1
        assert len(stats["top_tools"]) == 2

    def test_empty_db(self, conn):
        stats = get_raw_event_stats(
            str(conn.execute("PRAGMA database_list").fetchone()[2]),
        )
        assert stats["total"] == 0
        assert stats["success_count"] == 0


# ── Retention ─────────────────────────────────────────────────────────────────

class TestCleanupRawEvents:
    def test_cleanup_old_events(self, conn):
        _insert_raw_row(conn, "2020-01-01T00:00:00", "terminal", "old-sess")
        _insert_raw_row(conn, datetime.now().isoformat(), "terminal", "recent-sess")
        conn.commit()
        deleted = cleanup_raw_events(
            str(conn.execute("PRAGMA database_list").fetchone()[2]),
            retention_days=30,
        )
        assert deleted == 1
        count = conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
        assert count == 1

    def test_protected_events_not_deleted(self, conn):
        _insert_raw_row(conn, "2020-01-01T00:00:00", "terminal", "protected-sess", protected=1)
        conn.commit()
        deleted = cleanup_raw_events(
            str(conn.execute("PRAGMA database_list").fetchone()[2]),
            retention_days=30,
        )
        assert deleted == 0
        count = conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
        assert count == 1

    def test_empty_db_no_error(self, conn):
        deleted = cleanup_raw_events(
            str(conn.execute("PRAGMA database_list").fetchone()[2]),
        )
        assert deleted == 0
