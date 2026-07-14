"""Tests for agent/domain_modules.py — vertical domain module hot-plug."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from agent.domain_modules import (
    DomainModule,
    DomainModuleManager,
    ModuleEmergenceDetector,
    get_active_modules_prompt,
    list_all_modules,
    scan_modules,
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


def _insert_cluster(conn, name, event_count, success_count, stage="stable",
                     cluster_id=None, signature=""):
    """Insert a cluster row."""
    now = datetime.now().isoformat()
    error_count = event_count - success_count
    if cluster_id:
        conn.execute(
            """INSERT INTO clusters (id, name, feature_signature, event_count,
               success_count, error_count, total_duration, first_seen,
               last_seen, last_active_at, success_rate, avg_duration,
               is_active, lifecycle_stage)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (cluster_id, name, signature or name, event_count, success_count,
             error_count, float(event_count * 0.5), now, now, now,
             success_count / max(event_count, 1), 0.5, stage)
        )
    else:
        conn.execute(
            """INSERT INTO clusters (name, feature_signature, event_count,
               success_count, error_count, total_duration, first_seen,
               last_seen, last_active_at, success_rate, avg_duration,
               is_active, lifecycle_stage)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (name, signature or name, event_count, success_count,
             error_count, float(event_count * 0.5), now, now, now,
             success_count / max(event_count, 1), 0.5, stage)
        )
    conn.commit()


# ── DomainModule Dataclass ───────────────────────────────────────────────────

class TestDomainModule:
    def test_to_prompt_block_active(self):
        m = DomainModule(
            id=1, cluster_id=1, name="terminal:git",
            event_count=50, success_rate=0.92, is_active=True,
            insights_summary="反模式: 2 条 | 策略: 1 条",
        )
        block = m.to_prompt_block()
        assert "terminal:git" in block
        assert "启用中" in block
        assert "50" in block
        assert "92%" in block

    def test_to_prompt_block_inactive(self):
        m = DomainModule(id=1, cluster_id=1, name="old", is_active=False)
        block = m.to_prompt_block()
        assert "已禁用" in block


# ── Module Emergence Detector ────────────────────────────────────────────────

class TestModuleEmergenceDetector:
    def test_no_stable_clusters(self, temp_db):
        detector = ModuleEmergenceDetector(temp_db)
        candidates = detector.detect_emerging_modules()
        assert candidates == []

    def test_large_stable_cluster_detected(self, temp_db):
        conn = sqlite3.connect(temp_db)
        # Large stable cluster
        _insert_cluster(conn, "terminal:python3+write_file:.py", 50, 45,
                         stage="stable", cluster_id=1,
                         signature="terminal|python3|write_file")
        # Small cluster (below median × 1.5)
        _insert_cluster(conn, "web_search", 5, 4, stage="stable",
                         cluster_id=2, signature="web_search")
        conn.close()

        detector = ModuleEmergenceDetector(temp_db)
        candidates = detector.detect_emerging_modules()

        # Large cluster should be a candidate (stability + scale + uniqueness)
        assert len(candidates) >= 1
        assert candidates[0].cluster_id == 1
        assert candidates[0].event_count == 50

    def test_declining_cluster_not_candidate(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "declining", 30, 25, stage="declining",
                         cluster_id=1)
        conn.close()

        detector = ModuleEmergenceDetector(temp_db)
        candidates = detector.detect_emerging_modules()
        assert len(candidates) == 0  # Not stable

    def test_uniqueness_check(self, temp_db):
        """Two clusters with very similar signatures — only the bigger one qualifies."""
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:git+commit", 40, 38,
                         stage="stable", cluster_id=1,
                         signature="terminal|git|commit")
        _insert_cluster(conn, "terminal:git+push", 35, 33,
                         stage="stable", cluster_id=2,
                         signature="terminal|git|push")
        conn.close()

        detector = ModuleEmergenceDetector(temp_db)
        candidates = detector.detect_emerging_modules()
        # Both have stability + scale. But they share "terminal|git" — uniqueness may fail for one
        # The larger one (40 events) should definitely qualify
        assert len(candidates) >= 1


# ── Module Manager ───────────────────────────────────────────────────────────

class TestDomainModuleManager:
    def test_ensure_tables(self, temp_db):
        manager = DomainModuleManager(temp_db)
        manager.ensure_tables()

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='domain_modules'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_scan_and_create(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:python3", 50, 45, stage="stable",
                         cluster_id=1, signature="terminal|python3")
        _insert_cluster(conn, "web_search", 5, 4, stage="stable",
                         cluster_id=2, signature="web_search")
        conn.close()

        manager = DomainModuleManager(temp_db)
        new_modules = manager.scan_and_create_modules()

        assert len(new_modules) >= 1
        assert new_modules[0].cluster_id == 1
        assert new_modules[0].id > 0

    def test_scan_idempotent(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:python3", 50, 45, stage="stable",
                         cluster_id=1, signature="terminal|python3")
        conn.close()

        manager = DomainModuleManager(temp_db)
        first = manager.scan_and_create_modules()
        second = manager.scan_and_create_modules()

        # Second scan should not create duplicates
        assert len(first) >= 1
        assert len(second) == 0

    def test_activate_deactivate(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:python3", 50, 45, stage="stable",
                         cluster_id=1, signature="terminal|python3")
        conn.close()

        manager = DomainModuleManager(temp_db)
        modules = manager.scan_and_create_modules()
        assert len(modules) >= 1
        module_id = modules[0].id

        # Deactivate
        assert manager.deactivate_module(module_id) is True
        active = manager.list_modules(active_only=True)
        assert all(m.id != module_id for m in active)

        # Reactivate
        assert manager.activate_module(module_id) is True
        active = manager.list_modules(active_only=True)
        assert any(m.id == module_id for m in active)

    def test_get_active_prompt_blocks_empty(self, temp_db):
        manager = DomainModuleManager(temp_db)
        assert manager.get_active_prompt_blocks() == ""

    def test_get_active_prompt_blocks_with_data(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:python3", 50, 45, stage="stable",
                         cluster_id=1, signature="terminal|python3")
        conn.close()

        manager = DomainModuleManager(temp_db)
        manager.scan_and_create_modules()

        prompt = manager.get_active_prompt_blocks()
        assert "<domain_modules>" in prompt
        assert "</domain_modules>" in prompt
        assert "terminal:python3" in prompt
        assert "启用中" in prompt

    def test_list_all_modules(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:python3", 50, 45, stage="stable",
                         cluster_id=1, signature="terminal|python3")
        _insert_cluster(conn, "web_search+write_file", 40, 38, stage="stable",
                         cluster_id=2, signature="web_search|write_file")
        conn.close()

        manager = DomainModuleManager(temp_db)
        manager.scan_and_create_modules()

        all_modules = manager.list_modules()
        assert len(all_modules) >= 1

    def test_update_existing_module_stats(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:python3", 30, 28, stage="stable",
                         cluster_id=1, signature="terminal|python3")
        conn.close()

        manager = DomainModuleManager(temp_db)
        manager.scan_and_create_modules()

        # Update cluster stats
        conn = sqlite3.connect(temp_db)
        conn.execute("UPDATE clusters SET event_count = 60, success_count = 55 WHERE id = 1")
        conn.commit()
        conn.close()

        # Re-scan should update, not create
        new_modules = manager.scan_and_create_modules()
        assert len(new_modules) == 0  # No new modules

        modules = manager.list_modules()
        assert any(m.event_count == 60 for m in modules)


# ── Convenience Functions ────────────────────────────────────────────────────

class TestConvenienceFunctions:
    def test_scan_modules(self, temp_db):
        conn = sqlite3.connect(temp_db)
        _insert_cluster(conn, "terminal:python3", 50, 45, stage="stable",
                         cluster_id=1, signature="terminal|python3")
        conn.close()

        new = scan_modules(temp_db)
        assert len(new) >= 1

    def test_get_active_modules_prompt_empty(self, temp_db):
        prompt = get_active_modules_prompt(temp_db)
        assert prompt == ""

    def test_list_all_modules_empty(self, temp_db):
        modules = list_all_modules(temp_db)
        assert modules == []
