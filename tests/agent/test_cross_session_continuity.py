"""Tests for cross_session_continuity integration into main pipeline.

Verifies:
1. Session start loads continuity prompt (conversation_loop.py)
2. Session end saves snapshot (run_agent.py commit_memory_session)
3. system_prompt.py includes continuity block
4. memory_budget.py budgets continuity block
"""

import json
import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ── ContinuityPrompt generation ──────────────────────────────────────────────

def test_get_continuity_prompt_empty_db():
    """Empty DB with no snapshots should return empty string."""
    from agent.cross_session_continuity import get_continuity_prompt
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        result = get_continuity_prompt(db_path)
        assert result == ""
    finally:
        os.unlink(db_path)


def test_save_and_load_snapshot():
    """Save a snapshot, then generate a briefing that detects changes."""
    from agent.cross_session_continuity import CrossSessionContinuity

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        continuity = CrossSessionContinuity(db_path)
        # Save initial snapshot
        snapshot = continuity.save_snapshot("test-session-1")
        assert snapshot.session_id == "test-session-1"
        assert snapshot.timestamp  # should have a timestamp

        # Load it back
        loaded = continuity.load_last_snapshot()
        assert loaded is not None
        assert loaded.session_id == "test-session-1"
    finally:
        os.unlink(db_path)


def test_briefing_detects_new_clusters():
    """Briefing should report new clusters after state changes."""
    from agent.cross_session_continuity import CrossSessionContinuity

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        continuity = CrossSessionContinuity(db_path)

        # Save snapshot with no clusters
        continuity.save_snapshot("session-1")

        # Add a cluster directly
        conn = sqlite3.connect(db_path)
        from agent.emergent_clusterer import ensure_cluster_tables
        ensure_cluster_tables(conn)
        conn.execute(
            "INSERT INTO clusters (id, name, lifecycle_stage, event_count, is_active) "
            "VALUES (1, 'coding-pattern', 'emerging', 3, 1)"
        )
        conn.commit()
        conn.close()

        # Generate briefing — should detect the new cluster
        briefing = continuity.generate_briefing()
        assert "coding-pattern" in briefing.new_clusters
    finally:
        os.unlink(db_path)


def test_briefing_prompt_text():
    """Briefing should render readable prompt text."""
    from agent.cross_session_continuity import ContinuityBriefing

    briefing = ContinuityBriefing(
        new_clusters=["research-workflow", "code-review"],
        total_events_since=15,
    )
    text = briefing.to_prompt_text()
    assert "research-workflow" in text
    assert "code-review" in text
    assert "15" in text


def test_briefing_is_empty():
    """Empty briefing should report is_empty correctly."""
    from agent.cross_session_continuity import ContinuityBriefing

    empty = ContinuityBriefing()
    assert empty.is_empty()

    non_empty = ContinuityBriefing(new_clusters=["test"])
    assert not non_empty.is_empty()


# ── Integration: system_prompt includes continuity ───────────────────────────

def test_system_prompt_has_continuity_slot():
    """system_prompt.py should extract _continuity_context from agent."""
    import ast
    with open("agent/system_prompt.py") as f:
        tree = ast.parse(f.read())
    src = open("agent/system_prompt.py").read()
    assert "_continuity_context" in src
    assert "_continuity_block" in src


def test_memory_budget_has_continuity():
    """memory_budget.py should budget _continuity_context."""
    from agent.memory_budget import _BLOCK_PRIORITIES, _BLOCK_SOFT_CAPS
    assert "_continuity_context" in _BLOCK_PRIORITIES
    assert _BLOCK_PRIORITIES["_continuity_context"] == 5
    assert "_continuity_context" in _BLOCK_SOFT_CAPS
    assert _BLOCK_SOFT_CAPS["_continuity_context"] == 600


# ── Integration: conversation_loop loads continuity at turn 1 ────────────────

def test_conversation_loop_imports_continuity():
    """conversation_loop.py should load continuity context at turn 1 via facade."""
    src = open("agent/conversation_loop.py").read()
    assert "continuity_facade" in src or "load_continuity_context" in src
    assert "_continuity_context" in src


# ── Integration: run_agent saves snapshot at session end ─────────────────────

def test_run_agent_saves_snapshot():
    """run_agent.py commit_memory_session should call save_session_snapshot."""
    src = open("run_agent.py").read()
    assert "save_session_snapshot" in src
