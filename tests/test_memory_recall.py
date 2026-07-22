"""Tests for memory_recall — automatic context retrieval."""

import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.memory_recall import (
    _extract_keywords,
    recall_context,
    format_recall_for_prompt,
    load_and_format_recall,
    _query_recent_outcomes,
    _query_domain_stats,
    _query_emotion_snapshot,
    _MAX_BLOCK_CHARS,
    compute_richness,
    RichnessScore,
    _sigmoid,
)


def _create_test_self_model(db_path: Path):
    """Create a test self-model DB with sample data."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE IF NOT EXISTS outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL, task TEXT, action TEXT, tool TEXT,
        success INTEGER, details TEXT, duration REAL,
        domain TEXT, error_type TEXT, error_msg TEXT, role TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS anti_patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL, pattern TEXT, correct TEXT,
        domain TEXT, frequency INTEGER, last_seen REAL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS self_model (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL, metric TEXT, value REAL, details TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_type TEXT, strategy TEXT,
        success_rate_when_used REAL, times_used INTEGER, created REAL
    )""")

    now = time.time()
    now_iso = datetime.fromtimestamp(now).isoformat()

    # Insert outcomes with different domains
    for i in range(20):
        conn.execute(
            "INSERT INTO outcomes (timestamp, task, action, tool, success, details, duration, domain) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.fromtimestamp(now - i * 3600).isoformat(), f"write code for feature {i}", f"action_{i}",
             "write_file" if i % 2 == 0 else "read_file",
             1 if i % 3 != 0 else 0, "", 0.5 + i * 0.01, "代码管理"),
        )
    for i in range(10):
        conn.execute(
            "INSERT INTO outcomes (timestamp, task, action, tool, success, details, duration, domain) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.fromtimestamp(now - i * 3600).isoformat(), f"search for {i}", f"search_{i}",
             "grep_search", 1, "", 0.3, "搜索"),
        )

    # ── v_outcomes 视图（生产代码查 v_outcomes，由 evolution_manager 创建）
    # 测试需同步创建，否则 memory_recall 查询报 no such table: v_outcomes
    conn.execute("CREATE VIEW IF NOT EXISTS v_outcomes AS SELECT * FROM outcomes")

    conn.commit()
    conn.close()


def _create_test_fusion(db_path: Path):
    """Create a test fusion-state DB."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE IF NOT EXISTS emotional_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL,
        emotion TEXT,
        intensity REAL,
        valence REAL,
        arousal REAL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS evolution_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL,
        metric TEXT,
        value REAL
    )""")

    now = time.time()
    conn.execute(
        "INSERT INTO emotional_state (timestamp, emotion, intensity, valence, arousal) VALUES (?, ?, ?, ?, ?)",
        (now, "confident", 0.7, 0.5, 0.3),
    )
    conn.commit()
    conn.close()


class TestKeywordExtraction(unittest.TestCase):
    """Test keyword extraction from user messages."""

    def test_english_keywords(self):
        keywords = _extract_keywords("help me write a Python function to parse JSON")
        self.assertIn("python", keywords)
        self.assertIn("function", keywords)
        self.assertIn("parse", keywords)
        self.assertNotIn("the", keywords)
        self.assertNotIn("and", keywords)

    def test_chinese_keywords(self):
        keywords = _extract_keywords("帮我写一个Python函数来解析JSON数据")
        # Should extract Chinese 2-4 char tokens
        self.assertTrue(len(keywords) > 0)
        # Should also extract English tokens
        self.assertIn("python", [k.lower() for k in keywords])

    def test_empty_message(self):
        self.assertEqual(_extract_keywords(""), [])

    def test_only_stop_words(self):
        keywords = _extract_keywords("the and for are but not you all")
        self.assertEqual(keywords, [])

    def test_max_keywords_limit(self):
        keywords = _extract_keywords("python java ruby golang rust javascript typescript kotlin", max_keywords=3)
        self.assertLessEqual(len(keywords), 3)

    def test_mixed_language(self):
        keywords = _extract_keywords("写代码 with Python and database")
        self.assertTrue(any("写" in k or "代码" in k for k in keywords))
        self.assertIn("python", [k.lower() for k in keywords])
        self.assertIn("database", [k.lower() for k in keywords])


