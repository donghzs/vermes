"""A5 跨会话常驻记忆 — 契约测试（取法 Codex 常驻 curated memory）。

锁定：
- recall_curated_notes 只召回稳定 tag（decision/preference/reusable/procedural），排除 volatile
- 容量预算（limit / max_chars）生效
- fail-open：DB 缺失/异常返回空列表
- format_curated_system_block 带标记块
- _restore_or_build_system_prompt 新会话开场注入常驻块（fail-open 不破坏构建）
"""

import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

from agent import memory_fabric
from agent.memory_fabric import recall_curated_notes, format_curated_system_block


def test_recall_curated_excludes_volatile():
    """volatile 类 note 不进入常驻记忆。"""
    fake_rows = [
        ("user prefers red for stock up", "preference", 100),
        ("temp snapshot of session", "volatile", 200),  # 应被排除
        ("decided to use FreeCAD backend", "decision", 150),
    ]
    with patch.object(memory_fabric, "_get_index_db", return_value="/tmp/fake.db"), \
         patch("os.path.exists", return_value=True), \
         patch.object(memory_fabric, "_LOCK"), \
         patch.object(memory_fabric, "_get_conn") as mock_conn:
        cur = MagicMock()
        cur.fetchall.return_value = fake_rows
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn
        notes = recall_curated_notes()
    assert len(notes) == 2
    assert all("temp snapshot" not in n for n in notes)
    assert "user prefers red" in notes[0]


def test_recall_curated_capacity_budget():
    """max_chars 预算截断。"""
    fake_rows = [
        ("a" * 500, "preference", 1),
        ("b" * 500, "decision", 2),
        ("c" * 500, "reusable", 3),
    ]
    with patch.object(memory_fabric, "_get_index_db", return_value="/tmp/fake.db"), \
         patch("os.path.exists", return_value=True), \
         patch.object(memory_fabric, "_LOCK"), \
         patch.object(memory_fabric, "_get_conn") as mock_conn:
        cur = MagicMock()
        cur.fetchall.return_value = fake_rows
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn
        notes = recall_curated_notes(max_chars=600)
    # 第一条 500 + 第二条 500 = 1000 > 600，第二条会被截断（break）
    assert len(notes) == 1


def test_recall_curated_fail_open_on_db_missing():
    """DB 不存在 → 空列表，不崩。"""
    with patch.object(memory_fabric, "_get_index_db", return_value="/tmp/nope.db"), \
         patch("os.path.exists", return_value=False):
        assert recall_curated_notes() == []


def test_format_block_has_marker():
    block = format_curated_system_block(["note one", "note two"])
    assert "<<CURATED_MEMORY>>" in block
    assert "note one" in block and "note two" in block


def test_format_block_empty():
    assert format_curated_system_block([]) == ""


def test_system_prompt_injects_curated_on_new_session():
    """_restore_or_build_system_prompt 新会话开场注入常驻块。"""
    import agent.conversation_loop as cl
    agent = MagicMock()
    agent._session_db = None  # 无 stored prompt
    agent.session_id = "s-test"
    agent._cached_system_prompt = None
    agent._build_system_prompt.return_value = "BASE SYSTEM PROMPT"
    notes = ["user prefers concise replies", "project uses MIT license"]
    with patch("agent.memory_fabric.recall_curated_notes", return_value=notes), \
         patch("agent.memory_fabric.format_curated_system_block",
               side_effect=memory_fabric.format_curated_system_block):
        cl._restore_or_build_system_prompt(agent, "sys", None)
    assert agent._cached_system_prompt is not None
    assert "BASE SYSTEM PROMPT" in agent._cached_system_prompt
    assert "<<CURATED_MEMORY>>" in agent._cached_system_prompt
    assert "user prefers concise replies" in agent._cached_system_prompt


def test_system_prompt_no_injection_when_continuing():
    """继续会话（conversation_history 非空）不注入常驻块。"""
    import agent.conversation_loop as cl
    agent = MagicMock()
    agent._session_db = None
    agent.session_id = "s-test"
    agent._cached_system_prompt = None
    agent._build_system_prompt.return_value = "BASE"
    with patch("agent.memory_fabric.recall_curated_notes", return_value=["should not inject"]) as mock_recall:
        cl._restore_or_build_system_prompt(agent, "sys", [{"role": "user", "content": "hi"}])
    # 继续会话不调用 recall（避免每轮重复拼接）
    mock_recall.assert_not_called()
    assert "<<CURATED_MEMORY>>" not in (agent._cached_system_prompt or "")
