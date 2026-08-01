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
    _extract_key_sentences,
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

    def test_build_summary_text_keeps_key_sentences(self):
        """P1-⑤: 关键句（top-N 长 assistant 消息）应进入摘要作为语料留存。"""
        messages = [
            {"role": "user", "content": "研究一下存储方案"},
            {"role": "assistant", "content": "我们评估了 Postgres 与 MySQL，最终决定采用 Postgres 作为主存储，因为它对 JSONB 与事务的完整支持更契合我们的需求。"},
            {"role": "assistant", "content": "短句。"},
            {"role": "assistant", "content": "索引层选用 trigram 分词器，兼顾中英文混合查询的召回率，避免定长切窗带来的错位问题。"},
            {"role": "assistant", "content": "连接池层面做了调优以提升并发吞吐，峰值压测下稳定支撑每秒数千次事务。"},
        ]
        key_sentences = _extract_key_sentences(messages, top_n=3, max_chars=200)
        self.assertEqual(len(key_sentences), 3)
        summary = _build_summary_text(
            user_request="研究一下存储方案",
            tools_used=[],
            decisions=[{"decision": "决定采用 Postgres 作为主存储"}],
            pending_tasks=[],
            open_questions=[],
            key_sentences=key_sentences,
        )
        self.assertIn("关键句:", summary)
        # 最长两条正文应被保留为关键句
        self.assertTrue(
            any("Postgres" in s for s in key_sentences)
        )
        self.assertTrue(
            any("trigram" in s for s in key_sentences)
        )
        # 关键句应出现在最终摘要里
        self.assertIn("Postgres", summary)
        self.assertIn("trigram", summary)

    def test_extract_key_sentences_truncates_long(self):
        long_msg = "x" * 500
        messages = [
            {"role": "assistant", "content": long_msg},
            {"role": "assistant", "content": "短。"},
        ]
        key_sentences = _extract_key_sentences(messages, top_n=3, max_chars=200)
        self.assertEqual(len(key_sentences), 1)
        # 超过 max_chars 应被截断并带省略号
        self.assertTrue(key_sentences[0].endswith("…"))
        self.assertLessEqual(len(key_sentences[0]), 201)


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

    def test_generate_stores_key_sentences_in_summary(self):
        """P1-⑤ 端到端：长 assistant 消息应作为关键句写入 summary_text。"""
        long_decision = (
            "经过对 Postgres 与 MySQL 的对比评估，我们最终决定采用 Postgres "
            "作为主存储引擎，主要考量是它对 JSONB 类型与复杂事务的完整支持。"
        )
        messages = [
            {"role": "user", "content": "研究存储方案"},
            {"role": "assistant", "content": long_decision},
        ]
        row_id = generate_and_store_handoff(messages, "test-session-ks")
        self.assertGreater(row_id, 0)

        loaded = handoff_store.get_latest_handoff("test-session-ks")
        self.assertIsNotNone(loaded)
        summary = loaded["summary_text"]
        self.assertIn("关键句:", summary)
        self.assertIn("Postgres", summary)
        # 关键词也应能从拼接后的 summary 中受益
        self.assertTrue(len(loaded["keywords"]) > 0)

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


class TestMultiSessionRelevance(unittest.TestCase):
    """Test that handoff selection respects task relevance.

    Scenario: user has two parallel sessions about different topics.
    New session should get the handoff matching its query, not just
    the most recent one.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_db_path = handoff_store._DB_PATH
        handoff_store._DB_PATH = Path(self._tmpdir) / "session_handoffs.db"

    def tearDown(self):
        handoff_store._DB_PATH = self._orig_db_path
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _store_handoff(self, session_id, user_request, summary_text, keywords):
        """Helper to store a handoff with keywords."""
        handoff_store.store_handoff(
            session_id,
            user_request=user_request,
            summary_text=summary_text,
            keywords=keywords,
        )

    def test_relevant_handoff_selected_over_latest(self):
        """When two sessions exist, new session gets the matching one."""
        # Session A: trading system (older)
        self._store_handoff(
            "session-a",
            "调试量化交易系统bug",
            "修复了 live_trader.py 的 NOMUSDT 计算问题",
            ["量化交易", "live_trader", "bug"],
        )

        # Session B: content writing (newer)
        self._store_handoff(
            "session-b",
            "写公众号文章关于AI Agent",
            "完成了AI Agent自进化技术文章草稿",
            ["公众号", "文章", "agent"],
        )

        # New session about trading should get session-a, not session-b
        result = handoff_store.get_relevant_handoff("交易系统 bug 修复")
        self.assertIsNotNone(result)
        self.assertEqual(result["session_id"], "session-a")

    def test_content_query_gets_content_handoff(self):
        """Content query should match content session, not trading."""
        self._store_handoff(
            "session-a",
            "调试量化交易系统",
            "修复交易bug",
            ["量化交易", "bug"],
        )
        self._store_handoff(
            "session-b",
            "写公众号文章",
            "完成文章草稿",
            ["公众号", "文章"],
        )

        result = handoff_store.get_relevant_handoff("帮我写文章")
        self.assertIsNotNone(result)
        self.assertEqual(result["session_id"], "session-b")

    def test_no_keyword_match_falls_back_to_latest(self):
        """When query doesn't match any handoff, return latest."""
        self._store_handoff(
            "session-a",
            "quantitative trading",
            "fixed trading bug",
            ["trading", "bug"],
        )
        self._store_handoff(
            "session-b",
            "article writing",
            "drafted article",
            ["article", "writing"],
        )

        # Query about cooking — no match, should get latest (session-b)
        result = handoff_store.get_relevant_handoff("how to cook pasta")
        self.assertIsNotNone(result)
        self.assertEqual(result["session_id"], "session-b")

    def test_empty_query_returns_latest(self):
        """Empty query should fall back to latest handoff."""
        self._store_handoff(
            "session-a", "test", "test summary", ["test"]
        )
        result = handoff_store.get_relevant_handoff("")
        self.assertIsNotNone(result)
        self.assertEqual(result["session_id"], "session-a")

    def test_single_handoff_returned_directly(self):
        """Single handoff should be returned without scoring."""
        self._store_handoff(
            "session-a", "unique task", "unique summary", ["unique"]
        )
        result = handoff_store.get_relevant_handoff("completely different")
        self.assertIsNotNone(result)
        self.assertEqual(result["session_id"], "session-a")


if __name__ == "__main__":
    unittest.main()
