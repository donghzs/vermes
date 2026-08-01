"""Tests for merge real-merge + cleanup_merged_skill_memories."""
import sqlite3
import tempfile
from pathlib import Path
import pytest

from agent.memory_reflection import resolve_flag, restore_flag, cleanup_merged_skill_memories


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Create a temporary memory_index.db with schema + test data."""
    db_path = tmp_path / "memory_index.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, layer TEXT, type TEXT, scope TEXT,
            pointer TEXT, fts_content TEXT, updated_at TEXT,
            access_count INTEGER DEFAULT 0, lifecycle_tag TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER, flag_type TEXT, confidence REAL,
            evidence TEXT, status TEXT, created_at TEXT,
            source TEXT, resolution TEXT, resolved_at TEXT
        )
    """)
    # Insert skill memories (duplicates)
    for i in range(5):
        conn.execute(
            "INSERT INTO memories (source, layer, lifecycle_tag, fts_content) "
            "VALUES ('skill', 'procedural', 'ephemeral', ?)",
            (f"Skill description {i}",),
        )
    # Insert one non-skill memory (should NOT be deleted)
    conn.execute(
        "INSERT INTO memories (source, layer, lifecycle_tag, fts_content) "
        "VALUES ('note', 'note', 'preference', 'user prefers python')",
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("agent.memory_fabric._get_index_db", lambda: db_path)
    return db_path


class TestMergeRealMerge:

    def test_merge_deletes_skill_memory(self, tmp_db):
        """resolve_flag('merge') on a skill memory deletes the memory row."""
        conn = sqlite3.connect(str(tmp_db))
        # Create an open duplicate flag pointing to memory id=1 (skill)
        conn.execute(
            "INSERT INTO memory_flags (memory_id, flag_type, confidence, status, source) "
            "VALUES (1, 'duplicate', 0.9, 'open', 'reflection')"
        )
        conn.commit()
        conn.close()

        ok = resolve_flag(1, "merge")
        assert ok is True

        conn = sqlite3.connect(str(tmp_db))
        # Flag should be resolved
        flag = conn.execute("SELECT status, resolution FROM memory_flags WHERE id=1").fetchone()
        assert flag[0] == "resolved"
        assert flag[1] == "merge"
        # Memory should be deleted (source=skill)
        mem = conn.execute("SELECT COUNT(*) FROM memories WHERE id=1").fetchone()
        assert mem[0] == 0
        conn.close()

    def test_merge_does_not_delete_non_skill(self, tmp_db):
        """resolve_flag('merge') on a non-skill memory does NOT delete it."""
        conn = sqlite3.connect(str(tmp_db))
        # memory id=6 is source='note'
        conn.execute(
            "INSERT INTO memory_flags (memory_id, flag_type, confidence, status, source) "
            "VALUES (6, 'duplicate', 0.9, 'open', 'reflection')"
        )
        conn.commit()
        conn.close()

        ok = resolve_flag(1, "merge")
        assert ok is True

        conn = sqlite3.connect(str(tmp_db))
        # Memory id=6 should still exist (source='note', not 'skill')
        mem = conn.execute("SELECT COUNT(*) FROM memories WHERE id=6").fetchone()
        assert mem[0] == 1
        conn.close()

    def test_merge_flag_still_resolved_if_memory_already_gone(self, tmp_db):
        """merge on already-deleted memory: flag still resolves (fail-open)."""
        conn = sqlite3.connect(str(tmp_db))
        # Delete memory first
        conn.execute("DELETE FROM memories WHERE id=2")
        conn.execute(
            "INSERT INTO memory_flags (memory_id, flag_type, confidence, status, source) "
            "VALUES (2, 'duplicate', 0.9, 'open', 'reflection')"
        )
        conn.commit()
        conn.close()

        ok = resolve_flag(1, "merge")
        assert ok is True  # flag resolves even though memory is gone


class TestCleanupMergedSkillMemories:

    def test_cleanup_resolved_merge(self, tmp_db):
        """cleanup deletes memories pointed to by resolved-merge flags."""
        conn = sqlite3.connect(str(tmp_db))
        # Simulate 87 historical resolved-merge flags
        for i in range(1, 4):
            conn.execute(
                "INSERT INTO memory_flags (memory_id, flag_type, confidence, status, resolution) "
                f"VALUES ({i}, 'duplicate', 0.9, 'resolved', 'merge')"
            )
        conn.commit()
        # Verify memories exist before cleanup
        assert conn.execute("SELECT COUNT(*) FROM memories WHERE source='skill'").fetchone()[0] == 5
        conn.close()

        deleted = cleanup_merged_skill_memories()
        assert deleted == 3

        conn = sqlite3.connect(str(tmp_db))
        # 3 skill memories deleted, 2 remain (no flag) + 1 note
        assert conn.execute("SELECT COUNT(*) FROM memories WHERE source='skill'").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM memories WHERE source='note'").fetchone()[0] == 1
        conn.close()

    def test_cleanup_open_duplicate(self, tmp_db):
        """cleanup also covers open-duplicate conf>=0.7."""
        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            "INSERT INTO memory_flags (memory_id, flag_type, confidence, status) "
            "VALUES (4, 'duplicate', 0.75, 'open')"
        )
        conn.execute(
            "INSERT INTO memory_flags (memory_id, flag_type, confidence, status) "
            "VALUES (5, 'duplicate', 0.95, 'open')"
        )
        conn.commit()
        conn.close()

        deleted = cleanup_merged_skill_memories()
        assert deleted == 2

    def test_cleanup_idempotent(self, tmp_db):
        """Second cleanup call deletes 0 (already cleaned)."""
        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            "INSERT INTO memory_flags (memory_id, flag_type, confidence, status, resolution) "
            "VALUES (1, 'duplicate', 0.9, 'resolved', 'merge')"
        )
        conn.commit()
        conn.close()

        first = cleanup_merged_skill_memories()
        assert first == 1
        second = cleanup_merged_skill_memories()
        assert second == 0

    def test_cleanup_skips_non_skill(self, tmp_db):
        """cleanup does NOT delete non-skill memories even if flagged."""
        conn = sqlite3.connect(str(tmp_db))
        # Flag the note memory (id=6) as resolved-merge
        conn.execute(
            "INSERT INTO memory_flags (memory_id, flag_type, confidence, status, resolution) "
            "VALUES (6, 'duplicate', 0.9, 'resolved', 'merge')"
        )
        conn.commit()
        conn.close()

        deleted = cleanup_merged_skill_memories()
        assert deleted == 0  # non-skill, not deleted

        conn = sqlite3.connect(str(tmp_db))
        assert conn.execute("SELECT COUNT(*) FROM memories WHERE id=6").fetchone()[0] == 1
        conn.close()


