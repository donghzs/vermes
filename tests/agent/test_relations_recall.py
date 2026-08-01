"""Tests for relations recall — session_id bridge to strategies/anti_patterns."""
import sqlite3
import tempfile
from pathlib import Path
import pytest

from agent.memory_recall import _collect_relation_snippets


def _make_self_model_db(tmp_path):
    """Create a self-model.db with schema + test data."""
    db_path = tmp_path / "self-model.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # outcomes
    conn.execute("""
        CREATE TABLE outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, task TEXT, action TEXT, tool TEXT,
            success INTEGER, details TEXT, duration REAL,
            domain TEXT, error_type TEXT, error_msg TEXT, role TEXT
        )
    """)
    # strategies
    conn.execute("""
        CREATE TABLE strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT, strategy TEXT,
            success_rate_when_used REAL, times_used INTEGER, created TEXT
        )
    """)
    # anti_patterns
    conn.execute("""
        CREATE TABLE anti_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, pattern TEXT, correct INTEGER,
            domain TEXT, frequency INTEGER, last_seen TEXT
        )
    """)
    # relations (source_type='outcome', target_type='strategy'/'anti_pattern'/'emotional_state')
    conn.execute("""
        CREATE TABLE relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT, source_id INTEGER,
            target_type TEXT, target_id INTEGER,
            rel_type TEXT, weight REAL, timestamp TEXT
        )
    """)

    # Insert test data
    conn.execute("INSERT INTO outcomes (timestamp, task, success) VALUES ('2026-08-01T10:00:00', 'deploy', 1)")
    conn.execute("INSERT INTO outcomes (timestamp, task, success) VALUES ('2026-08-01T11:00:00', 'test', 0)")
    outcome_ids = [1, 2]

    conn.execute("INSERT INTO strategies (task_type, strategy, success_rate_when_used, times_used) VALUES ('deploy', 'Use blue-green deployment', 0.92, 15)")
    conn.execute("INSERT INTO strategies (task_type, strategy, success_rate_when_used, times_used) VALUES ('test', 'Run integration tests first', 0.85, 10)")

    conn.execute("INSERT INTO anti_patterns (timestamp, pattern, correct, domain, frequency) VALUES ('2026-08-01', 'Skip tests when rushing', 0, 'testing', 3)")

    # relations: outcome 1 → strategy 1, outcome 2 → strategy 2, outcome 2 → anti_pattern 1
    conn.execute("INSERT INTO relations (source_type, source_id, target_type, target_id, rel_type, weight) VALUES ('outcome', 1, 'strategy', 1, 'used_strategy', 0.8)")
    conn.execute("INSERT INTO relations (source_type, source_id, target_type, target_id, rel_type, weight) VALUES ('outcome', 2, 'strategy', 2, 'used_strategy', 0.6)")
    conn.execute("INSERT INTO relations (source_type, source_id, target_type, target_id, rel_type, weight) VALUES ('outcome', 2, 'anti_pattern', 1, 'violated', 0.7)")
    # emotional_state relation (should be skipped — phantom table)
    conn.execute("INSERT INTO relations (source_type, source_id, target_type, target_id, rel_type, weight) VALUES ('outcome', 1, 'emotional_state', 999, 'caused_emotion', 0.5)")

    conn.commit()
    conn.close()
    return db_path


class TestCollectRelationSnippets:

    def test_returns_strategy_and_anti_pattern(self, tmp_path):
        db_path = _make_self_model_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        snippets = _collect_relation_snippets(conn, limit=3)
        assert len(snippets) == 3  # 2 strategies + 1 anti_pattern (emotional_state skipped)

        types = [s["type"] for s in snippets]
        assert "strategy" in types
        assert "anti_pattern" in types

        # Strategy content
        strat = [s for s in snippets if s["type"] == "strategy"]
        assert any("blue-green" in s["content"] for s in strat)
        assert any("integration tests" in s["content"] for s in strat)

        # Anti-pattern content
        ap = [s for s in snippets if s["type"] == "anti_pattern"]
        assert any("Skip tests" in s["content"] for s in ap)

        conn.close()

    def test_skips_emotional_state(self, tmp_path):
        """emotional_state target_type should be silently skipped (phantom table)."""
        db_path = _make_self_model_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        snippets = _collect_relation_snippets(conn, limit=10)
        assert all(s["type"] in ("strategy", "anti_pattern") for s in snippets)
        assert len(snippets) == 3  # not 4

        conn.close()

    def test_empty_db(self, tmp_path):
        """No outcomes → empty list."""
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE outcomes (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE strategies (id INTEGER PRIMARY KEY, strategy TEXT)")
        conn.execute("CREATE TABLE anti_patterns (id INTEGER PRIMARY KEY, pattern TEXT)")
        conn.execute("CREATE TABLE relations (source_type TEXT, source_id INTEGER, target_type TEXT, target_id INTEGER, rel_type TEXT, weight REAL)")
        conn.commit()

        snippets = _collect_relation_snippets(conn, limit=3)
        assert snippets == []
        conn.close()

    def test_limit_respected(self, tmp_path):
        db_path = _make_self_model_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        snippets = _collect_relation_snippets(conn, limit=1)
        assert len(snippets) == 1

        conn.close()

    def test_fail_open_on_error(self, tmp_path):
        """If tables don't exist, returns empty list (fail-open)."""
        db_path = tmp_path / "broken.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # No tables created

        snippets = _collect_relation_snippets(conn, limit=3)
        assert snippets == []
        conn.close()
