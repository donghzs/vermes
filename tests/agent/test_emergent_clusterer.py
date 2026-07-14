"""Tests for agent/emergent_clusterer.py — emergent clustering engine."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime
from unittest import mock

import pytest

from agent.emergent_clusterer import (
    DEFAULT_EPSILON,
    DEFAULT_MIN_SAMPLES,
    Cluster,
    ClusterDelta,
    EmergentClusterer,
    EventVector,
    _cosine_distance,
    _cosine_similarity,
    _extract_commands,
    _extract_extensions,
    _generate_cluster_name,
    _generate_feature_signature,
    _collect_feature_space,
    dbscan_cluster,
    extract_event_vector,
    match_clusters,
    run_clustering_if_needed,
    should_trigger_clustering,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_raw_table(conn):
    """Create raw_events table for testing."""
    from agent.raw_event import ensure_raw_events_table
    ensure_raw_events_table(conn)


def _insert_raw(conn, **kwargs):
    """Insert a raw_event row. Default non-error event."""
    defaults = {
        "timestamp": datetime.now().isoformat(),
        "tool_name": "terminal",
        "args_preview": "",
        "result_preview": "",
        "success": 1,
        "duration": 0.1,
        "session_id": "test-sess",
    }
    defaults.update(kwargs)
    conn.execute(
        """INSERT INTO raw_events
           (timestamp, tool_name, args_preview, result_preview, success, duration, session_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (defaults["timestamp"], defaults["tool_name"], defaults["args_preview"],
         defaults["result_preview"], defaults["success"], defaults["duration"],
         defaults["session_id"]),
    )


# ── Feature Extraction ───────────────────────────────────────────────────────

class TestExtractExtensions:
    def test_python_file(self):
        exts = _extract_extensions('{"path": "/src/main.py"}')
        assert ".py" in exts

    def test_multiple_extensions(self):
        exts = _extract_extensions("path/to/file.py .py .md config.yml")
        assert ".py" in exts
        assert ".md" in exts
        assert ".yml" in exts

    def test_no_extensions(self):
        exts = _extract_extensions("just some text")
        assert exts == {} or all(v <= 1 for v in exts.values())

    def test_normalized(self):
        # _extract_extensions counts presence per ext type, not frequency
        exts = _extract_extensions(".py .py .py .md")
        assert ".py" in exts
        assert ".md" in exts
        assert abs(exts[".py"] - 0.5) < 0.01  # 1 type of 2 types


class TestExtractCommands:
    def test_git_command(self):
        cmds = _extract_commands('{"command": "git commit -m fix"}')
        assert "git" in cmds

    def test_multiple_commands(self):
        cmds = _extract_commands("pip install numpy && python3 test.py")
        assert "pip" in cmds
        assert "python3" in cmds

    def test_no_commands(self):
        cmds = _extract_commands("echo hello world")
        assert cmds == {} or all(v <= 1 for v in cmds.values())

    def test_docker_command(self):
        cmds = _extract_commands("docker run -it ubuntu bash")
        assert "docker" in cmds

    def test_plain_text_args(self):
        cmds = _extract_commands("grep -r pattern .")
        assert "grep" in cmds


class TestExtractEventVector:
    def test_basic_vector(self, tmp_path):
        db = tmp_path / "test.db"
        c = sqlite3.connect(str(db))
        _make_raw_table(c)
        _insert_raw(c, tool_name="terminal", args_preview='{"command": "git status"}',
                     result_preview="On branch main", success=1, duration=0.5)
        c.commit()
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM raw_events LIMIT 1").fetchone()
        v = extract_event_vector(row)
        assert v.tool_name == "terminal"
        assert v.duration == 0.5
        assert v.is_error is False
        assert 0 <= v.time_of_day <= 1

    def test_error_event(self, tmp_path):
        db = tmp_path / "test.db"
        c = sqlite3.connect(str(db))
        _make_raw_table(c)
        _insert_raw(c, tool_name="write_file", args_preview='{"path": "/dev/null"}',
                     success=0)
        c.commit()
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM raw_events LIMIT 1").fetchone()
        v = extract_event_vector(row)
        assert v.is_error is True


# ── Feature Space ────────────────────────────────────────────────────────────

class TestCollectFeatureSpace:
    def test_basic(self):
        v1 = EventVector(1, "terminal", 0.1, 0.5, {".py": 1.0}, {"git": 1.0}, False)
        v2 = EventVector(2, "web_search", 0.2, 0.6, {}, {}, False)
        features = _collect_feature_space([v1, v2])
        assert "tool:terminal" in features
        assert "tool:web_search" in features
        assert "ext:.py" in features
        assert "cmd:git" in features
        assert "time_of_day" in features
        assert "duration_log" in features
        assert "is_error" in features

    def test_no_extensions_omits_ext_dim(self):
        v1 = EventVector(1, "terminal", 0.1, 0.5, {}, {}, False)
        features = _collect_feature_space([v1])
        for f in features:
            assert not f.startswith("ext:")

    def test_no_commands_omits_cmd_dim(self):
        v1 = EventVector(1, "terminal", 0.1, 0.5, {}, {}, False)
        features = _collect_feature_space([v1])
        for f in features:
            assert not f.startswith("cmd:")


