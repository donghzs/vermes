"""Tests for decision_tracker — decision recording and contradiction detection."""

import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.decision_tracker import (
    record_decision,
    get_active_decisions,
    get_superseded_decisions,
    format_decisions_for_prompt,
    _extract_decision_keywords,
    _check_contradiction,
    _DECISIONS_SCHEMA,
)


def _ensure_test_db(db_path: Path):
    """Create the decisions table in a test DB."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(_DECISIONS_SCHEMA)
    conn.commit()
    conn.close()


class TestKeywordExtraction(unittest.TestCase):
    """Test keyword extraction for decisions."""

    def test_chinese_decision(self):
        keywords = _extract_decision_keywords("使用 PostgreSQL 作为主数据库")
        self.assertTrue(len(keywords) > 0)

    def test_english_decision(self):
        keywords = _extract_decision_keywords("Use PostgreSQL as the primary database")
        self.assertIn("postgresql", [k.lower() for k in keywords])
        self.assertIn("primary", [k.lower() for k in keywords])
        self.assertIn("database", [k.lower() for k in keywords])

    def test_mixed_decision(self):
        keywords = _extract_decision_keywords("用 FastAPI 替换 Flask 后端")
        self.assertTrue(any("替换" in k or "后端" in k for k in keywords))
        self.assertIn("fastapi", [k.lower() for k in keywords])
        self.assertIn("flask", [k.lower() for k in keywords])

    def test_empty(self):
        self.assertEqual(_extract_decision_keywords(""), [])

    def test_max_keywords(self):
        keywords = _extract_decision_keywords(
            "python java ruby golang rust javascript typescript kotlin scala"
        )
        self.assertLessEqual(len(keywords), 8)


class TestContradictionDetection(unittest.TestCase):
    """Test contradiction detection logic."""

    def test_negation_contradiction(self):
        reason = _check_contradiction(
            "不要使用 PostgreSQL",
            "使用 PostgreSQL 作为主数据库",
            ["postgresql"],
            ["postgresql"],
        )
        self.assertIsNotNone(reason)
        self.assertIn("Negation", reason)

    def test_revision_contradiction(self):
        reason = _check_contradiction(
            "改为使用 MySQL",
            "使用 PostgreSQL 作为主数据库",
            ["mysql", "postgresql"],
            ["postgresql"],
        )
        self.assertIsNotNone(reason)

    def test_no_contradiction_different_topics(self):
        reason = _check_contradiction(
            "使用 React 作为前端框架",
            "使用 PostgreSQL 作为主数据库",
            ["react", "前端"],
            ["postgresql", "数据库"],
        )
        self.assertIsNone(reason)

    def test_no_contradiction_same_decision(self):
        reason = _check_contradiction(
            "使用 PostgreSQL 作为主数据库",
            "使用 PostgreSQL 作为主数据库",
            ["postgresql", "数据库"],
            ["postgresql", "数据库"],
        )
        # Same decision shouldn't contradict itself (no negation/revision patterns)
        self.assertIsNone(reason)

    def test_tool_shift_contradiction(self):
        reason = _check_contradiction(
            "Use MySQL instead",
            "Use PostgreSQL as the primary database",
            ["mysql"],
            ["postgresql"],
        )
        # No keyword overlap → no contradiction
        # Wait — MySQL and PostgreSQL are different keywords, no overlap
        # This should return None (no overlap)
        self.assertIsNone(reason)

    def test_tool_shift_with_overlap(self):
        reason = _check_contradiction(
            "Replace PostgreSQL with MySQL for the database",
            "Use PostgreSQL as the primary database",
            ["postgresql", "mysql", "database"],
            ["postgresql", "database"],
        )
        # Has overlap (postgresql, database) + revision pattern "replace"
        self.assertIsNotNone(reason)


class TestRecordDecision(unittest.TestCase):
    """Test decision recording."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = Path(self._tmpdir) / "evolution" / "self-model.db"
        _ensure_test_db(self._db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_record_simple_decision(self):
        with patch("agent.decision_tracker._get_self_model_db", return_value=self._db_path):
            result = record_decision("Use PostgreSQL as primary database", "architecture discussion")
        self.assertGreater(result["id"], 0)
        self.assertEqual(len(result["contradicted"]), 0)

    def test_record_contradicting_decision(self):
        with patch("agent.decision_tracker._get_self_model_db", return_value=self._db_path):
            # First decision
            record_decision("Use PostgreSQL as primary database")
            # Contradicting decision
            result = record_decision("不要使用 PostgreSQL，改用 MySQL")
        self.assertGreater(result["id"], 0)
        self.assertGreater(len(result["contradicted"]), 0)

    def test_record_empty_decision(self):
        with patch("agent.decision_tracker._get_self_model_db", return_value=self._db_path):
            result = record_decision("")
        self.assertEqual(result["id"], -1)

    def test_record_no_db(self):
        with patch("agent.decision_tracker._get_self_model_db", return_value=None):
            result = record_decision("test decision")
        self.assertEqual(result["id"], -1)


class TestGetDecisions(unittest.TestCase):
    """Test decision retrieval."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = Path(self._tmpdir) / "evolution" / "self-model.db"
        _ensure_test_db(self._db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_get_active_decisions(self):
        with patch("agent.decision_tracker._get_self_model_db", return_value=self._db_path):
            record_decision("Decision 1", session_id="s1")
            record_decision("Decision 2", session_id="s2")
            decisions = get_active_decisions()
        self.assertEqual(len(decisions), 2)
        # Most recent first
        self.assertEqual(decisions[0]["decision"], "Decision 2")

    def test_get_superseded(self):
        with patch("agent.decision_tracker._get_self_model_db", return_value=self._db_path):
            record_decision("Use PostgreSQL as primary database")
            record_decision("不要使用 PostgreSQL，改用 MySQL")
            superseded = get_superseded_decisions()
        self.assertEqual(len(superseded), 1)
        self.assertIn("PostgreSQL", superseded[0]["decision"])
        self.assertIsNotNone(superseded[0]["contradiction_reason"])

    def test_empty_decisions(self):
        with patch("agent.decision_tracker._get_self_model_db", return_value=self._db_path):
            decisions = get_active_decisions()
        self.assertEqual(decisions, [])

    def test_limit_respected(self):
        with patch("agent.decision_tracker._get_self_model_db", return_value=self._db_path):
            for i in range(10):
                record_decision(f"Decision {i}")
            decisions = get_active_decisions(limit=3)
        self.assertEqual(len(decisions), 3)


class TestFormatDecisions(unittest.TestCase):
    """Test decision formatting for prompt."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = Path(self._tmpdir) / "evolution" / "self-model.db"
        _ensure_test_db(self._db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_format_with_decisions(self):
        with patch("agent.decision_tracker._get_self_model_db", return_value=self._db_path):
            record_decision("Use PostgreSQL as primary database")
            block = format_decisions_for_prompt()
        self.assertIn("<active_decisions>", block)
        self.assertIn("PostgreSQL", block)
        self.assertIn("Standing decisions", block)

    def test_format_empty(self):
        with patch("agent.decision_tracker._get_self_model_db", return_value=self._db_path):
            block = format_decisions_for_prompt()
        self.assertEqual(block, "")

    def test_format_with_age(self):
        with patch("agent.decision_tracker._get_self_model_db", return_value=self._db_path):
            # Insert a decision with old timestamp
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "INSERT INTO decisions (session_id, timestamp, decision, context, status, keywords) "
                "VALUES (?, ?, ?, ?, 'active', ?)",
                ("s1", time.time() - 48 * 3600, "Old decision", "", "old"),
            )
            conn.commit()
            conn.close()

            block = format_decisions_for_prompt()
        self.assertIn("2d ago", block)


if __name__ == "__main__":
    unittest.main()
