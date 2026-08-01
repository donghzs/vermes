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