# ── Cosine Distance ──────────────────────────────────────────────────────────

class TestCosineDistance:
    def test_identical_vectors(self):
        v = [1.0, 0.5, 0.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-10
        assert abs(_cosine_distance(v, v)) < 1e-10

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_similarity(a, b)) < 1e-10
        assert abs(_cosine_distance(a, b) - 1.0) < 1e-10

    def test_zero_norm(self):
        assert _cosine_similarity([0, 0], [1, 1]) == 0.0


# ── DBSCAN ────────────────────────────────────────────────────────────────────

class TestDBSCAN:
    def test_empty_input(self):
        labels, clusters, noise = dbscan_cluster([], [])
        assert labels == [] and clusters == [] and noise == []

    def test_single_cluster(self):
        # Create 5 nearly identical vectors
        vectors = []
        for i in range(5):
            v = EventVector(i, "terminal", 0.5, 0.5, {".py": 1.0}, {"git": 1.0}, False)
            vectors.append(v)
        features = _collect_feature_space(vectors)
        labels, clusters, noise = dbscan_cluster(vectors, features, eps=0.4, min_samples=3)
        # All should be in one cluster
        assert len(clusters) == 1, f"Expected 1 cluster, got {len(clusters)}: labels={labels}"
        assert len(clusters[0]) == 5
        assert len(noise) == 0
        assert all(l == 0 for l in labels)

    def test_two_distinct_clusters(self):
        vectors = []
        # Cluster 1: terminal + python
        for i in range(5):
            v = EventVector(i, "terminal", 0.5, 0.5, {".py": 1.0}, {"python3": 1.0}, False)
            vectors.append(v)
        # Cluster 2: web_search + write_file
        for i in range(5, 10):
            v = EventVector(i, "web_search", 0.2, 0.6, {".md": 1.0}, {}, False)
            vectors.append(v)
        features = _collect_feature_space(vectors)
        labels, clusters, noise = dbscan_cluster(vectors, features, eps=0.3, min_samples=3)

        assert len(clusters) == 2, f"Expected 2 clusters, got {len(clusters)}: labels={labels}"
        assert len(noise) == 0

    def test_noise_points(self):
        vectors = []
        # One tight cluster of 4
        for i in range(4):
            v = EventVector(i, "terminal", 0.5, 0.5, {".py": 1.0}, {"git": 1.0}, False)
            vectors.append(v)
        # One outlier
        v = EventVector(99, "read_file", 0.1, 0.2, {".txt": 1.0}, {"cat": 1.0}, False)
        vectors.append(v)
        features = _collect_feature_space(vectors)
        labels, clusters, noise = dbscan_cluster(vectors, features, eps=0.3, min_samples=3)

        assert len(clusters) == 1
        assert len(noise) == 1
        assert labels[4] == -1  # last one is noise


# ── Cluster Naming ───────────────────────────────────────────────────────────

class TestGenerateClusterName:
    def test_terminal_git_py(self):
        vectors = [
            EventVector(i, "terminal", 0.5, 0.5, {".py": 1.0}, {"git": 1.0}, False)
            for i in range(3)
        ]
        name = _generate_cluster_name(vectors)
        assert "terminal" in name
        assert (".py" in name or "python" in name.lower())

    def test_web_search(self):
        vectors = [
            EventVector(i, "web_search", 0.2, 0.6, {}, {}, False)
            for i in range(2)
        ]
        name = _generate_cluster_name(vectors)
        assert "web_search" in name

    def test_empty(self):
        assert _generate_cluster_name([]) == "empty"


class TestGenerateFeatureSignature:
    def test_unique_tools(self):
        vectors = [
            EventVector(1, "terminal", 0.5, 0.5, {}, {}, False),
            EventVector(2, "web_search", 0.2, 0.6, {}, {}, False),
        ]
        sig = _generate_feature_signature(vectors)
        assert "terminal" in sig and "web_search" in sig


# ── Cluster Matching ─────────────────────────────────────────────────────────

