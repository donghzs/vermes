"""Regression tests for ContinuityFacade (Route C-1).

Covers:
- Cold start (empty DB → all blocks empty)
- Each source independently populated
- Fail-open: one source failing doesn't block others
- to_prompt_sections / summary helpers
- Integration: load_continuity_context returns valid ContinuityContext

These tests use temp HERMES_HOME to avoid polluting real data.
"""

import os
import sqlite3
import time
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def clean_home(tmp_path, monkeypatch):
    """Clean HERMES_HOME for isolation."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


# ── ContinuityContext dataclass tests ────────────────────────────────


class TestContinuityContext:
    """Test the ContinuityContext dataclass helpers."""

    def test_empty_context_is_empty(self):
        from agent.continuity_facade import ContinuityContext
        ctx = ContinuityContext()
        assert ctx.is_empty is True
        assert ctx.to_prompt_sections() == {}
        assert ctx.summary() == "empty"

    def test_non_empty_context_not_empty(self):
        from agent.continuity_facade import ContinuityContext
        ctx = ContinuityContext(handoff_block="Previous session info")
        assert ctx.is_empty is False
        assert "handoff_context" in ctx.to_prompt_sections()

    def test_to_prompt_sections_only_non_empty(self):
        from agent.continuity_facade import ContinuityContext
        ctx = ContinuityContext(
            handoff_block="handoff",
            evolution_block="",  # empty, should be excluded
            recall_block="recall",
            continuity_block="",
        )
        sections = ctx.to_prompt_sections()
        assert set(sections.keys()) == {"handoff_context", "recall_context"}

    def test_summary_includes_all_sources(self):
        from agent.continuity_facade import ContinuityContext
        ctx = ContinuityContext(
            handoff_block="x" * 100,
            evolution_block="y" * 50,
            sources_failed=["recall"],
        )
        s = ctx.summary()
        assert "handoff" in s
        assert "evolution" in s
        assert "recall" in s  # in failed list

    def test_sources_loaded_tracking(self):
        from agent.continuity_facade import ContinuityContext
        ctx = ContinuityContext(
            sources_loaded=["handoff", "evolution", "recall", "continuity"],
        )
        assert len(ctx.sources_loaded) == 4


# ── load_continuity_context integration tests ───────────────────────


class TestLoadContinuityContext:
    """Test the facade with real (temp) databases."""

    def test_cold_start_empty_db(self, clean_home):
        """No previous session data → all blocks empty, cold start."""
        from agent.continuity_facade import load_continuity_context
        ctx = load_continuity_context("hello world")
        assert ctx.is_empty is True
        # All sources should have been attempted (loaded or failed)
        # 5 sources: handoff, evolution, recall, continuity (loaded) +
        # compression_handoff (empty=skipped) + reflection_flags (failed)
        assert len(ctx.sources_loaded) + len(ctx.sources_failed) == 5

    def test_handoff_source_populated(self, clean_home):
        """Store a handoff → handoff_block should be non-empty."""
        from agent.continuity_facade import load_continuity_context
        from agent.handoff_store import store_handoff

        row_id = store_handoff(
            "test-session-1",
            user_request="Help me with Python",
            summary_text="Worked on Python testing patterns",
            keywords=["python", "testing"],
        )
        assert row_id > 0

        ctx = load_continuity_context("Python testing help")
        assert ctx.handoff_block != ""
        assert "handoff" in ctx.sources_loaded
        assert "Python" in ctx.handoff_block or "python" in ctx.handoff_block.lower()

    def test_evolution_source_populated(self, clean_home):
        """With evolution data → evolution_block should be non-empty."""
        from agent.continuity_facade import load_continuity_context

        # Mock evolution_injector since it depends on evolution DB
        with mock.patch(
            "agent.evolution_injector.load_and_format_evolution",
            return_value="<evolution>\nLearned: prefer pytest over unittest\n</evolution>",
        ):
            ctx = load_continuity_context("test query")
        assert ctx.evolution_block != ""
        assert "evolution" in ctx.sources_loaded

    def test_recall_source_populated(self, clean_home):
        """With memory recall data → recall_block should be non-empty."""
        from agent.continuity_facade import load_continuity_context

        with mock.patch(
            "agent.memory_recall.load_and_format_recall",
            return_value="<recall>\nPrevious: worked on RAG\n</recall>",
        ):
            ctx = load_continuity_context("RAG search")
        assert ctx.recall_block != ""
        assert "recall" in ctx.sources_loaded

    def test_continuity_source_populated(self, clean_home):
        """With cluster snapshots → continuity_block should be non-empty."""
        from agent.continuity_facade import load_continuity_context

        with mock.patch(
            "agent.cross_session_continuity.get_continuity_prompt",
            return_value="<continuity>\nNew behavior detected\n</continuity>",
        ):
            ctx = load_continuity_context("test")
        assert ctx.continuity_block != ""
        assert "continuity" in ctx.sources_loaded

    def test_fail_open_one_source_fails(self, clean_home):
        """If one source raises, others should still load."""
        from agent.continuity_facade import load_continuity_context

        with mock.patch(
            "agent.session_handoff.load_handoff_for_new_session",
            side_effect=RuntimeError("DB locked"),
        ), mock.patch(
            "agent.evolution_injector.load_and_format_evolution",
            return_value="<evolution>data</evolution>",
        ):
            ctx = load_continuity_context("test")
        # Handoff failed but evolution loaded
        assert "handoff" in ctx.sources_failed
        assert "evolution" in ctx.sources_loaded
        assert ctx.evolution_block != ""
        assert ctx.handoff_block == ""

    def test_fail_open_all_sources_fail(self, clean_home):
        """All sources failing → empty context, no exception raised."""
        from agent.continuity_facade import load_continuity_context

        with mock.patch(
            "agent.session_handoff.load_handoff_for_new_session",
            side_effect=RuntimeError("fail"),
        ), mock.patch(
            "agent.evolution_injector.load_and_format_evolution",
            side_effect=RuntimeError("fail"),
        ), mock.patch(
            "agent.memory_recall.load_and_format_recall",
            side_effect=RuntimeError("fail"),
        ), mock.patch(
            "agent.cross_session_continuity.get_continuity_prompt",
            side_effect=RuntimeError("fail"),
        ):
            ctx = load_continuity_context("test")
        assert ctx.is_empty is True
        # 4 mocked fails + compression_handoff (skipped, recall mocked) +
        # reflection_flags (fail on empty db) = 5 failed
        assert len(ctx.sources_failed) == 5
        assert len(ctx.sources_loaded) == 0

    def test_user_message_passed_to_sources(self, clean_home):
        """User message should be passed to sources that accept it."""
        from agent.continuity_facade import load_continuity_context

        with mock.patch(
            "agent.session_handoff.load_handoff_for_new_session",
        ) as mock_handoff, mock.patch(
            "agent.evolution_injector.load_and_format_evolution",
        ) as mock_evolution, mock.patch(
            "agent.memory_recall.load_and_format_recall",
        ) as mock_recall:
            mock_handoff.return_value = None
            mock_evolution.return_value = ""
            mock_recall.return_value = ""
            load_continuity_context("specific query about RAG")
        # These three sources receive the user message
        mock_handoff.assert_called_once_with("specific query about RAG")
        mock_evolution.assert_called_once_with("specific query about RAG")
        mock_recall.assert_called_once_with("specific query about RAG")

    def test_db_path_passed_to_continuity(self, clean_home):
        """db_path should be forwarded to cross_session_continuity."""
        from agent.continuity_facade import load_continuity_context

        with mock.patch(
            "agent.cross_session_continuity.get_continuity_prompt",
        ) as mock_cont:
            mock_cont.return_value = ""
            load_continuity_context("test", db_path="/fake/path.db")
        mock_cont.assert_called_once_with("/fake/path.db")

    def test_multiple_sources_populated(self, clean_home):
        """Multiple sources returning data → all blocks populated."""
        from agent.continuity_facade import load_continuity_context
        from agent.handoff_store import store_handoff

        store_handoff(
            "session-multi",
            user_request="RAG optimization",
            summary_text="Optimized vector search",
            keywords=["rag", "vector"],
        )

        with mock.patch(
            "agent.evolution_injector.load_and_format_evolution",
            return_value="<evolution>Learned RAG patterns</evolution>",
        ), mock.patch(
            "agent.memory_recall.load_and_format_recall",
            return_value="<recall>RAG context</recall>",
        ):
            ctx = load_continuity_context("RAG optimization")
        assert ctx.handoff_block != ""
        assert ctx.evolution_block != ""
        assert ctx.recall_block != ""
        assert not ctx.is_empty
        sections = ctx.to_prompt_sections()
        assert len(sections) >= 3


# ── Backward compatibility ──────────────────────────────────────────


class TestBackwardCompatibility:
    """Verify the facade produces the same interface as the old paths."""

    def test_prompt_section_keys_match_old_attributes(self, clean_home):
        """Section keys should match the old agent._*_context attribute names."""
        from agent.continuity_facade import ContinuityContext
        ctx = ContinuityContext(
            handoff_block="h",
            evolution_block="e",
            recall_block="r",
            continuity_block="c",
        )
        sections = ctx.to_prompt_sections()
        assert "handoff_context" in sections
        assert "evolution_context" in sections
        assert "recall_context" in sections
        assert "continuity_context" in sections
