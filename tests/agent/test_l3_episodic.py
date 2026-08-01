"""Tests for L3 episodic activation — handoff injection with quality floor."""
import sqlite3
from pathlib import Path
import pytest

from agent.memory_recall import _collect_recall_sections


def _make_handoff_db(tmp_path, entries):
    """Create session_handoffs.db with given entries."""
    db_path = tmp_path / "session_handoffs.db"
    # Also need to make _get_handoff_db return this path
    # We'll monkeypatch _get_handoff_db
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE session_handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT, summary_text TEXT
        )
    """)
    for session_id, summary in entries:
        conn.execute(
            "INSERT INTO session_handoffs (session_id, summary_text) VALUES (?, ?)",
            (session_id, summary),
        )
    conn.commit()
    conn.close()
    return db_path


class TestHandoffQualityFloor:

    def test_quality_handoff_injected(self, tmp_path, monkeypatch):
        """Handoff >= 80 chars and not test noise → injected."""
        db_path = _make_handoff_db(tmp_path, [
            ("sess_good", "上次会话主题: Vermes 记忆系统审计 | 关键决策: 完成 merge 真合并修复，relations 接召回通过 session_id 桥接 | 未完成: event_time 时序加权"),
        ])
        monkeypatch.setattr("agent.memory_recall._get_handoff_db", lambda: db_path)
        # Also need self_model_db to be None to avoid noise
        monkeypatch.setattr("agent.memory_recall._get_self_model_db", lambda: None)
        monkeypatch.setattr("agent.memory_recall._get_fusion_db", lambda: None)

        result = _collect_recall_sections("test query")
        assert "handoff_snippets" in result
        assert len(result["handoff_snippets"]) == 1
        assert "merge 真合并" in result["handoff_snippets"][0]["content"]

    def test_short_handoff_filtered(self, tmp_path, monkeypatch):
        """Handoff < 80 chars → filtered out."""
        db_path = _make_handoff_db(tmp_path, [
            ("sess_short", "上次会话主题: 通讯正常不"),  # 13 chars
        ])
        monkeypatch.setattr("agent.memory_recall._get_handoff_db", lambda: db_path)
        monkeypatch.setattr("agent.memory_recall._get_self_model_db", lambda: None)
        monkeypatch.setattr("agent.memory_recall._get_fusion_db", lambda: None)

        result = _collect_recall_sections("test query")
        assert "handoff_snippets" not in result

    def test_test_noise_filtered(self, tmp_path, monkeypatch):
        """Handoff starting with test noise → filtered."""
        db_path = _make_handoff_db(tmp_path, [
            ("sess_noise", "上次会话主题: 通讯正常不 | 关键决策: 这是一个测试用的长摘要超过八十字符的测试数据用于验证质量地板是否正常工作过滤掉噪声数据"),
        ])
        monkeypatch.setattr("agent.memory_recall._get_handoff_db", lambda: db_path)
        monkeypatch.setattr("agent.memory_recall._get_self_model_db", lambda: None)
        monkeypatch.setattr("agent.memory_recall._get_fusion_db", lambda: None)

        result = _collect_recall_sections("test query")
        assert "handoff_snippets" not in result

    def test_you_hao_filtered(self, tmp_path, monkeypatch):
        """Handoff starting with 你好 test noise → filtered."""
        db_path = _make_handoff_db(tmp_path, [
            ("sess_hello", "上次会话主题: 你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好"),  # >80 chars but test noise
        ])
        monkeypatch.setattr("agent.memory_recall._get_handoff_db", lambda: db_path)
        monkeypatch.setattr("agent.memory_recall._get_self_model_db", lambda: None)
        monkeypatch.setattr("agent.memory_recall._get_fusion_db", lambda: None)

        result = _collect_recall_sections("test query")
        assert "handoff_snippets" not in result

    def test_multiple_handoffs_mixed(self, tmp_path, monkeypatch):
        """Mix of quality + noise → only quality ones pass."""
        db_path = _make_handoff_db(tmp_path, [
            ("sess_noise1", "上次会话主题: 通讯正常不"),
            ("sess_good1", "上次会话主题: 深度审计 | 关键决策: 完成四项大脑层修复方案设计，merge 真合并删 skill 冗余记忆，relations 通过 session_id 桥接 | 未完成: benchmark"),
            ("sess_noise2", "上次会话主题: 你好"),
            ("sess_good2", "上次会话主题: 飞轮量化报告 | 关键决策: 飞轮评分 4.5/10，五环中感知和反馈已通，判断和行动环断裂需修复，需 merge 真合并和 relations 接召回 | 未完成: 跑 benchmark 定档"),
        ])
        monkeypatch.setattr("agent.memory_recall._get_handoff_db", lambda: db_path)
        monkeypatch.setattr("agent.memory_recall._get_self_model_db", lambda: None)
        monkeypatch.setattr("agent.memory_recall._get_fusion_db", lambda: None)

        result = _collect_recall_sections("test query")
        assert "handoff_snippets" in result
        assert len(result["handoff_snippets"]) == 2

    def test_no_handoff_db(self, tmp_path, monkeypatch):
        """No handoff DB → no handoff snippets (fail-open)."""
        monkeypatch.setattr("agent.memory_recall._get_handoff_db", lambda: None)
        monkeypatch.setattr("agent.memory_recall._get_self_model_db", lambda: None)
        monkeypatch.setattr("agent.memory_recall._get_fusion_db", lambda: None)

        result = _collect_recall_sections("test query")
        assert "handoff_snippets" not in result
