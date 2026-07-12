"""Tests for session_handoff — cross-session task continuity."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure we can import agent modules
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.session_handoff import (
    generate_and_store_handoff,
    load_handoff_for_new_session,
    format_handoff_for_prompt,
    _extract_user_request,
    _extract_tools_used,
    _extract_decisions,
    _extract_pending_tasks,
    _extract_open_questions,
    _build_summary_text,
)
from agent import handoff_store


class TestExtractors(unittest.TestCase):
    """Test deterministic extraction functions."""

    def test_extract_user_request_first_user_message(self):
        messages = [
            {"role": "user", "content": "帮我写一个Python脚本"},
            {"role": "assistant", "content": "好的"},
        ]
        result = _extract_user_request(messages)
        self.assertEqual(result, "帮我写一个Python脚本")

    def test_extract_user_request_strips_xml_tags(self):
        messages = [
            {"role": "user", "content": "<skill>content</skill>帮我分析数据"},
        ]
        result = _extract_user_request(messages)
        self.assertIn("帮我分析数据", result)

    def test_extract_user_request_no_user_message(self):
        messages = [{"role": "assistant", "content": "hello"}]
        result = _extract_user_request(messages)
        self.assertEqual(result, "")

    def test_extract_tools_used_aggregates_counts(self):
        messages = [
            {"role": "tool", "tool_name": "shell", "content": "output"},
            {"role": "tool", "tool_name": "shell", "content": "output2"},
            {"role": "tool", "tool_name": "shell", "content": "Error: failed"},
            {"role": "tool", "tool_name": "search", "content": "results"},
        ]
        result = _extract_tools_used(messages)
        self.assertEqual(len(result), 2)
        # shell should be first (higher count)
        self.assertEqual(result[0]["name"], "shell")
        self.assertEqual(result[0]["count"], 3)
        self.assertEqual(result[0]["success"], 2)
        self.assertEqual(result[0]["failed"], 1)

    def test_extract_decisions_finds_keywords(self):
        messages = [
            {"role": "assistant", "content": "我决定使用FastAPI作为后端框架。"},
            {"role": "assistant", "content": "普通回复，没有决策"},
        ]
        result = _extract_decisions(messages)
        self.assertEqual(len(result), 1)
        self.assertIn("FastAPI", result[0]["decision"])

    def test_extract_decisions_english_keywords(self):
        messages = [
            {"role": "assistant", "content": "I decided to use PostgreSQL for the database."},
        ]
        result = _extract_decisions(messages)
        self.assertEqual(len(result), 1)
        self.assertIn("PostgreSQL", result[0]["decision"])

    def test_extract_pending_tasks_finds_markers(self):
        messages = [
            {"role": "assistant", "content": "工作完成了。"},
            {"role": "assistant", "content": "下一步需要部署到服务器\n还需要写测试"},
        ]
        result = _extract_pending_tasks(messages)
        self.assertEqual(len(result), 2)
        self.assertIn("部署", result[0]["task"])
        self.assertIn("测试", result[1]["task"])

    def test_extract_open_questions(self):
        messages = [
            {"role": "assistant", "content": "这个方案是否可行？需要进一步验证。"},
        ]
        result = _extract_open_questions(messages)
        self.assertGreaterEqual(len(result), 1)
        self.assertIn("可行", result[0])

    def test_build_summary_text_with_content(self):
        summary = _build_summary_text(
            user_request="写脚本",
            tools_used=[{"name": "shell", "count": 5, "success": 4, "failed": 1}],
            decisions=[{"decision": "用Python", "keyword": "决定"}],
            pending_tasks=[{"task": "写测试"}],
            open_questions=[],
        )
        self.assertIn("写脚本", summary)
        self.assertIn("Python", summary)
        self.assertIn("写测试", summary)

    def test_build_summary_text_empty(self):
        summary = _build_summary_text("", [], [], [], [])
        self.assertEqual(summary, "(无显著内容)")

    def test_build_summary_text_fallback_to_tools(self):
        summary = _build_summary_text(
            "", [{"name": "search", "count": 3, "success": 3, "failed": 0}],
            [], [], []
        )
        self.assertIn("search", summary)


class TestHandoffStore(unittest.TestCase):
    """Test SQLite storage layer."""

    def setUp(self):
        """Use a temp directory for test DB."""
        self._tmpdir = tempfile.mkdtemp()
        self._orig_db_path = handoff_store._DB_PATH
        handoff_store._DB_PATH = Path(self._tmpdir) / "test_handoffs.db"

    def tearDown(self):
        handoff_store._DB_PATH = self._orig_db_path

    def test_store_and_retrieve_handoff(self):
        row_id = handoff_store.store_handoff(
            "test-session-1",
            user_request="test request",
            tools_used=[{"name": "shell", "count": 1, "success": 1, "failed": 0}],
            decisions=[{"decision": "test decision"}],
            pending_tasks=[{"task": "test task"}],
            open_questions=["test question?"],
            summary_text="test summary",
        )
        self.assertGreater(row_id, 0)

        result = handoff_store.get_latest_handoff("test-session-1")
        self.assertIsNotNone(result)
        self.assertEqual(result["user_request"], "test request")
        self.assertEqual(result["summary_text"], "test summary")
        self.assertEqual(len(result["tools_used"]), 1)
        self.assertEqual(result["tools_used"][0]["name"], "shell")

    def test_get_global_latest_handoff(self):
        """Most recent handoff across all sessions."""
        handoff_store.store_handoff("session-a", summary_text="first")
        time.sleep(0.01)
        handoff_store.store_handoff("session-b", summary_text="second")

        result = handoff_store.get_global_latest_handoff()
        self.assertIsNotNone(result)
        self.assertEqual(result["summary_text"], "second")

    def test_get_latest_handoff_empty_db(self):
        result = handoff_store.get_global_latest_handoff()
        self.assertIsNone(result)

    def test_mark_superseded(self):
        row_id_1 = handoff_store.store_handoff("s1", summary_text="old")
        row_id_2 = handoff_store.store_handoff("s1", summary_text="new")
        handoff_store.mark_superseded(row_id_1, row_id_2)

        # Old handoff should not be returned (superseded)
        result = handoff_store.get_latest_handoff("s1")
        self.assertIsNotNone(result)
        self.assertEqual(result["summary_text"], "new")


class TestHandoffIntegration(unittest.TestCase):
    """Test the full generate → store → load → format pipeline."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_db_path = handoff_store._DB_PATH
        handoff_store._DB_PATH = Path(self._tmpdir) / "test_handoffs.db"

    def tearDown(self):
        handoff_store._DB_PATH = self._orig_db_path

    def test_generate_and_store_then_load(self):
        messages = [
            {"role": "user", "content": "帮我搭建一个Web服务器"},
            {"role": "assistant", "content": "好的，我决定使用FastAPI。"},
            {"role": "tool", "tool_name": "shell", "content": "FastAPI installed"},
            {"role": "assistant", "content": "服务器已搭建。下一步需要配置Nginx。"},
        ]

        row_id = generate_and_store_handoff(messages, "test-session")
        self.assertGreater(row_id, 0)

        loaded = load_handoff_for_new_session()
        self.assertIsNotNone(loaded)
        self.assertIn("Web服务器", loaded["user_request"])
        self.assertIn("FastAPI", loaded["decisions"][0]["decision"])
        self.assertTrue(len(loaded["pending_tasks"]) > 0)

    def test_format_handoff_for_prompt(self):
        handoff = {
            "summary_text": "上次会话主题: 搭建服务器",
            "tools_used": [{"name": "shell", "count": 3, "success": 2, "failed": 1}],
            "decisions": [{"decision": "用FastAPI"}],
            "pending_tasks": [{"task": "配置Nginx"}],
            "open_questions": ["是否需要SSL？"],
        }
        result = format_handoff_for_prompt(handoff)
        self.assertIn("<previous_session_summary>", result)
        self.assertIn("</previous_session_summary>", result)
        self.assertIn("搭建服务器", result)
        self.assertIn("shell", result)
        self.assertIn("配置Nginx", result)
        self.assertIn("SSL", result)

    def test_load_handoff_empty_db(self):
        result = load_handoff_for_new_session()
        self.assertIsNone(result)

    def test_load_handoff_too_old(self):
        """Handoffs older than 7 days should not be loaded."""
        handoff_store.store_handoff("old-session", summary_text="old")
        # Manually update the timestamp to 8 days ago
        with handoff_store._conn() as conn:
            old_time = time.time() - 8 * 86400
            conn.execute(
                "UPDATE session_handoffs SET created_at = ? WHERE summary_text = ?",
                (old_time, "old"),
            )
            conn.commit()

        result = load_handoff_for_new_session()
        self.assertIsNone(result)

    def test_generate_and_store_empty_messages(self):
        row_id = generate_and_store_handoff([], "test")
        self.assertEqual(row_id, -1)


if __name__ == "__main__":
    unittest.main()