class TestRecallContext(unittest.TestCase):
    """Test context recall from multiple sources."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._sm_db = Path(self._tmpdir) / "evolution" / "self-model.db"
        self._sm_db.parent.mkdir(parents=True, exist_ok=True)
        _create_test_self_model(self._sm_db)

        self._fusion_db = Path(self._tmpdir) / "evolution" / "fusion-state.db"
        _create_test_fusion(self._fusion_db)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_recall_with_matching_keywords(self):
        with patch("agent.memory_recall._get_self_model_db", return_value=self._sm_db), \
             patch("agent.memory_recall._get_fusion_db", return_value=self._fusion_db):
            result = recall_context("write code for feature")
        self.assertIsNotNone(result)
        self.assertIn("recent_outcomes", result)
        self.assertIn("domain_stats", result)
        self.assertIn("emotion", result)

    def test_recall_with_no_matching_keywords(self):
        with patch("agent.memory_recall._get_self_model_db", return_value=self._sm_db), \
             patch("agent.memory_recall._get_fusion_db", return_value=self._fusion_db):
            result = recall_context("random unrelated query zzz")
        # Should still get emotion + recent outcomes (no keyword match → recent fallback)
        self.assertIsNotNone(result)

    def test_recall_with_no_db(self):
        with patch("agent.memory_recall._get_self_model_db", return_value=None), \
             patch("agent.memory_recall._get_fusion_db", return_value=None):
            result = recall_context("test")
        # Always returns a dict (richness + keywords), even with no DB
        self.assertIsNotNone(result)
        self.assertIn("keywords", result)
        self.assertIn("richness", result)

    def test_recall_returns_keywords(self):
        with patch("agent.memory_recall._get_self_model_db", return_value=self._sm_db), \
             patch("agent.memory_recall._get_fusion_db", return_value=self._fusion_db):
            result = recall_context("write Python code")
        self.assertIn("keywords", result)
        self.assertTrue(len(result["keywords"]) > 0)

    def test_domain_stats_correct(self):
        with patch("agent.memory_recall._get_self_model_db", return_value=self._sm_db), \
             patch("agent.memory_recall._get_fusion_db", return_value=self._fusion_db):
            result = recall_context("write code")
        domain_stats = result.get("domain_stats", [])
        self.assertTrue(len(domain_stats) > 0)
        # Should have "代码管理" domain
        domains = [ds["domain"] for ds in domain_stats]
        self.assertIn("代码管理", domains)

    def test_emotion_snapshot(self):
        with patch("agent.memory_recall._get_self_model_db", return_value=self._sm_db), \
             patch("agent.memory_recall._get_fusion_db", return_value=self._fusion_db):
            result = recall_context("test")
        emotion = result.get("emotion")
        self.assertIsNotNone(emotion)
        self.assertEqual(emotion["emotion"], "confident")
        self.assertGreater(emotion["intensity"], 0)


class TestFormatRecall(unittest.TestCase):
    """Test formatting of recalled context."""

    def test_format_with_all_sources(self):
        recall = {
            "keywords": ["python", "code"],
            "domain_stats": [
                {"domain": "代码管理", "total": 20, "success_rate": 0.85, "avg_duration": 0.6},
            ],
            "recent_outcomes": [
                {"task": "write code for feature 0", "tool": "write_file", "success": True, "domain": "代码管理", "duration": 0.5},
                {"task": "write code for feature 2", "tool": "write_file", "success": False, "domain": "代码管理", "duration": 0.52},
            ],
            "emotion": {"emotion": "confident", "intensity": 0.7, "valence": 0.5},
        }
        block = format_recall_for_prompt(recall)
        self.assertIn("<recalled_context>", block)
        self.assertIn("</recalled_context>", block)
        self.assertIn("代码管理", block)
        self.assertIn("write_file", block)
        self.assertIn("confident", block)

    def test_format_empty_recall(self):
        block = format_recall_for_prompt({})
        # Should be just wrapper tags → filtered to empty
        self.assertEqual(block, "")

    def test_format_only_emotion(self):
        recall = {
            "emotion": {"emotion": "curious", "intensity": 0.6, "valence": 0.4},
        }
        block = format_recall_for_prompt(recall)
        self.assertIn("curious", block)
        self.assertIn("<recalled_context>", block)

    def test_format_failures_emphasized(self):
        recall = {
            "recent_outcomes": [
                {"task": "failed task", "tool": "grep", "success": False, "domain": "搜索", "duration": 0.3},
                {"task": "success task", "tool": "read", "success": True, "domain": "搜索", "duration": 0.2},
            ],
        }
        block = format_recall_for_prompt(recall)
        self.assertIn("✗", block)
        self.assertIn("✓", block)

    def test_token_budget(self):
        big_recall = {
            "domain_stats": [
                {"domain": f"domain_{i}" * 5, "total": 100, "success_rate": 0.8, "avg_duration": 0.5}
                for i in range(20)
            ],
            "recent_outcomes": [
                {"task": f"task_{i}" * 20, "tool": f"tool_{i}", "success": i % 2 == 0, "domain": "test", "duration": 0.5}
                for i in range(20)
            ],
        }
        block = format_recall_for_prompt(big_recall)
        self.assertLessEqual(len(block), _MAX_BLOCK_CHARS + 50)


class TestLoadAndFormat(unittest.TestCase):
    """Test the convenience function."""

    def test_no_data_returns_empty(self):
        with patch("agent.memory_recall._get_self_model_db", return_value=None), \
             patch("agent.memory_recall._get_fusion_db", return_value=None):
            result = load_and_format_recall("test")
        self.assertEqual(result, "")

    def test_with_data_returns_block(self):
        tmpdir = tempfile.mkdtemp()
        try:
            sm_db = Path(tmpdir) / "evolution" / "self-model.db"
            sm_db.parent.mkdir(parents=True, exist_ok=True)
            _create_test_self_model(sm_db)

            fusion_db = Path(tmpdir) / "evolution" / "fusion-state.db"
            _create_test_fusion(fusion_db)

            with patch("agent.memory_recall._get_self_model_db", return_value=sm_db), \
                 patch("agent.memory_recall._get_fusion_db", return_value=fusion_db):
                block = load_and_format_recall("write code")
            self.assertIn("<recalled_context>", block)
            self.assertIn("代码管理", block)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestContextRichness(unittest.TestCase):
    """Tests for data-density-driven richness scoring."""

    def test_sigmoid_zero(self):
        self.assertEqual(_sigmoid(0, 500), 0.0)
        self.assertEqual(_sigmoid(0, 1), 0.0)
        self.assertEqual(_sigmoid(-1, 500), 0.0)

    def test_sigmoid_half_ref(self):
        # At half the reference, should be around 0.55 (ref=125 for ref=500)
        v = _sigmoid(125, 500)
        self.assertGreater(v, 0.5)
        self.assertLess(v, 0.65)

    def test_sigmoid_at_ref(self):
        # At reference, should be around 0.83
        v = _sigmoid(500, 500)
        self.assertGreater(v, 0.75)
        self.assertLess(v, 0.90)

    def test_sigmoid_monotonic(self):
        vals = [_sigmoid(x, 500) for x in [0, 50, 100, 200, 500, 1000]]
        for i in range(len(vals) - 1):
            self.assertLess(vals[i], vals[i + 1])

    def test_richness_no_db(self):
        """Without any DB, returns cold_start."""
        with patch("agent.memory_recall._get_self_model_db", return_value=None), \
             patch("agent.memory_recall._get_handoff_db", return_value=None):
            r = compute_richness()
        self.assertEqual(r.tier, "cold_start")
        self.assertEqual(r.value, 0.0)

    def test_richness_with_data(self):
        """With test data, computes non-zero score and correct tier."""
        tmpdir = tempfile.mkdtemp()
        try:
            # Create self-model DB with raw_events and clusters
            sm_db = Path(tmpdir) / "evolution" / "self-model.db"
            sm_db.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(sm_db))
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS raw_events (
                    id INTEGER PRIMARY KEY, session_id TEXT, tool_name TEXT);
                CREATE TABLE IF NOT EXISTS clusters (
                    id INTEGER PRIMARY KEY, lifecycle_stage TEXT);
            """)
            # 50 events, 3 stable clusters → should hit "learning" tier
            for i in range(50):
                conn.execute(
                    "INSERT INTO raw_events (session_id, tool_name) VALUES (?, ?)",
                    (f"session_{i % 4}", "terminal")
                )
            for i in range(3):
                conn.execute(
                    "INSERT INTO clusters (lifecycle_stage) VALUES (?)",
                    ("stable",)
                )
            conn.commit()
            conn.close()

            with patch("agent.memory_recall._get_self_model_db", return_value=sm_db), \
                 patch("agent.memory_recall._get_handoff_db", return_value=None):
                r = compute_richness()

            # 50 events / ref=500, 3 clusters / ref=10, 4 sessions / ref=20
            self.assertGreater(r.value, 0.1)
            self.assertGreaterEqual(r.raw_event_count, 50)
            self.assertGreaterEqual(r.stable_cluster_count, 3)
            self.assertEqual(r.session_count, 4)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_recall_includes_richness(self):
        """recall_context() always includes richness score."""
        with patch("agent.memory_recall._get_self_model_db", return_value=None), \
             patch("agent.memory_recall._get_fusion_db", return_value=None):
            result = recall_context("test message")
        self.assertIn("richness", result)
        self.assertIn("_recall_depth", result)
        self.assertEqual(result["_recall_depth"], "minimal")

    def test_richness_repr(self):
        r = RichnessScore(value=0.5, tier="learning", raw_event_count=100)
        s = repr(r)
        self.assertIn("0.500", s)
        self.assertIn("learning", s)


if __name__ == "__main__":
    unittest.main()
