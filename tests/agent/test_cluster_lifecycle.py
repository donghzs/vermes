"""Tests for agent/cluster_lifecycle.py — cluster lifecycle management."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from agent.cluster_lifecycle import (
    ClusterLifecycleManager,
    LifecycleThresholds,
    run_lifecycle_evaluation,
    wake_cluster_on_event,
)
from agent.emergent_clusterer import ensure_cluster_tables
from agent.raw_event import ensure_raw_events_table


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # Initialize tables
    conn = sqlite3.connect(path)
    ensure_cluster_tables(conn)
    ensure_raw_events_table(conn)
    conn.close()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def _insert_cluster(conn, name="test", stage="stable", event_count=10,
                     last_active=None, cluster_id=None):
    """Insert a cluster with given stage."""
    now = (last_active or datetime.now()).isoformat()
    if cluster_id:
        conn.execute(
            """INSERT INTO clusters (id, name, feature_signature, event_count,
               success_count, error_count, total_duration, first_seen,
               last_seen, last_active_at, is_active, lifecycle_stage)
               VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (cluster_id, name, event_count, event_count, 0, float(event_count),
             now, now, now, stage)
        )
    else:
        conn.execute(
            """INSERT INTO clusters (name, feature_signature, event_count,
               success_count, error_count, total_duration, first_seen,
               last_seen, last_active_at, is_active, lifecycle_stage)
               VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (name, event_count, event_count, 0, float(event_count),
             now, now, now, stage)
        )
    conn.commit()


def _insert_event(conn, cluster_id, timestamp, success=1):
    conn.execute(
        """INSERT INTO raw_events
           (timestamp, tool_name, args_preview, result_preview, success,
            duration, session_id, turn_number, cluster_id)
           VALUES (?, 'terminal', '', '', ?, 0.5, 's1', 0, ?)""",
        (timestamp, success, cluster_id)
    )
    conn.commit()


# ── LifecycleThresholds ──────────────────────────────────────────────────────

class TestLifecycleThresholds:
    def test_short_interval(self):
        t = LifecycleThresholds(n_declining=45, m_dormant=90, k_dead=225)
        desc = t.description
        # 45s rounds to ~1min (45/60=0.75 → "1min")
        assert "min" in desc

    def test_long_interval(self):
        t = LifecycleThresholds(n_declining=7776000, m_dormant=15552000, k_dead=38880000)
        desc = t.description
        assert "d" in desc  # days

    def test_hours(self):
        t = LifecycleThresholds(n_declining=7200, m_dormant=14400, k_dead=36000)
        desc = t.description
        assert "h" in desc


# ── Threshold Computation ────────────────────────────────────────────────────

class TestComputeThresholds:
    def test_from_event_intervals(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, name="trading", stage="stable", event_count=5, cluster_id=1)
        # Insert events with 15min intervals
        base = datetime.now() - timedelta(hours=2)
        for i in range(5):
            ts = (base + timedelta(minutes=15 * i)).isoformat()
            _insert_event(conn, 1, ts)
        conn.close()

        manager = ClusterLifecycleManager(temp_db)
        cluster = manager._load_cluster(1)
        thresholds = manager.compute_thresholds(cluster)

        # avg_interval ~900s (15min), N=2700s (45min), M=5400s, K=13500s
        assert 2000 < thresholds.n_declining < 4000  # ~45min
        assert 4000 < thresholds.m_dormant < 8000    # ~90min
        assert 10000 < thresholds.k_dead < 20000     # ~225min

    def test_fallback_no_intervals(self, temp_db):
        """When no raw_events exist, fall back to first_seen → last_seen span."""
        conn = sqlite3.connect(temp_db)
        first = (datetime.now() - timedelta(days=10)).isoformat()
        _insert_cluster(conn, name="monthly", stage="stable",
                         event_count=3, last_active=datetime.now())
        # Set first_seen to 30 days ago
        conn.execute("UPDATE clusters SET first_seen = ? WHERE id = 1",
                     ((datetime.now() - timedelta(days=30)).isoformat(),))
        conn.commit()
        conn.close()

        manager = ClusterLifecycleManager(temp_db)
        cluster = manager._load_cluster(1)
        thresholds = manager.compute_thresholds(cluster)

        # Should have some non-zero thresholds
        assert thresholds.n_declining > 0
        assert thresholds.m_dormant > thresholds.n_declining
        assert thresholds.k_dead > thresholds.m_dormant

    def test_fallback_empty_cluster(self, temp_db):
        """Empty cluster with no events gets conservative default."""
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, name="empty", stage="emerging", event_count=0)
        conn.close()

        manager = ClusterLifecycleManager(temp_db)
        cluster = manager._load_cluster(1)
        thresholds = manager.compute_thresholds(cluster)

        # Default: 1h interval → N=3h, M=6h, K=15h
        assert thresholds.n_declining > 0


# ── Stage Evaluation ─────────────────────────────────────────────────────────

class TestEvaluateCluster:
    def test_stable_stays_stable_when_active(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, name="active", stage="stable", event_count=10,
                         last_active=datetime.now())
        conn.close()

        manager = ClusterLifecycleManager(temp_db)
        cluster = manager._load_cluster(1)
        new_stage = manager._evaluate_cluster(cluster)
        assert new_stage == "stable"

    def test_stable_to_declining_when_inactive(self, temp_db):
        # Need enough events to compute a short interval (e.g. 1min)
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, name="old", stage="stable", event_count=10,
                         last_active=datetime.now() - timedelta(hours=1),
                         cluster_id=1)
        # Insert events with 1min intervals, ending 1h ago
        base = datetime.now() - timedelta(hours=1, minutes=10)
        for i in range(10):
            ts = (base + timedelta(minutes=i)).isoformat()
            _insert_event(conn, 1, ts)
        conn.close()

        manager = ClusterLifecycleManager(temp_db)
        cluster = manager._load_cluster(1)
        # avg_interval=60s → N=180s=3min. Inactive for 1h → declining at least
        new_stage = manager._evaluate_cluster(cluster)
        assert new_stage in ("declining", "dormant", "dead")

    def test_emerging_to_stable(self, temp_db):
        conn = sqlite3.connect(temp_db)
        # 5 events, recently active
        _insert_cluster(conn, name="growing", stage="emerging", event_count=5,
                         last_active=datetime.now(), cluster_id=1)
        for i in range(5):
            ts = (datetime.now() - timedelta(minutes=5-i)).isoformat()
            _insert_event(conn, 1, ts)
        conn.close()

        manager = ClusterLifecycleManager(temp_db)
        cluster = manager._load_cluster(1)
        new_stage = manager._evaluate_cluster(cluster)
        assert new_stage == "stable"

    def test_dormant_to_dead(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, name="sleeping", stage="dormant", event_count=5,
                         last_active=datetime.now() - timedelta(days=365),
                         cluster_id=1)
        # Add some events long ago
        for i in range(5):
            ts = (datetime.now() - timedelta(days=365, minutes=i)).isoformat()
            _insert_event(conn, 1, ts)
        conn.close()

        manager = ClusterLifecycleManager(temp_db)
        cluster = manager._load_cluster(1)
        new_stage = manager._evaluate_cluster(cluster)
        assert new_stage == "dead"


# ── On New Event (Wake/Resurrect) ────────────────────────────────────────────

class TestOnNewEvent:
    def test_dormant_wakes_to_stable(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, name="sleeping", stage="dormant", cluster_id=1)
        conn.close()

        result = wake_cluster_on_event(temp_db, 1)
        assert result == "stable"

        # Verify DB updated
        conn = sqlite3.connect(temp_db)
        row = conn.execute("SELECT lifecycle_stage FROM clusters WHERE id = 1").fetchone()
        conn.close()
        assert row[0] == "stable"

    def test_dead_resurrects_to_emerging(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, name="dead", stage="dead", cluster_id=1)
        conn.close()

        result = wake_cluster_on_event(temp_db, 1)
        assert result == "emerging"

    def test_stable_unchanged(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, name="active", stage="stable", cluster_id=1)
        conn.close()

        result = wake_cluster_on_event(temp_db, 1)
        assert result is None  # No transition

    def test_transition_recorded(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, name="sleeping", stage="dormant", cluster_id=1)
        conn.close()

        wake_cluster_on_event(temp_db, 1)

        conn = sqlite3.connect(temp_db)
        row = conn.execute(
            "SELECT from_stage, to_stage, reason FROM cluster_lifecycle_events WHERE cluster_id = 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "dormant"
        assert row[1] == "stable"
        assert row[2] == "new_event_wake"


# ── Batch Evaluation ─────────────────────────────────────────────────────────

class TestEvaluateAll:
    def test_mixed_clusters(self, temp_db):
        conn = sqlite3.connect(temp_db)
        # Active stable cluster
        _insert_cluster(conn, name="active", stage="stable", event_count=10,
                         last_active=datetime.now(), cluster_id=1)
        # Dormant cluster that should go dead
        _insert_cluster(conn, name="sleeping", stage="dormant", event_count=3,
                         last_active=datetime.now() - timedelta(days=365),
                         cluster_id=2)
        conn.close()

        stats = run_lifecycle_evaluation(temp_db)
        assert stats["transitioned"] >= 1
        assert stats["errors"] == 0

    def test_empty_db(self, temp_db):
        stats = run_lifecycle_evaluation(temp_db)
        assert stats["transitioned"] == 0
        assert stats["stayed"] == 0
        assert stats["errors"] == 0


# ── Dead Cluster Cleanup ─────────────────────────────────────────────────────

class TestCleanupDeadClusters:
    def test_no_dead_clusters(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, name="alive", stage="stable", cluster_id=1)
        conn.close()

        manager = ClusterLifecycleManager(temp_db)
        deleted = manager.cleanup_dead_clusters()
        assert deleted == 0

    def test_dead_cluster_events_deleted(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, name="dead", stage="dead", cluster_id=1)
        # Add unprotected events
        for _ in range(5):
            conn.execute(
                """INSERT INTO raw_events
                   (timestamp, tool_name, args_preview, result_preview, success,
                    duration, session_id, turn_number, cluster_id, protected)
                   VALUES (?, 'terminal', '', '', 1, 0.5, 's1', 0, 1, 0)""",
                (datetime.now().isoformat(),)
            )
        conn.commit()
        conn.close()

        manager = ClusterLifecycleManager(temp_db)
        deleted = manager.cleanup_dead_clusters()
        assert deleted > 0

    def test_protected_events_preserved(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, name="dead", stage="dead", cluster_id=1)
        # Add protected events
        for _ in range(3):
            conn.execute(
                """INSERT INTO raw_events
                   (timestamp, tool_name, args_preview, result_preview, success,
                    duration, session_id, turn_number, cluster_id, protected)
                   VALUES (?, 'terminal', '', '', 1, 0.5, 's1', 0, 1, 1)""",
                (datetime.now().isoformat(),)
            )
        conn.commit()
        conn.close()

        manager = ClusterLifecycleManager(temp_db)
        deleted = manager.cleanup_dead_clusters()
        assert deleted == 0  # Protected events not deleted
