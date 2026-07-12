"""Tests for evolution_injector — learned experience injection."""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.evolution_injector import (
    load_evolution_context,
    format_evolution_for_prompt,
    load_and_format_evolution,
    _is_too_generic,
    _MAX_BLOCK_CHARS,
)


def _create_test_db(db_path: Path):
    """Create a test evolution DB with sample data."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE IF NOT EXISTS outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL,
        task TEXT, action TEXT, tool TEXT,
        success INTEGER, details TEXT,
        duration REAL, domain TEXT,
        error_type TEXT, error_msg TEXT, role TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS anti_patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL,
        pattern TEXT, correct TEXT,
        domain TEXT, frequency INTEGER, last_seen REAL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS self_model (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL,
        metric TEXT, value REAL, details TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_type TEXT, strategy TEXT,
        success_rate_when_used REAL, times_used INTEGER, created REAL
    )""")

    now = time.time()

    # Insert anti_patterns
    conn.execute(
        "INSERT INTO anti_patterns (timestamp, pattern, correct, domain, frequency, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
        (now, "不要在未读源码前修改文件", "先 read_file 审计源码", "代码管理", 42, now),
    )
    conn.execute(
        "INSERT INTO anti_patterns (timestamp, pattern, correct, domain, frequency, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
        (now, "检查错误信息，分析根因", "检查错误信息", "通用", 586, now),  # generic, should be filtered
    )

    # Insert strategies
    conn.execute(
        "INSERT INTO strategies (task_type, strategy, success_rate_when_used, times_used, created) VALUES (?, ?, ?, ?, ?)",
        ("文件读取", "read_file:文件读取", 0.85, 20, now),
    )
    conn.execute(
        "INSERT INTO strategies (task_type, strategy, success_rate_when_used, times_used, created) VALUES (?, ?, ?, ?, ?)",
        ("搜索", "grep_search:搜索", 0.92, 15, now),
    )

    # Insert self_model metrics
    conn.execute(
        "INSERT INTO self_model (timestamp, metric, value, details) VALUES (?, ?, ?, ?)",
        (now, "tool.success", 0.88, "overall"),
    )
    conn.execute(
        "INSERT INTO self_model (timestamp, metric, value, details) VALUES (?, ?, ?, ?)",
        (now, "tool.duration", 1.5, "avg_ms"),
    )

    # Insert recent outcomes
    for i in range(50):
        conn.execute(
            "INSERT INTO outcomes (timestamp, task, action, tool, success, details, duration, domain) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now - i * 100, f"task_{i}", f"action_{i}", "shell", 1 if i % 5 != 0 else 0, "", 0.5 + i * 0.01, "通用"),
        )

    conn.commit()
    conn.close()


class TestEvolutionInjector(unittest.TestCase):
    """Test evolution data loading and formatting."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = Path(self._tmpdir) / "evolution" / "self-model.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        _create_test_db(self._db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_load_evolution_context_with_data(self):
        with patch("agent.evolution_injector._get_evolution_db", return_value=self._db_path):
            result = load_evolution_context("test")
        self.assertIsNotNone(result)
        self.assertIn("anti_patterns", result)
        self.assertIn("strategies", result)
        self.assertIn("self_model", result)
        self.assertIn("recent_summary", result)

    def test_generic_anti_patterns_filtered(self):
        """Generic patterns like '检查错误信息，分析根因' should be filtered."""
        with patch("agent.evolution_injector._get_evolution_db", return_value=self._db_path):
            result = load_evolution_context("test")
        patterns = [ap["pattern"] for ap in result["anti_patterns"]]
        self.assertNotIn("检查错误信息，分析根因", patterns)
        self.assertIn("不要在未读源码前修改文件", patterns)

    def test_strategies_sorted_by_success(self):
        with patch("agent.evolution_injector._get_evolution_db", return_value=self._db_path):
            result = load_evolution_context("test")
        strats = result["strategies"]
        self.assertGreaterEqual(len(strats), 1)
        # Higher success rate should come first
        self.assertGreaterEqual(strats[0]["success_rate"], strats[-1]["success_rate"])

    def test_recent_summary_correct(self):
        with patch("agent.evolution_injector._get_evolution_db", return_value=self._db_path):
            result = load_evolution_context("test")
        summary = result["recent_summary"]
        self.assertEqual(summary["total_actions"], 50)
        self.assertGreater(summary["success_rate"], 0.7)
        self.assertEqual(summary["period_days"], 7)

    def test_format_evolution_for_prompt(self):
        evolution = {
            "anti_patterns": [
                {"pattern": "不要跳过验证", "correct": "先验证再继续", "domain": "通用", "frequency": 10},
            ],
            "strategies": [
                {"task_type": "搜索", "strategy": "grep_search", "success_rate": 0.9, "times_used": 15},
            ],
            "self_model": {"tool.success": 0.88},
            "recent_summary": {"total_actions": 50, "success_rate": 0.8, "avg_duration_ms": 1.5, "period_days": 7},
        }
        block = format_evolution_for_prompt(evolution)
        self.assertIn("<learned_experience>", block)
        self.assertIn("</learned_experience>", block)
        self.assertIn("不要跳过验证", block)
        self.assertIn("grep_search", block)
        self.assertIn("tool.success", block)
        self.assertIn("50 actions", block)

    def test_format_empty_evolution(self):
        block = format_evolution_for_prompt({})
        self.assertIn("<learned_experience>", block)
        self.assertIn("</learned_experience>", block)

    def test_load_and_format_integration(self):
        with patch("agent.evolution_injector._get_evolution_db", return_value=self._db_path):
            block = load_and_format_evolution("test")
        self.assertIn("<learned_experience>", block)
        self.assertIn("不要在未读源码前修改文件", block)

    def test_load_with_no_db(self):
        with patch("agent.evolution_injector._get_evolution_db", return_value=None):
            result = load_evolution_context("test")
        self.assertIsNone(result)

    def test_load_and_format_no_db(self):
        with patch("agent.evolution_injector._get_evolution_db", return_value=None):
            block = load_and_format_evolution("test")
        self.assertEqual(block, "")

    def test_is_too_generic(self):
        self.assertTrue(_is_too_generic("检查错误信息，分析根因"))
        self.assertTrue(_is_too_generic("检查错误信息"))
        self.assertFalse(_is_too_generic("不要在未读源码前修改文件"))
        self.assertFalse(_is_too_generic("跳过了工具调用"))

    def test_token_budget(self):
        """Block should not exceed max chars."""
        # Create large evolution data
        big_evolution = {
            "anti_patterns": [
                {"pattern": f"pattern_{i}" * 20, "correct": f"correct_{i}" * 20, "domain": "test", "frequency": i}
                for i in range(50)
            ],
            "strategies": [
                {"task_type": f"type_{i}", "strategy": f"strategy_{i}" * 20, "success_rate": 0.9, "times_used": i}
                for i in range(50)
            ],
            "self_model": {f"metric_{i}": 0.5 + i * 0.01 for i in range(50)},
            "recent_summary": {"total_actions": 999, "success_rate": 0.8, "avg_duration_ms": 1.5, "period_days": 7},
        }
        block = format_evolution_for_prompt(big_evolution)
        self.assertLessEqual(len(block), _MAX_BLOCK_CHARS + 50)  # +50 for closing tag


if __name__ == "__main__":
    unittest.main()
