"""Tests for agent/emergent_insight.py — emergent insight extraction."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from agent.emergent_insight import (
    EmergentInsightExtractor,
    Insight,
    InsightReport,
    build_insight_prompt_block,
    extract_insights,
)
from agent.raw_event import ensure_raw_events_table, record_raw_event
from agent.emergent_clusterer import ensure_cluster_tables


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_db(path):
    return mock.patch("agent.evolution_manager.get_self_model_db", return_value=Path(path))


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def _insert_cluster(conn, name, event_count, success_count, error_count,
                     first_seen=None, last_seen=None):
    """Insert a cluster row."""
    now = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO clusters
           (name, feature_signature, event_count, success_count, error_count,
            total_duration, first_seen, last_seen, last_active_at,
            success_rate, avg_duration, is_active, lifecycle_stage)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, name, event_count, success_count, error_count,
         float(event_count * 0.5), first_seen or now, last_seen or now,
         last_seen or now, success_count / max(event_count, 1),
         0.5, 1, "stable"),
    )


def _insert_raw_event(conn, tool_name, success, timestamp=None, session_id="s1"):
    """Insert a raw_event row."""
    ts = timestamp or datetime.now().isoformat()
    conn.execute(
        """INSERT INTO raw_events
           (timestamp, tool_name, args_preview, result_preview, success,
            duration, session_id, turn_number)
           VALUES (?, ?, '', '', ?, 0.5, ?, 0)""",
        (ts, tool_name, 1 if success else 0, session_id),
    )


# ── Insight Dataclass ────────────────────────────────────────────────────────

class TestInsight:
    def test_to_prompt_line_anti_pattern(self):
        i = Insight(kind="anti_pattern", cluster_id=1, cluster_name="test",
                    description="error rate 60%")
        line = i.to_prompt_line()
        assert "✗" in line
        assert "error rate 60%" in line

    def test_to_prompt_line_strategy(self):
        i = Insight(kind="strategy", cluster_id=1, cluster_name="test",
                    description="95% success")
        assert "✓" in i.to_prompt_line()

    def test_to_prompt_line_achievement(self):
        i = Insight(kind="achievement", cluster_id=None, cluster_name="",
                    description="breakthrough")
        assert "🏆" in i.to_prompt_line()


# ── InsightReport ────────────────────────────────────────────────────────────

class TestInsightReport:
    def test_empty_report(self):
        r = InsightReport()
        assert r.is_empty()
        assert r.total_count() == 0
        assert r.to_prompt_block() == ""

    def test_with_data(self):
        r = InsightReport()
        r.anti_patterns.append(Insight("anti_pattern", 1, "c1", "err"))
        r.strategies.append(Insight("strategy", 2, "c2", "good"))
        assert not r.is_empty()
        assert r.total_count() == 2

    def test_prompt_block_format(self):
        r = InsightReport()
        r.anti_patterns.append(Insight("anti_pattern", 1, "c1", "fail 50%"))
        block = r.to_prompt_block()
        assert "✗" in block
        assert "fail 50%" in block

    def test_prompt_block_max_lines(self):
        r = InsightReport()
        for i in range(10):
            r.anti_patterns.append(Insight("anti_pattern", i, f"c{i}", f"err{i}"))
        block = r.to_prompt_block(max_lines=5)
        assert block.count("\n") <= 5


# ── Anti-Pattern Extraction ──────────────────────────────────────────────────

class TestAntiPatternExtraction:
    def test_high_failure_cluster_detected(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_cluster_tables(conn)
        # Overall: 80% success. Bad cluster: 40% success.
        _insert_cluster(conn, "terminal:git", 20, 18, 2)   # 90%
        _insert_cluster(conn, "terminal:bad", 20, 8, 12)   # 40%
        conn.commit()
        conn.close()

        extractor = EmergentInsightExtractor(temp_db)
        clusters = extractor._load_cluster_stats()
        overall = extractor._compute_overall_stats(clusters)
        aps = extractor._extract_anti_patterns(clusters, overall)

        assert len(aps) >= 1
        assert aps[0].cluster_name == "terminal:bad"
        assert aps[0].severity > 0

    def test_low_event_cluster_skipped(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_cluster_tables(conn)
        _insert_cluster(conn, "small", 2, 0, 2)  # Only 2 events
        _insert_cluster(conn, "big", 20, 18, 2)
        conn.commit()
        conn.close()

        extractor = EmergentInsightExtractor(temp_db)
        clusters = extractor._load_cluster_stats()
        overall = extractor._compute_overall_stats(clusters)
        aps = extractor._extract_anti_patterns(clusters, overall)
        # Small cluster should be skipped
        assert all(a.cluster_name != "small" for a in aps)

    def test_no_anti_patterns_when_all_similar(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_cluster_tables(conn)
        # All clusters have similar success rates
        _insert_cluster(conn, "a", 20, 18, 2)
        _insert_cluster(conn, "b", 20, 17, 3)
        _insert_cluster(conn, "c", 20, 19, 1)
        conn.commit()
        conn.close()

        extractor = EmergentInsightExtractor(temp_db)
        clusters = extractor._load_cluster_stats()
        overall = extractor._compute_overall_stats(clusters)
        aps = extractor._extract_anti_patterns(clusters, overall)
        assert len(aps) == 0  # No significant deviation


# ── Strategy Extraction ──────────────────────────────────────────────────────

class TestStrategyExtraction:
    def test_high_success_cluster_detected(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_cluster_tables(conn)
        _insert_cluster(conn, "good", 30, 29, 1)   # 97%
        _insert_cluster(conn, "avg", 10, 7, 3)     # 70%
        _insert_cluster(conn, "low", 10, 5, 5)     # 50%
        conn.commit()
        conn.close()

        extractor = EmergentInsightExtractor(temp_db)
        clusters = extractor._load_cluster_stats()
        overall = extractor._compute_overall_stats(clusters)
        strategies = extractor._extract_strategies(clusters, overall)

        assert len(strategies) >= 1
        assert strategies[0].cluster_name == "good"

    def test_small_cluster_not_strategy(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_cluster_tables(conn)
        _insert_cluster(conn, "small", 3, 3, 0)  # 100% but only 3 events
        _insert_cluster(conn, "big", 20, 14, 6)
        conn.commit()
        conn.close()

        extractor = EmergentInsightExtractor(temp_db)
        clusters = extractor._load_cluster_stats()
        overall = extractor._compute_overall_stats(clusters)
        strategies = extractor._extract_strategies(clusters, overall)
        # "small" has event_count=3 which is < 3 threshold check, but median is 11
        # event_count (3) < median (11), so not a strategy
        assert all(s.cluster_name != "small" for s in strategies)


# ── Achievement Extraction ───────────────────────────────────────────────────

class TestAchievementExtraction:
    def test_breakout_day(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_raw_events_table(conn)
        ensure_cluster_tables(conn)

        # Insert events from past 6 days (low activity)
        for d in range(6, 0, -1):
            ts = (datetime.now() - timedelta(days=d)).isoformat()
            _insert_raw_event(conn, "terminal", True, ts)

        # Insert many events today (high activity)
        for _ in range(20):
            _insert_raw_event(conn, "terminal", True)

        conn.commit()
        conn.close()

        extractor = EmergentInsightExtractor(temp_db)
        overall = extractor._compute_overall_stats([])
        # Override recent_events computation
        achievements = extractor._extract_achievements(overall)

        # Should detect breakout (20 today vs ~1/day avg)
        breakout = [a for a in achievements if "突破" in a.description]
        assert len(breakout) >= 1

    def test_success_streak(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_raw_events_table(conn)
        # Insert 25 consecutive successes
        for _ in range(25):
            _insert_raw_event(conn, "terminal", True)
        conn.commit()
        conn.close()

        extractor = EmergentInsightExtractor(temp_db)
        overall = extractor._compute_overall_stats([])
        achievements = extractor._extract_achievements(overall)

        streak = [a for a in achievements if "连续" in a.description]
        assert len(streak) >= 1
        assert "25" in streak[0].description


# ── Emotion Signal Extraction ────────────────────────────────────────────────

class TestEmotionExtraction:
    def test_resilient_pattern(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_raw_events_table(conn)

        # Error followed quickly by success (resilient)
        now = datetime.now()
        _insert_raw_event(conn, "terminal", False,
                          (now - timedelta(seconds=60)).isoformat())
        _insert_raw_event(conn, "terminal", True,
                          (now - timedelta(seconds=45)).isoformat())
        _insert_raw_event(conn, "terminal", False,
                          (now - timedelta(seconds=30)).isoformat())
        _insert_raw_event(conn, "terminal", True,
                          (now - timedelta(seconds=15)).isoformat())

        conn.commit()
        conn.close()

        extractor = EmergentInsightExtractor(temp_db)
        emotions = extractor._extract_emotion_signals()

        assert len(emotions) >= 1
        assert "恢复" in emotions[0].description or "灵活" in emotions[0].description

    def test_deep_work_pattern(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_raw_events_table(conn)

        # 25 successful events with 1-2 tools (deep work)
        for _ in range(25):
            _insert_raw_event(conn, "terminal", True)

        conn.commit()
        conn.close()

        extractor = EmergentInsightExtractor(temp_db)
        emotions = extractor._extract_emotion_signals()

        assert len(emotions) >= 1
        assert "深度" in emotions[0].description

    def test_insufficient_events_no_emotion(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_raw_events_table(conn)
        _insert_raw_event(conn, "terminal", True)
        conn.commit()
        conn.close()

        extractor = EmergentInsightExtractor(temp_db)
        emotions = extractor._extract_emotion_signals()
        assert len(emotions) == 0


# ── Integration ──────────────────────────────────────────────────────────────

class TestIntegration:
    def test_extract_insights_empty_db(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_raw_events_table(conn)
        ensure_cluster_tables(conn)
        conn.close()

        report = extract_insights(temp_db)
        assert report.is_empty()

    def test_extract_insights_with_data(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_raw_events_table(conn)
        ensure_cluster_tables(conn)

        # Create clusters with different success rates
        _insert_cluster(conn, "terminal:git", 30, 28, 2)   # 93% strategy
        _insert_cluster(conn, "terminal:bad", 30, 10, 20)  # 33% anti-pattern
        _insert_cluster(conn, "web_search", 15, 12, 3)     # 80% normal

        # Add some raw events for achievements
        for _ in range(5):
            _insert_raw_event(conn, "terminal", True)

        conn.commit()
        conn.close()

        report = extract_insights(temp_db)
        assert not report.is_empty()
        assert len(report.anti_patterns) >= 1
        assert len(report.strategies) >= 1

    def test_build_insight_prompt_block(self, temp_db):
        conn = sqlite3.connect(temp_db)
        ensure_raw_events_table(conn)
        ensure_cluster_tables(conn)
        _insert_cluster(conn, "bad", 30, 10, 20)
        conn.commit()
        conn.close()

        block = build_insight_prompt_block(temp_db)
        # Should produce some text
        assert isinstance(block, str)
