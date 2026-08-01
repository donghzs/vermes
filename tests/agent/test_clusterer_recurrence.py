"""Tests for emergent_clusterer recurrence fix.

Tests the 5 root-cause fixes:
1. Accumulating counts (not overwrite)
2. Dead clusters participate in matching
3. Dead clusters get revived on match
4. Signature idempotency (no duplicate clusters)
5. is_active field properly tracked
"""
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import pytest

from agent.emergent_clusterer import (
    EmergentClusterer, Cluster, ClusterDelta, match_clusters,
    _row_to_cluster, ensure_cluster_tables,
    CLUSTERS_TABLE_SQL,
)


def _make_db(tmp_path):
    """Create a fresh self-model.db with cluster tables."""
    db_path = str(tmp_path / "self-model.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(CLUSTERS_TABLE_SQL)
    conn.commit()
    conn.close()
    return db_path


def _insert_cluster_row(conn, cluster_id, name, sig, event_count=10,
                        success_count=9, error_count=1, is_active=1,
                        lifecycle_stage="stable", last_active_at=None):
    """Insert a cluster row directly for testing."""
    conn.execute(
        """INSERT OR REPLACE INTO clusters
           (id, name, feature_signature, event_count, success_count, error_count,
            total_duration, first_seen, last_seen, last_active_at,
            success_rate, avg_duration, is_active, lifecycle_stage,
            parent_cluster_id, evolved_from)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '')""",
        (cluster_id, name, sig, event_count, success_count, error_count,
         100.0, '2026-07-01T00:00:00', '2026-07-01T00:00:00',
         last_active_at or '2026-07-01T00:00:00',
         success_count / max(event_count, 1), 100.0 / max(event_count, 1),
         is_active, lifecycle_stage),
    )
    conn.commit()


class TestAccumulatingCounts:

    def test_upsert_accumulates_counts(self, tmp_path):
        """_upsert_cluster should ADD to existing counts, not overwrite."""
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        # Pre-insert a cluster with event_count=20
        _insert_cluster_row(conn, cluster_id=1, name="search_files",
                            sig="search_files", event_count=20, success_count=18)
        conn.close()

        clusterer = EmergentClusterer(db_path)
        # Create a new batch cluster matching old id=1
        nc = Cluster(id=0, name="search_files", feature_signature="search_files",
                     event_count=5, success_count=5, error_count=0,
                     total_duration=50.0, lifecycle_stage="emerging")
        nc.parent_cluster_id = 1

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        clusterer._upsert_cluster(cursor, nc, old_id=1, now="2026-08-02T00:00:00")
        conn.commit()

        row = cursor.execute(
            "SELECT event_count, success_count, error_count, total_duration FROM clusters WHERE id=1"
        ).fetchone()
        conn.close()

        assert row[0] == 25  # 20 + 5, not 5
        assert row[1] == 23  # 18 + 5
        assert row[2] == 1   # 1 + 0
        assert row[3] == 150.0  # 100 + 50

    def test_upsert_sets_is_active_1(self, tmp_path):
        """Upsert should set is_active=1 (revive if was dead)."""
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _insert_cluster_row(conn, cluster_id=1, name="test", sig="test",
                            is_active=0, lifecycle_stage="dead")
        conn.close()

        clusterer = EmergentClusterer(db_path)
        nc = Cluster(id=0, name="test", feature_signature="test",
                     event_count=3, success_count=3)
        nc.parent_cluster_id = 1

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        clusterer._upsert_cluster(cursor, nc, old_id=1, now="2026-08-02T00:00:00")
        conn.commit()

        row = cursor.execute("SELECT is_active FROM clusters WHERE id=1").fetchone()
        conn.close()
        assert row[0] == 1


class TestDeadClusterMatching:

    def test_load_old_clusters_includes_dead(self, tmp_path):
        """_load_old_clusters should load ALL clusters, not just active."""
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _insert_cluster_row(conn, 1, "alive", "sig_a", is_active=1, lifecycle_stage="stable")
        _insert_cluster_row(conn, 2, "dead", "sig_d", is_active=0, lifecycle_stage="dead")
        conn.close()

        clusterer = EmergentClusterer(db_path)
        old = clusterer._load_old_clusters()
        assert len(old) == 2  # both active and dead

    def test_match_clusters_revives_dead(self):
        """match_clusters should match against dead clusters too."""
        dead_cluster = Cluster(id=5, name="read_file", feature_signature="read_file",
                               lifecycle_stage="dead", is_active=False)
        new_cluster = Cluster(id=0, name="read_file", feature_signature="read_file",
                              lifecycle_stage="emerging")

        delta = match_clusters([new_cluster], [dead_cluster])

        # Should be matched as stable, not new
        assert len(delta.new_clusters) == 0
        assert len(delta.stable_clusters) == 1
        assert len(delta.revived_clusters) == 1
        assert delta.revived_clusters[0].id == 5

    def test_match_clusters_no_duplicate_for_dead(self):
        """Same signature matching a dead cluster should NOT create a new cluster."""
        dead = Cluster(id=10, name="search_files:.py", feature_signature="search_files",
                       lifecycle_stage="dead", is_active=False)
        new_batch = Cluster(id=0, name="search_files:.py", feature_signature="search_files")

        delta = match_clusters([new_batch], [dead])

        assert len(delta.new_clusters) == 0  # no duplicate!
        assert len(delta.revived_clusters) == 1

    def test_dead_not_remarked_dead(self):
        """A cluster already dead should not be re-added to dead_clusters."""
        dead = Cluster(id=3, name="old", feature_signature="old_sig",
                       lifecycle_stage="dead", is_active=False)
        new_batch = Cluster(id=0, name="different", feature_signature="different_sig")

        delta = match_clusters([new_batch], [dead])

        # The dead cluster should NOT be in dead_clusters (it's already dead)
        assert len(delta.dead_clusters) == 0

    def test_active_unmatched_marked_dead(self):
        """An active cluster not matched this batch should be marked dead."""
        active = Cluster(id=7, name="old_behavior", feature_signature="old_sig",
                         lifecycle_stage="stable", is_active=True)
        new_batch = Cluster(id=0, name="new_behavior", feature_signature="new_sig")

        delta = match_clusters([new_batch], [active])

        assert len(delta.dead_clusters) == 1
        assert delta.dead_clusters[0].id == 7


class TestSaveRevivesDead:

    def test_save_clusters_revives_dead(self, tmp_path):
        """_save_clusters should UPDATE is_active=1 for revived clusters."""
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _insert_cluster_row(conn, 1, "read_file", "read_file",
                            is_active=0, lifecycle_stage="dead")
        conn.close()

        clusterer = EmergentClusterer(db_path)
        delta = ClusterDelta()
        delta.revived_clusters.append(Cluster(id=1, name="read_file",
                                              feature_signature="read_file"))

        clusterer._save_clusters([], delta)

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT is_active, lifecycle_stage FROM clusters WHERE id=1").fetchone()
        conn.close()
        assert row[0] == 1
        assert row[1] == "emerging"


class TestClusterIsActiveField:

    def test_cluster_defaults_active(self):
        c = Cluster(id=1, name="test")
        assert c.is_active is True

    def test_row_to_cluster_reads_is_active(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _insert_cluster_row(conn, 1, "test", "test", is_active=0, lifecycle_stage="dead")
        conn.close()

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM clusters WHERE id=1").fetchone()
        c = _row_to_cluster(row)
        conn.close()

        assert c.is_active is False
        assert c.lifecycle_stage == "dead"


class TestG5LegacyMigration:
    """G5 fix: ensure_cluster_tables must add is_active to legacy databases."""

    def test_legacy_db_gets_is_active_column(self, tmp_path):
        """A legacy clusters table without is_active should get it added."""
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        # Create legacy schema (no is_active column)
        conn.execute("""
            CREATE TABLE clusters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                feature_signature TEXT DEFAULT '',
                event_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                total_duration REAL DEFAULT 0.0,
                first_seen TEXT DEFAULT '',
                last_seen TEXT DEFAULT '',
                last_active_at TEXT DEFAULT '',
                success_rate REAL DEFAULT 0.0,
                avg_duration REAL DEFAULT 0.0,
                lifecycle_stage TEXT DEFAULT 'emerging',
                parent_cluster_id INTEGER DEFAULT NULL,
                evolved_from TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "INSERT INTO clusters(name, lifecycle_stage) VALUES ('legacy_cluster', 'stable')"
        )
        conn.commit()

        # Run migration
        from agent.emergent_clusterer import ensure_cluster_tables
        ensure_cluster_tables(conn)

        cols = [r[1] for r in conn.execute("PRAGMA table_info(clusters)").fetchall()]
        assert "is_active" in cols

        # Existing rows get default value 1
        row = conn.execute(
            "SELECT name, is_active, lifecycle_stage FROM clusters WHERE name='legacy_cluster'"
        ).fetchone()
        assert row[1] == 1  # default 1
        assert row[2] == "stable"  # preserved
        conn.close()

    def test_fresh_db_has_is_active_from_create(self, tmp_path):
        """Fresh databases get is_active from CREATE TABLE (not ALTER)."""
        db_path = tmp_path / "fresh.db"
        conn = sqlite3.connect(str(db_path))
        from agent.emergent_clusterer import ensure_cluster_tables
        ensure_cluster_tables(conn)

        cols = [r[1] for r in conn.execute("PRAGMA table_info(clusters)").fetchall()]
        assert "is_active" in cols
        conn.close()


class TestG6StageNoDowngrade:
    """G6 fix: _upsert_cluster must not downgrade stable → emerging."""

    def test_stable_not_downgraded_to_emerging(self, tmp_path):
        """An existing stable cluster should stay stable on upsert."""
        from agent.emergent_clusterer import (
            EmergentClusterer,
            Cluster,
        )
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        from agent.emergent_clusterer import ensure_cluster_tables
        ensure_cluster_tables(conn)

        # Insert a stable cluster
        conn.execute(
            "INSERT INTO clusters(name, feature_signature, event_count, lifecycle_stage, is_active) "
            "VALUES('stable_skill', 'sig1', 10, 'stable', 1)"
        )
        conn.commit()
        conn.close()

        # Create a new cluster with same signature but stage='emerging'
        new_cluster = Cluster(
            id=0,
            name="stable_skill",
            feature_signature="sig1",
            event_count=5,
            success_count=5,
            error_count=0,
            total_duration=10.0,
            first_seen="2026-01-01",
            last_seen="2026-08-02",
            last_active_at="2026-08-02",
            lifecycle_stage="emerging",  # new cluster default
            is_active=True,
        )

        clusterer = EmergentClusterer(str(db_path))
        conn2 = sqlite3.connect(db_path)
        old_id = conn2.execute("SELECT id FROM clusters WHERE name='stable_skill'").fetchone()[0]
        clusterer._upsert_cluster(conn2, new_cluster, old_id, "2026-08-02T00:00:00")
        conn2.commit()

        row = conn2.execute(
            "SELECT lifecycle_stage, event_count FROM clusters WHERE name='stable_skill'"
        ).fetchone()
        conn2.close()

        assert row[0] == "stable"  # not downgraded to emerging
        assert row[1] == 15  # 10 + 5 accumulated

    def test_emerging_can_become_stable(self, tmp_path):
        """If new cluster is stable and old is emerging, upgrade to stable."""
        from agent.emergent_clusterer import (
            EmergentClusterer,
            Cluster,
            ensure_cluster_tables,
        )
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        ensure_cluster_tables(conn)

        conn.execute(
            "INSERT INTO clusters(name, feature_signature, event_count, lifecycle_stage, is_active) "
            "VALUES('growing', 'sig2', 5, 'emerging', 1)"
        )
        conn.commit()
        conn.close()

        new_cluster = Cluster(
            id=0,
            name="growing",
            feature_signature="sig2",
            event_count=10,
            success_count=10,
            error_count=0,
            total_duration=20.0,
            first_seen="2026-01-01",
            last_seen="2026-08-02",
            last_active_at="2026-08-02",
            lifecycle_stage="stable",  # upgrade
            is_active=True,
        )

        clusterer = EmergentClusterer(str(db_path))
        conn2 = sqlite3.connect(db_path)
        old_id = conn2.execute("SELECT id FROM clusters WHERE name='growing'").fetchone()[0]
        clusterer._upsert_cluster(conn2, new_cluster, old_id, "2026-08-02T00:00:00")
        conn2.commit()

        row = conn2.execute(
            "SELECT lifecycle_stage FROM clusters WHERE name='growing'"
        ).fetchone()
        conn2.close()

        assert row[0] == "stable"  # upgraded
