"""
tests/agent/test_self_assessment.py — 自检模块测试

验证：
  1. 信号类型正确（bottleneck / capacity_ok / cold_start / no_keywords）
  2. 信号写入 raw_events（参与聚类）
  3. 不触发任何动作（纯观测）
  4. 异常静默降级
"""

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agent.self_assessment import (
    assess_and_record,
    _compute_recall_quality,
    _record_assessment_event,
)
from agent.memory_recall import RichnessScore


# ── Test _compute_recall_quality ────────────────────────────────────────────

class TestComputeRecallQuality:
    """Test signal computation from recall results."""

    def _make_richness(self, value=0.5, tier="learning", clusters=4):
        return RichnessScore(
            value=value,
            tier=tier,
            stable_cluster_count=clusters,
        )

    def test_no_keywords(self):
        """No keywords → no_keywords signal."""
        recall = {"richness": self._make_richness()}
        result = _compute_recall_quality(recall, [])
        assert result["signal"] == "no_keywords"
        assert result["keyword_count"] == 0

    def test_cold_start(self):
        """Cold start tier → cold_start signal."""
        recall = {"richness": self._make_richness(value=0.01, tier="cold_start", clusters=0)}
        result = _compute_recall_quality(recall, ["git", "commit"])
        assert result["signal"] == "cold_start"
        assert result["stable_cluster_count"] == 0

    def test_bottleneck_many_clusters_no_hits(self):
        """5+ stable clusters but 0-1 data sources hit → bottleneck."""
        recall = {
            "richness": self._make_richness(value=0.7, tier="fluent", clusters=8),
            # No outcomes, no domains, no emotion, no embeddings
        }
        result = _compute_recall_quality(recall, ["python", "test"])
        assert result["signal"] == "bottleneck"
        assert result["stable_cluster_count"] == 8
        assert result["hit_count"] == 0

    def test_bottleneck_with_one_hit(self):
        """5+ clusters but only 1 source hit → still bottleneck."""
        recall = {
            "richness": self._make_richness(value=0.65, tier="fluent", clusters=6),
            "emotion": {"emotion": "flow"},
        }
        result = _compute_recall_quality(recall, ["debug"])
        assert result["signal"] == "bottleneck"
        assert result["hit_count"] == 1

    def test_capacity_ok_multiple_hits(self):
        """2+ data sources hit → capacity_ok."""
        recall = {
            "richness": self._make_richness(value=0.55, tier="learning", clusters=4),
            "recent_outcomes": [{"task": "test"}],
            "domain_stats": [{"domain": "testing"}],
        }
        result = _compute_recall_quality(recall, ["test", "python"])
        assert result["signal"] == "capacity_ok"
        assert result["hit_count"] == 2

    def test_capacity_ok_building_tier(self):
        """Building tier with 1 hit → capacity_ok."""
        recall = {
            "richness": self._make_richness(value=0.25, tier="building", clusters=2),
            "recent_outcomes": [{"task": "test"}],
        }
        result = _compute_recall_quality(recall, ["test"])
        assert result["signal"] == "capacity_ok"

    def test_sources_hit_tracking(self):
        """Sources hit list is accurate."""
        recall = {
            "richness": self._make_richness(),
            "recent_outcomes": [{"task": "x"}],
            "domain_stats": [{"domain": "y"}],
            "emotion": {"emotion": "flow"},
            "embedding_matches": [{"content": "z"}],
        }
        result = _compute_recall_quality(recall, ["test"])
        assert set(result["sources_hit"]) == {"outcomes", "domains", "emotion", "embeddings"}
        assert result["hit_count"] == 4

    def test_no_richness_in_recall(self):
        """Missing richness → cold_start signal."""
        recall = {}
        result = _compute_recall_quality(recall, ["test"])
        assert result["signal"] == "cold_start"
        assert result["richness_tier"] == "cold_start"
        assert result["stable_cluster_count"] == 0


# ── Test _record_assessment_event ───────────────────────────────────────────

class TestRecordAssessmentEvent:
    """Test raw_event recording of assessment signals."""

    def test_records_to_raw_events(self, tmp_path):
        """Assessment signal is written to raw_events table."""
        # Setup: create a minimal self-model.db
        db_path = tmp_path / "evolution" / "self-model.db"
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                args_preview TEXT,
                result_preview TEXT,
                success INTEGER DEFAULT 1,
                duration REAL DEFAULT 0,
                session_id TEXT DEFAULT '',
                turn_number INTEGER DEFAULT 0,
                cluster_id INTEGER,
                embedding_id INTEGER,
                protected INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

        assessment = {
            "signal": "bottleneck",
            "sources_hit": [],
            "hit_count": 0,
            "keyword_count": 2,
            "stable_cluster_count": 7,
            "richness_value": 0.65,
            "richness_tier": "fluent",
        }

        with patch("agent.evolution_manager.get_self_model_db", return_value=db_path):
            _record_assessment_event(assessment, "test_session", 1)

        # Verify
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT tool_name, args_preview, success FROM raw_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "__self_assessment__"
        assert "bottleneck" in row[1]
        assert row[2] == 1  # success=True

    def test_exception_silent(self):
        """Recording failure doesn't raise."""
        assessment = {"signal": "ok", "sources_hit": [], "hit_count": 0,
                      "keyword_count": 0, "stable_cluster_count": 0,
                      "richness_value": 0.0, "richness_tier": "cold_start"}
        # Will fail because no DB, but should not raise
        with patch("agent.evolution_manager.get_self_model_db", return_value=Path("/nonexistent")):
            _record_assessment_event(assessment, "x", 0)  # should not raise


# ── Test assess_and_record (integration) ────────────────────────────────────

class TestAssessAndRecord:
    """Test the full assess_and_record flow."""

    def test_returns_assessment(self):
        """assess_and_record returns the assessment dict."""
        recall = {
            "richness": RichnessScore(value=0.5, tier="learning", stable_cluster_count=3),
            "recent_outcomes": [{"task": "x"}],
        }
        with patch("agent.self_assessment._record_assessment_event"):
            result = assess_and_record(recall, ["test"], "s1", 1)

        assert result["signal"] == "capacity_ok"
        assert result["hit_count"] == 1

    def test_exception_silent(self):
        """Full flow failure doesn't raise."""
        with patch("agent.self_assessment._compute_recall_quality", side_effect=Exception("boom")):
            result = assess_and_record({}, ["test"], "s1", 1)
        # Should not raise, returns empty dict
        assert isinstance(result, dict)

    def test_bottleneck_signal_propagates(self, tmp_path):
        """Bottleneck signal flows through to raw_events."""
        db_path = tmp_path / "evolution" / "self-model.db"
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                args_preview TEXT,
                result_preview TEXT,
                success INTEGER DEFAULT 1,
                duration REAL DEFAULT 0,
                session_id TEXT DEFAULT '',
                turn_number INTEGER DEFAULT 0,
                cluster_id INTEGER,
                embedding_id INTEGER,
                protected INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

        recall = {
            "richness": RichnessScore(value=0.72, tier="fluent", stable_cluster_count=9),
            # No data sources hit → bottleneck
        }

        with patch("agent.evolution_manager.get_self_model_db", return_value=db_path):
            result = assess_and_record(recall, ["python", "debug"], "s1", 5)

        assert result["signal"] == "bottleneck"

        # Verify it was recorded
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT tool_name, args_preview FROM raw_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "__self_assessment__"
        assert "bottleneck" in row[1]
