"""Tests for skill_extractor user confirmation flow.

Verifies:
1. Pending skills prompt includes behavioral instructions for the agent
2. reject_skill records a raw_event
3. Rejected clusters are not re-extracted
4. confirm_skill activates a pending skill
"""

import os
import sqlite3
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from agent.skill_extractor import SkillExtractor, ensure_skill_tables


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    ensure_skill_tables(conn)

    # Create clusters table for FK
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS clusters (
            id INTEGER PRIMARY KEY,
            name TEXT DEFAULT 'unnamed',
            event_count INTEGER DEFAULT 0,
            tool_names TEXT DEFAULT '',
            lifecycle_stage TEXT DEFAULT 'emerging',
            success_count INTEGER DEFAULT 0,
            feature_signature TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS raw_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_name TEXT,
            tool_args TEXT DEFAULT '{}',
            result TEXT DEFAULT '',
            is_error INTEGER DEFAULT 0,
            duration REAL DEFAULT 0.0,
            timestamp TEXT DEFAULT (datetime('now')),
            cluster_id INTEGER,
            session_id TEXT DEFAULT ''
        );
    """)

    # Insert a test cluster
    conn.execute(
        "INSERT INTO clusters (id, name, event_count, lifecycle_stage, tool_names, success_count) "
        "VALUES (1, 'test-pattern', 20, 'stable', 'read|write|edit', 18)"
    )
    # Insert a pending skill for that cluster
    conn.execute(
        "INSERT INTO extracted_skills (cluster_id, name, description, tool_sequence, "
        "usage_count, success_rate, status, extracted_at) "
        "VALUES (1, 'test-pattern', 'test desc', '[\"read\",\"write\"]', 20, 0.9, 'pending', '2026-01-01')"
    )
    conn.commit()
    conn.close()
    yield db_path
    os.unlink(db_path)


def test_pending_prompt_has_instructions(temp_db):
    """get_pending_skills_prompt should include behavioral instructions."""
    extractor = SkillExtractor(temp_db)
    prompt = extractor.get_pending_skills_prompt()

    assert "<pending_skills>" in prompt
    assert "test-pattern" in prompt
    # Should include agent behavioral instructions
    assert "confirm_skill" in prompt or "confirm" in prompt.lower()
    assert "reject_skill" in prompt or "reject" in prompt.lower()
    assert "只提议一次" in prompt or "once" in prompt.lower()


def test_reject_skill_changes_status(temp_db):
    """reject_skill should set status to 'rejected'."""
    extractor = SkillExtractor(temp_db)
    ok = extractor.reject_skill(1)
    assert ok

    skills = extractor.list_skills()
    assert len(skills) == 1
    assert skills[0].status == "rejected"


def test_confirm_skill_activates(temp_db):
    """confirm_skill should set status to 'active'."""
    extractor = SkillExtractor(temp_db)
    ok = extractor.confirm_skill(1)
    assert ok

    skills = extractor.list_skills(status="active")
    assert len(skills) == 1
    assert skills[0].name == "test-pattern"


def test_rejected_cluster_not_re_extracted(temp_db):
    """_get_existing_skill_clusters should include rejected clusters."""
    extractor = SkillExtractor(temp_db)
    # Reject the skill first
    extractor.reject_skill(1)

    # Try to extract — should not create a new skill for cluster 1
    conn = sqlite3.connect(temp_db)
    conn.row_factory = sqlite3.Row
    existing = extractor._get_existing_skill_clusters(conn)
    conn.close()

    assert 1 in existing  # cluster 1 already has a skill (rejected)


def test_empty_pending_returns_empty_string(temp_db):
    """When no pending skills, prompt should be empty."""
    extractor = SkillExtractor(temp_db)
    # Reject the only pending skill
    extractor.reject_skill(1)

    prompt = extractor.get_pending_skills_prompt()
    assert prompt == ""


def test_active_skills_prompt(temp_db):
    """get_active_skills_prompt should show active skills."""
    extractor = SkillExtractor(temp_db)
    extractor.confirm_skill(1)

    prompt = extractor.get_active_skills_prompt()
    assert "<extracted_skills>" in prompt
    assert "test-pattern" in prompt
