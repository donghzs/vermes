"""Tests for agent/cross_session_continuity.py — cross-session continuity."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from agent.cross_session_continuity import (
    ClusterSnapshot,
    ContinuityBriefing,
    CrossSessionContinuity,
    get_continuity_prompt,
    save_session_snapshot,
)
from agent.emergent_clusterer import ensure_cluster_tables
from agent.raw_event import ensure_raw_events_table


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    ensure_cluster_tables(conn)
    ensure_raw_events_table(conn)
    conn.close()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def _insert_cluster(conn, name, event_count=10, success_count=9, stage="stable",
                     cluster_id=None, is_active=1):
    now = datetime.now().isoformat()
    if cluster_id:
        conn.execute(
            """INSERT INTO clusters (id, name, feature_signature, event_count,
               success_count, error_count, total_duration, first_seen,
               last_seen, last_active_at, success_rate, avg_duration,
               is_active, lifecycle_stage)
               VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cluster_id, name, event_count, success_count,
             event_count - success_count, float(event_count * 0.5),
             now, now, now, success_count / max(event_count, 1), 0.5,
             is_active, stage)
        )
    else:
        conn.execute(
            """INSERT INTO clusters (name, feature_signature, event_count,
               success_count, error_count, total_duration, first_seen,
               last_seen, last_active_at, success_rate, avg_duration,
               is_active, lifecycle_stage)
               VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, event_count, success_count,
             event_count - success_count, float(event_count * 0.5),
             now, now, now, success_count / max(event_count, 1), 0.5,
             is_active, stage)
        )
    conn.commit()


# ── ContinuityBriefing ───────────────────────────────────────────────────────

class TestContinuityBriefing:
    def test_empty_briefing(self):
        b = ContinuityBriefing()
        assert b.is_empty() is True

    def test_non_empty_briefing(self):
        b = ContinuityBriefing(new_clusters=["terminal:git"])
        assert b.is_empty() is False

    def test_to_prompt_text_empty(self):
        b = ContinuityBriefing()
        assert b.to_prompt_text() == ""

    def test_to_prompt_text_with_data(self):
        b = ContinuityBriefing(
            new_clusters=["terminal:git", "web_search", "extra1", "extra2"],
            new_modules=["trading"],
            total_events_since=42,
        )
        text = b.to_prompt_text()
        assert "terminal:git" in text
        assert "web_search" in text
        # Should cap at 3 new clusters
        assert "extra2" not in text
        assert "trading" in text
        assert "42" in text


# ── CrossSessionContinuity ───────────────────────────────────────────────────

class TestCrossSessionContinuity:
    def test_ensure_tables(self, temp_db):
        csc = CrossSessionContinuity(temp_db)
        csc.ensure_tables()

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cluster_snapshots'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_save_snapshot(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:git", event_count=30, cluster_id=1)
        _insert_cluster(conn, "web_search", event_count=10, cluster_id=2)
        conn.close()

        csc = CrossSessionContinuity(temp_db)
        snapshot = csc.save_snapshot("session_1")

        assert snapshot.session_id == "session_1"
        assert len(snapshot.clusters) == 2
        assert snapshot.total_events == 40
        assert snapshot.active_clusters == 2

    def test_load_last_snapshot_none(self, temp_db):
        csc = CrossSessionContinuity(temp_db)
        assert csc.load_last_snapshot() is None

    def test_load_last_snapshot(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:git", event_count=30, cluster_id=1)
        conn.close()

        csc = CrossSessionContinuity(temp_db)
        csc.save_snapshot("session_1")

        last = csc.load_last_snapshot()
        assert last is not None
        assert last.session_id == "session_1"
        assert len(last.clusters) == 1

    def test_generate_briefing_no_snapshot(self, temp_db):
        csc = CrossSessionContinuity(temp_db)
        briefing = csc.generate_briefing()
        assert briefing.is_empty() is True

    def test_generate_briefing_new_cluster(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:git", event_count=30, cluster_id=1)
        conn.close()

        csc = CrossSessionContinuity(temp_db)
        csc.save_snapshot("session_1")

        # Add a new cluster
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "web_search", event_count=5, cluster_id=2)
        conn.close()

        briefing = csc.generate_briefing()
        assert len(briefing.new_clusters) >= 1
        assert "web_search" in briefing.new_clusters

    def test_generate_briefing_dormant_cluster(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:git", event_count=30, stage="stable",
                         cluster_id=1)
        conn.close()

        csc = CrossSessionContinuity(temp_db)
        csc.save_snapshot("session_1")

        # Change stage to dormant
        conn = sqlite3.connect(temp_db)
        conn.execute("UPDATE clusters SET lifecycle_stage = 'dormant' WHERE id = 1")
        conn.commit()
        conn.close()

        briefing = csc.generate_briefing()
        assert len(briefing.dormant_clusters) >= 1

    def test_get_session_start_prompt_empty(self, temp_db):
        csc = CrossSessionContinuity(temp_db)
        assert csc.get_session_start_prompt() == ""

    def test_get_session_start_prompt_with_data(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:git", event_count=30, cluster_id=1)
        conn.close()

        csc = CrossSessionContinuity(temp_db)
        csc.save_snapshot("session_1")

        # Add new cluster
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "web_search", event_count=10, cluster_id=2)
        conn.close()

        prompt = csc.get_session_start_prompt()
        assert "<continuity>" in prompt
        assert "</continuity>" in prompt
        assert "web_search" in prompt

    def test_multiple_snapshots(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "c1", event_count=10, cluster_id=1)
        conn.close()

        csc = CrossSessionContinuity(temp_db)
        csc.save_snapshot("s1")
        csc.save_snapshot("s2")
        csc.save_snapshot("s3")

        last = csc.load_last_snapshot()
        assert last is not None
        assert last.session_id == "s3"

    def test_events_since_snapshot(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "c1", event_count=5, cluster_id=1)
        conn.close()

        csc = CrossSessionContinuity(temp_db)
        csc.save_snapshot("s1")

        # Add more events
        conn = sqlite3.connect(temp_db)
        for _ in range(3):
            conn.execute(
                """INSERT INTO raw_events
                   (timestamp, tool_name, args_preview, result_preview, success,
                    duration, session_id, turn_number)
                   VALUES (?, 'terminal', '', '', 1, 0.5, 's2', 0)""",
                (datetime.now().isoformat(),)
            )
        conn.commit()
        conn.close()

        briefing = csc.generate_briefing()
        assert briefing.total_events_since == 3


# ── Convenience Functions ────────────────────────────────────────────────────

class TestConvenienceFunctions:
    def test_save_session_snapshot(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "c1", event_count=5, cluster_id=1)
        conn.close()

        snap = save_session_snapshot(temp_db, "test_session")
        assert snap.session_id == "test_session"
        assert len(snap.clusters) == 1

    def test_get_continuity_prompt_empty(self, temp_db):
        prompt = get_continuity_prompt(temp_db)
        assert prompt == ""
