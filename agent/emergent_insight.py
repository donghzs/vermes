"""
agent/emergent_insight.py — 涌现洞察提取器

从 P2 聚类结果中自动提取四类洞察：
  1. 反模式：簇失败率显著高于用户基线
  2. 策略：簇成功率和频次显著高于用户基线
  3. 成就：相对用户自身基线的突破（非固定阈值）
  4. 情绪信号：从用户对错误的反应模式涌现

所有洞察 100% 由用户数据驱动，不预设任何分类。
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class Insight:
    """A single emergent insight."""
    kind: str           # "anti_pattern" | "strategy" | "achievement" | "emotion"
    cluster_id: Optional[int]
    cluster_name: str
    description: str
    severity: float = 0.0   # 0-1, how strong the signal is
    source_event_ids: List[int] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def to_prompt_line(self) -> str:
        """Render as a single line for system prompt injection."""
        icons = {
            "anti_pattern": "✗",
            "strategy": "✓",
            "achievement": "🏆",
            "emotion": "📊",
        }
        icon = icons.get(self.kind, "•")
        return f"  {icon} {self.description}"


@dataclass
class InsightReport:
    """Collection of all insights for a user."""
    anti_patterns: List[Insight] = field(default_factory=list)
    strategies: List[Insight] = field(default_factory=list)
    achievements: List[Insight] = field(default_factory=list)
    emotions: List[Insight] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any([self.anti_patterns, self.strategies,
                        self.achievements, self.emotions])

    def total_count(self) -> int:
        return (len(self.anti_patterns) + len(self.strategies) +
                len(self.achievements) + len(self.emotions))

    def to_prompt_block(self, max_lines: int = 12) -> str:
        """Render as a <learned_experience> block for system prompt.

        Only includes the most important insights to stay within budget.
        """
        if self.is_empty():
            return ""

        lines: List[str] = []

        # Prioritize: achievements (exciting) > anti_patterns (useful) > strategies > emotions
        if self.achievements:
            lines.append("突破:")
            for a in self.achievements[:2]:
                lines.append(a.to_prompt_line())

        if self.anti_patterns:
            lines.append("最近出现的错误模式:")
            for ap in self.anti_patterns[:4]:
                lines.append(ap.to_prompt_line())

        if self.strategies:
            lines.append("有效策略:")
            for s in self.strategies[:3]:
                lines.append(s.to_prompt_line())

        if self.emotions:
            lines.append("行为信号:")
            for e in self.emotions[:2]:
                lines.append(e.to_prompt_line())

        # Trim to budget
        if len(lines) > max_lines:
            lines = lines[:max_lines]

        return "\n".join(lines)


# ── Insight Extractor ────────────────────────────────────────────────────────

class EmergentInsightExtractor:
    """Extracts insights from cluster statistics.

    All thresholds are relative to the user's own baseline — no absolute
    presets. A "high failure rate" means high relative to this user's
    overall failure rate, not relative to some global standard.

    Usage:
        extractor = EmergentInsightExtractor(db_path)
        report = extractor.extract()
        prompt_block = report.to_prompt_block()
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def extract(self) -> InsightReport:
        """Run full insight extraction pipeline."""
        report = InsightReport()

        try:
            clusters = self._load_cluster_stats()
            if not clusters:
                return report

            overall_stats = self._compute_overall_stats(clusters)

            report.anti_patterns = self._extract_anti_patterns(clusters, overall_stats)
            report.strategies = self._extract_strategies(clusters, overall_stats)
            report.achievements = self._extract_achievements(overall_stats)
            report.emotions = self._extract_emotion_signals()

        except Exception:
            logger.debug("Insight extraction failed", exc_info=True)

        return report

    # ── Data Loading ─────────────────────────────────────────────────────────

    def _load_cluster_stats(self) -> List[Dict[str, Any]]:
        """Load all active clusters with stats."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Ensure tables exist
            from agent.emergent_clusterer import ensure_cluster_tables
            ensure_cluster_tables(conn)

            cursor.execute(
                """SELECT * FROM clusters
                   WHERE is_active = 1 AND event_count > 0
                   ORDER BY event_count DESC"""
            )
            rows = cursor.fetchall()
            conn.close()

            return [dict(r) for r in rows]
        except Exception:
            logger.debug("Failed to load cluster stats", exc_info=True)
            return []

    def _compute_overall_stats(self, clusters: List[Dict[str, Any]]) -> Dict[str, float]:
        """Compute user's baseline stats across all clusters."""
        total_events = sum(c["event_count"] for c in clusters)
        total_success = sum(c["success_count"] for c in clusters)
        total_errors = sum(c["error_count"] for c in clusters)

        overall_success_rate = total_success / total_events if total_events > 0 else 0.0

        # Compute std of cluster success rates (for anti-pattern detection)
        rates = [c["success_count"] / c["event_count"]
                 for c in clusters if c["event_count"] > 0]
        if len(rates) > 1:
            mean_rate = sum(rates) / len(rates)
            variance = sum((r - mean_rate) ** 2 for r in rates) / len(rates)
            std_rate = math.sqrt(variance)
        else:
            mean_rate = overall_success_rate
            std_rate = 0.0

        # Recent activity (last 7 days)
        now = datetime.now()
        cutoff_7d = (now - timedelta(days=7)).isoformat()
        recent_events = 0
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM raw_events WHERE timestamp > ?", (cutoff_7d,)
            )
            recent_events = cursor.fetchone()[0]
            conn.close()
        except Exception:
            pass

        # Daily average over last 7 days
        daily_avg = recent_events / 7.0 if recent_events > 0 else 0.0

        return {
            "total_events": total_events,
            "total_success": total_success,
            "total_errors": total_errors,
            "overall_success_rate": overall_success_rate,
            "mean_cluster_success_rate": mean_rate,
            "std_cluster_success_rate": std_rate,
            "recent_events_7d": recent_events,
            "daily_avg_7d": daily_avg,
        }

    # ── Anti-Pattern Extraction ──────────────────────────────────────────────

    def _extract_anti_patterns(
        self,
        clusters: List[Dict[str, Any]],
        overall: Dict[str, float],
    ) -> List[Insight]:
        """Identify clusters with abnormally high failure rates.

        A cluster is an anti-pattern if:
          failure_rate > overall_failure_rate + 2 * std(cluster_failure_rates)

        This is purely relative — no preset "40% failure = bad".
        """
        insights: List[Insight] = []
        baseline_failure = 1.0 - overall["overall_success_rate"]
        # Use 1σ (not 2σ) — with few clusters, 2σ is too permissive
        threshold = baseline_failure + 1.0 * overall["std_cluster_success_rate"]

        for cluster in clusters:
            if cluster["event_count"] < 3:
                continue  # not enough data

            cluster_failure_rate = cluster["error_count"] / cluster["event_count"]

            if cluster_failure_rate >= threshold and cluster_failure_rate > 0.2:
                # How many std deviations above baseline?
                deviation = (
                    (cluster_failure_rate - baseline_failure) /
                    overall["std_cluster_success_rate"]
                    if overall["std_cluster_success_rate"] > 0
                    else 1.0
                )

                # Build description from cluster's actual errors
                error_samples = self._get_cluster_error_samples(cluster["id"])
                description = self._describe_anti_pattern(
                    cluster["name"], cluster_failure_rate, error_samples
                )

                insights.append(Insight(
                    kind="anti_pattern",
                    cluster_id=cluster["id"],
                    cluster_name=cluster["name"],
                    description=description,
                    severity=min(deviation / 3.0, 1.0),
                    source_event_ids=[e["id"] for e in error_samples[:5]],
                    evidence={
                        "cluster_failure_rate": round(cluster_failure_rate, 3),
                        "baseline_failure_rate": round(baseline_failure, 3),
                        "deviation_sigma": round(deviation, 2),
                        "event_count": cluster["event_count"],
                    },
                    timestamp=datetime.now().isoformat(),
                ))

        # Sort by severity
        insights.sort(key=lambda x: x.severity, reverse=True)
        return insights

    def _get_cluster_error_samples(self, cluster_id: int) -> List[Dict[str, Any]]:
        """Get sample error events from a cluster."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, tool_name, args_preview, result_preview, timestamp
                   FROM raw_events
                   WHERE cluster_id = ? AND success = 0
                   ORDER BY timestamp DESC LIMIT 5""",
                (cluster_id,)
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _describe_anti_pattern(
        self,
        cluster_name: str,
        failure_rate: float,
        error_samples: List[Dict[str, Any]],
    ) -> str:
        """Generate a natural language description from actual error data."""
        parts = [f"{cluster_name} 模式失败率 {failure_rate:.0%}"]

        if error_samples:
            # Find common patterns in error results
            error_texts = [e.get("result_preview", "") for e in error_samples]
            # Pick the shortest representative error
            shortest = min(error_texts, key=len) if error_texts else ""
            if shortest:
                # Truncate and clean
                snippet = shortest[:80].replace("\n", " ").strip()
                parts.append(f"（如: {snippet}）")

        return " ".join(parts)

    # ── Strategy Extraction ──────────────────────────────────────────────────

    def _extract_strategies(
        self,
        clusters: List[Dict[str, Any]],
        overall: Dict[str, float],
    ) -> List[Insight]:
        """Identify clusters with abnormally high success rates.

        A cluster is a strategy if:
          success_rate > 90% AND event_count > median(all_clusters)
        """
        insights: List[Insight] = []

        # Compute median event count
        counts = sorted(c["event_count"] for c in clusters)
        median_count = counts[len(counts) // 2] if counts else 0

        for cluster in clusters:
            if cluster["event_count"] < 3:
                continue

            cluster_success_rate = cluster["success_count"] / cluster["event_count"]

            if cluster_success_rate >= 0.90 and cluster["event_count"] >= median_count:
                description = self._describe_strategy(
                    cluster["name"], cluster_success_rate, cluster["event_count"]
                )

                insights.append(Insight(
                    kind="strategy",
                    cluster_id=cluster["id"],
                    cluster_name=cluster["name"],
                    description=description,
                    severity=cluster_success_rate,
                    source_event_ids=[],
                    evidence={
                        "cluster_success_rate": round(cluster_success_rate, 3),
                        "event_count": cluster["event_count"],
                        "median_event_count": median_count,
                    },
                    timestamp=datetime.now().isoformat(),
                ))

        insights.sort(key=lambda x: x.severity, reverse=True)
        return insights

    def _describe_strategy(
        self,
        cluster_name: str,
        success_rate: float,
        event_count: int,
    ) -> str:
        return f"{cluster_name} 模式成功率 {success_rate:.0%}（{event_count} 次操作）"

    # ── Achievement Extraction ───────────────────────────────────────────────

    def _extract_achievements(
        self,
        overall: Dict[str, float],
    ) -> List[Insight]:
        """Detect achievements relative to user's own baseline.

        No fixed thresholds — everything is relative:
          - Today's events > 7-day daily avg × 2.0  → "突破日"
          - New cluster first appeared                → "新领域探索"
          - Any cluster success rate first hit 90%    → "精通"
          - 50 consecutive successes                  → "稳定期"
        """
        insights: List[Insight] = []
        now = datetime.now()

        # 1. Breakout day: today's events > 2x daily average
        daily_avg = overall.get("daily_avg_7d", 0.0)
        today_count = self._count_today_events()
        if daily_avg > 0 and today_count > daily_avg * 2.0:
            insights.append(Insight(
                kind="achievement",
                cluster_id=None,
                cluster_name="",
                description=f"今日突破: 已完成 {today_count} 次操作（7日均值 {daily_avg:.0f} 的 {today_count/daily_avg:.1f}x）",
                severity=min(today_count / (daily_avg * 3), 1.0),
                evidence={"today_count": today_count, "daily_avg": daily_avg},
                timestamp=now.isoformat(),
            ))

        # 2. New cluster discovery
        new_clusters = self._count_new_clusters_today()
        if new_clusters > 0:
            insights.append(Insight(
                kind="achievement",
                cluster_id=None,
                cluster_name="",
                description=f"新领域探索: 今天发现了 {new_clusters} 个新的行为模式",
                severity=0.3,
                evidence={"new_clusters": new_clusters},
                timestamp=now.isoformat(),
            ))

        # 3. Consecutive successes
        streak = self._get_current_success_streak()
        if streak >= 20:
            insights.append(Insight(
                kind="achievement",
                cluster_id=None,
                cluster_name="",
                description=f"稳定期: 连续 {streak} 次操作无错误",
                severity=min(streak / 100, 1.0),
                evidence={"success_streak": streak},
                timestamp=now.isoformat(),
            ))

        return insights

    def _count_today_events(self) -> int:
        """Count raw_events from today."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            today_start = datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            cursor.execute(
                "SELECT COUNT(*) FROM raw_events WHERE timestamp >= ?",
                (today_start,)
            )
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def _count_new_clusters_today(self) -> int:
        """Count clusters first seen today."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            today_start = datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            cursor.execute(
                "SELECT COUNT(*) FROM clusters WHERE first_seen >= ?",
                (today_start,)
            )
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def _get_current_success_streak(self) -> int:
        """Get current consecutive success count (most recent events)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT success FROM raw_events ORDER BY id DESC LIMIT 100"
            )
            streak = 0
            for row in cursor.fetchall():
                if row[0] == 1:
                    streak += 1
                else:
                    break
            conn.close()
            return streak
        except Exception:
            return 0

    # ── Emotion Signal Extraction ────────────────────────────────────────────

    def _extract_emotion_signals(self) -> List[Insight]:
        """Detect behavioral emotion signals from recent event patterns.

        Instead of mapping error_type → emotion, we observe how the user
        (or agent) reacts to outcomes:
          - Error + quick retry success      → "resilient"
          - Error + long pause after         → "stuck"
          - Repeated same error 3+ times     → "looping"
          - Tool switch after error + success → "adaptable"
          - Long focused session, high output → "deep-work"
        """
        insights: List[Insight] = []

        try:
            recent_events = self._load_recent_events(limit=50)
            if len(recent_events) < 4:
                return insights

            # Detect patterns
            emotion = self._detect_dominant_pattern(recent_events)
            if emotion:
                insights.append(Insight(
                    kind="emotion",
                    cluster_id=None,
                    cluster_name="",
                    description=emotion["description"],
                    severity=emotion["confidence"],
                    evidence=emotion["evidence"],
                    timestamp=datetime.now().isoformat(),
                ))

        except Exception:
            logger.debug("Emotion extraction failed", exc_info=True)

        return insights

    def _load_recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Load recent raw_events for pattern detection."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            from agent.raw_event import ensure_raw_events_table
            ensure_raw_events_table(conn)

            cursor.execute(
                """SELECT id, timestamp, tool_name, success, duration,
                          args_preview, result_preview
                   FROM raw_events
                   ORDER BY id DESC LIMIT ?""",
                (limit,)
            )
            rows = cursor.fetchall()
            conn.close()

            # Reverse to chronological order
            return [dict(r) for r in reversed(rows)]
        except Exception:
            return []

    def _detect_dominant_pattern(
        self, events: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Detect the dominant behavioral pattern from recent events."""
        if not events:
            return None

        # Compute time gaps between events
        gaps: List[float] = []
        for i in range(1, len(events)):
            try:
                t1 = datetime.fromisoformat(events[i-1]["timestamp"])
                t2 = datetime.fromisoformat(events[i]["timestamp"])
                gap = (t2 - t1).total_seconds()
                gaps.append(max(gap, 0))
            except (ValueError, KeyError, TypeError):
                gaps.append(0)

        # Pattern 1: Resilient (error → quick retry → success)
        resilient_count = 0
        for i in range(1, len(events)):
            if (not events[i-1]["success"] and events[i]["success"]
                    and i-1 < len(gaps) and gaps[i-1] < 30):
                resilient_count += 1

        # Pattern 2: Stuck (error → long pause)
        stuck_count = 0
        for i in range(1, len(events)):
            if (not events[i-1]["success"] and i-1 < len(gaps)
                    and gaps[i-1] > 300):  # 5 min
                stuck_count += 1

        # Pattern 3: Looping (same error 3+ times)
        looping_count = 0
        for i in range(2, len(events)):
            if (not events[i]["success"] and not events[i-1]["success"]
                    and not events[i-2]["success"]):
                looping_count += 1

        # Pattern 4: Adaptable (error → different tool → success)
        adaptable_count = 0
        for i in range(1, len(events)):
            if (not events[i-1]["success"] and events[i]["success"]
                    and events[i-1]["tool_name"] != events[i]["tool_name"]):
                adaptable_count += 1

        # Pattern 5: Deep work (long session, many successes, focused)
        success_count = sum(1 for e in events if e["success"])
        success_rate = success_count / len(events) if events else 0
        distinct_tools = len(set(e["tool_name"] for e in events))

        # Pick the dominant pattern
        patterns = [
            ("resilient", resilient_count, f"遇到错误后快速恢复（{resilient_count} 次重试成功）"),
            ("stuck", stuck_count, f"遇到错误后停留较久（{stuck_count} 次卡顿）"),
            ("looping", looping_count, f"连续重复错误（{looping_count} 次循环）"),
            ("adaptable", adaptable_count, f"灵活切换策略后成功（{adaptable_count} 次）"),
        ]

        # Deep work check (needs more events)
        if len(events) >= 20 and success_rate > 0.85 and distinct_tools <= 3:
            return {
                "description": f"深度工作状态（{success_count} 次成功，工具聚焦）",
                "confidence": min(success_rate, 0.9),
                "evidence": {
                    "pattern": "deep_work",
                    "success_rate": round(success_rate, 3),
                    "distinct_tools": distinct_tools,
                    "event_count": len(events),
                }
            }

        # Find the strongest pattern
        best = max(patterns, key=lambda x: x[1])
        if best[1] >= 2:  # at least 2 occurrences
            confidence = min(best[1] / 5.0, 0.8)
            return {
                "description": best[2],
                "confidence": confidence,
                "evidence": {
                    "pattern": best[0],
                    "count": best[1],
                    "total_events": len(events),
                }
            }

        return None


# ── Convenience Functions ────────────────────────────────────────────────────

def extract_insights(db_path: str) -> InsightReport:
    """One-shot insight extraction. Returns an InsightReport."""
    extractor = EmergentInsightExtractor(db_path)
    return extractor.extract()


def build_insight_prompt_block(db_path: str, max_lines: int = 12) -> str:
    """Extract insights and format as a prompt block. Empty string if no insights."""
    report = extract_insights(db_path)
    if report.is_empty():
        return ""
    return report.to_prompt_block(max_lines=max_lines)