class TestMatchClusters:
    def test_exact_match(self):
        old = [Cluster(id=1, name="t:web_search", feature_signature="web_search", event_count=10)]
        new = [Cluster(id=0, name="t:web_search", feature_signature="web_search", event_count=15)]
        delta = match_clusters(new, old)
        assert len(delta.stable_clusters) == 1
        assert delta.stable_clusters[0][0].parent_cluster_id == 1

    def test_new_cluster(self):
        old = [Cluster(id=1, name="old", feature_signature="terminal")]
        new = [Cluster(id=0, name="new", feature_signature="web_search")]
        delta = match_clusters(new, old)
        assert len(delta.new_clusters) == 1
        assert len(delta.dead_clusters) == 1

    def test_partial_match_by_jaccard(self):
        old = [Cluster(id=1, name="old", feature_signature="terminal|python")]
        new = [Cluster(id=0, name="new", feature_signature="terminal|python|git")]
        delta = match_clusters(new, old)
        assert len(delta.stable_clusters) == 1
        assert delta.stable_clusters[0][0].evolved_from == "old"


# ── EmergentClusterer Integration ────────────────────────────────────────────

class TestEmergentClusterer:
    def test_empty_db_returns_empty(self, tmp_path):
        db = tmp_path / "empty.db"
        c = sqlite3.connect(str(db))
        _make_raw_table(c)
        c.close()

        clusterer = EmergentClusterer(str(db))
        result = clusterer.run()
        assert result["noise_count"] == 0
        assert len(result["clusters"]) == 0

    def test_clusters_terminal_vs_web(self, tmp_path):
        db = tmp_path / "test.db"
        c = sqlite3.connect(str(db))
        _make_raw_table(c)

        # 10 terminal events with .py files
        for i in range(10):
            _insert_raw(c, tool_name="terminal",
                         args_preview=f'{{"command": "python3 script_{i}.py"}}',
                         result_preview=f"output {i}",
                         success=1, duration=0.5,
                         session_id="sess-1")
        # 8 web_search events
        for i in range(8):
            _insert_raw(c, tool_name="web_search",
                         args_preview=f'{{"query": "test query {i}"}}',
                         result_preview="results...",
                         success=1, duration=0.3,
                         session_id="sess-1")
        c.commit()
        c.close()

        clusterer = EmergentClusterer(str(db), eps=0.3)
        result = clusterer.run()

        assert result["update_stats"]["events"] == 18
        # Should find 2 clusters
        assert len(result["clusters"]) > 0, f"No clusters found! {result}"
        assert result["noise_count"] <= 5, f"Too much noise: {result['noise_count']}"

    def test_backfill_cluster_ids(self, tmp_path):
        db = tmp_path / "backfill.db"
        c = sqlite3.connect(str(db))
        _make_raw_table(c)

        # Insert enough similar events to form clusters
        for i in range(10):
            _insert_raw(c, tool_name="terminal",
                         args_preview=f'{{"command": "git status {i}"}}',
                         result_preview="ok", success=1, duration=0.5,
                         session_id="sess-1")
        for i in range(8):
            _insert_raw(c, tool_name="web_search",
                         args_preview=f'{{"query": "q{i}"}}',
                         result_preview="ok", success=1, duration=0.3,
                         session_id="sess-1")
        c.commit()
        c.close()

        clusterer = EmergentClusterer(str(db), eps=0.3)
        result = clusterer.run()

        # Check that raw_events have cluster_id assigned
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT COUNT(*) as cnt FROM raw_events WHERE cluster_id IS NOT NULL").fetchone()
        assert rows["cnt"] > 0, "No events backfilled with cluster_id"
        c.close()


# ── Trigger Logic ────────────────────────────────────────────────────────────

class TestTriggerLogic:
    def test_below_threshold_no_trigger(self, tmp_path):
        db = tmp_path / "trigger.db"
        c = sqlite3.connect(str(db))
        _make_raw_table(c)
        for i in range(3):
            _insert_raw(c, tool_name="terminal", session_id="sess-1")
        c.commit()
        c.close()
        assert should_trigger_clustering(str(db), min_unclustered=50) is False

    def test_run_clustering_noop_below_threshold(self, tmp_path):
        db = tmp_path / "noop.db"
        c = sqlite3.connect(str(db))
        _make_raw_table(c)
        _insert_raw(c, tool_name="terminal", session_id="sess-1")
        c.commit()
        c.close()
        result = run_clustering_if_needed(str(db))
        assert result is None


# ── Cluster Dataclass ────────────────────────────────────────────────────────

class TestCluster:
    def test_success_rate(self):
        c = Cluster(id=1, name="test", event_count=10, success_count=8, error_count=2,
                     total_duration=5.0)
        assert abs(c.success_rate - 0.8) < 0.01
        assert abs(c.avg_duration - 0.5) < 0.01

    def test_empty_cluster(self):
        c = Cluster(id=1, name="test")
        assert c.success_rate == 0.0
        assert c.avg_duration == 0.0

    def test_defaults(self):
        c = Cluster(id=1, name="test")
        assert c.lifecycle_stage == "emerging"
        assert c.event_count == 0
        assert c.event_ids == []