class TestG1InitDbCleanup:
    """G1 fix: _init_db triggers merge cleanup on first run."""

    def test_init_db_deletes_merged_skill_memories(self, tmp_path, monkeypatch):
        """_init_db should delete skill memories flagged resolved-merge."""
        from agent.memory_fabric import _init_db
        db_path = tmp_path / "memory_index.db"
        monkeypatch.setattr("agent.memory_fabric._get_index_db", lambda: db_path)
        _init_db(db_path)

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        # Insert skill memory + merge flag
        conn.execute(
            "INSERT INTO memories(source, layer, type, pointer, fts_content, updated_at, lifecycle_tag) "
            "VALUES ('skill', 'procedural', 'skill_text', 'skill#g1', 'test', '2026-07-01', 'ephemeral')"
        )
        mem_id = conn.execute("SELECT id FROM memories WHERE pointer='skill#g1'").fetchone()[0]
        conn.execute(
            "INSERT INTO memory_flags(memory_id, flag_type, confidence, evidence, status, resolution, created_at) "
            "VALUES (?, 'duplicate', 0.95, 'test', 'resolved', 'merge', '2026-07-01T00:00:00')",
            (mem_id,),
        )
        # Clear marker to force cleanup re-run
        conn.execute("DELETE FROM schema_meta WHERE key='merge_cleanup_done'")
        conn.commit()
        conn.close()

        _init_db(db_path)  # re-run triggers cleanup

        conn2 = sqlite3.connect(str(db_path))
        count = conn2.execute("SELECT COUNT(*) FROM memories WHERE pointer='skill#g1'").fetchone()[0]
        marker = conn2.execute("SELECT value FROM schema_meta WHERE key='merge_cleanup_done'").fetchone()
        conn2.close()

        assert count == 0  # deleted
        assert marker is not None  # marker set

    def test_init_db_cleanup_idempotent(self, tmp_path, monkeypatch):
        """Second _init_db should not re-run cleanup (marker set)."""
        from agent.memory_fabric import _init_db
        db_path = tmp_path / "memory_index.db"
        monkeypatch.setattr("agent.memory_fabric._get_index_db", lambda: db_path)
        _init_db(db_path)

        import sqlite3
        # First cleanup run: insert skill + flag, clear marker, re-init
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO memories(source, layer, type, pointer, fts_content, updated_at, lifecycle_tag) "
            "VALUES ('skill', 'procedural', 'skill_text', 'skill#g1a', 'test1', '2026-07-01', 'ephemeral')"
        )
        mem_id = conn.execute("SELECT id FROM memories WHERE pointer='skill#g1a'").fetchone()[0]
        conn.execute(
            "INSERT INTO memory_flags(memory_id, flag_type, confidence, evidence, status, resolution, created_at) "
            "VALUES (?, 'duplicate', 0.95, 'test', 'resolved', 'merge', '2026-07-01T00:00:00')",
            (mem_id,),
        )
        conn.execute("DELETE FROM schema_meta WHERE key='merge_cleanup_done'")
        conn.commit()
        conn.close()

        _init_db(db_path)  # cleanup runs

        # Insert another skill + flag, DON'T clear marker
        conn2 = sqlite3.connect(str(db_path))
        conn2.execute(
            "INSERT INTO memories(source, layer, type, pointer, fts_content, updated_at, lifecycle_tag) "
            "VALUES ('skill', 'procedural', 'skill_text', 'skill#g1b', 'test2', '2026-07-01', 'ephemeral')"
        )
        mem_id2 = conn2.execute("SELECT id FROM memories WHERE pointer='skill#g1b'").fetchone()[0]
        conn2.execute(
            "INSERT INTO memory_flags(memory_id, flag_type, confidence, evidence, status, resolution, created_at) "
            "VALUES (?, 'duplicate', 0.95, 'test', 'resolved', 'merge', '2026-07-01T00:00:00')",
            (mem_id2,),
        )
        conn2.commit()
        conn2.close()

        _init_db(db_path)  # marker exists, cleanup skipped

        conn3 = sqlite3.connect(str(db_path))
        count = conn3.execute("SELECT COUNT(*) FROM memories WHERE pointer='skill#g1b'").fetchone()[0]
        conn3.close()

        assert count == 1  # not deleted
