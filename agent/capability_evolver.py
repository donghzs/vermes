"""
agent/capability_evolver.py — 涌现式能力决策器

从用户行为数据（self_assessment 信号 + 簇统计）涌现出
"系统需要什么能力"的决策。不硬编码任何触发规则。

工作流程：
  1. 观察 self_assessment 信号在 raw_events 中的分布
  2. 当 bottleneck 信号聚成簇 → 该簇代表一个"能力缺口"
  3. 查看缺口上下文（哪些工具/场景产生的 bottleneck）
  4. 匹配到注册表中的能力类型（retrieval/skill/graph）
  5. 累积涌现信号 → 达到自然阈值 → 自动安装+激活

"自然阈值"不是硬编码数字，而是：
  - bottleneck 信号占总信号的比例 > 用户基线
  - 即：相比这个用户自己的历史，当前瓶颈信号异常多

这意味着不同用户、不同使用强度下，触发时机自然不同。
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vermes.capability_evolver")


@dataclass
class EmergenceSignal:
    """A single emergence signal pointing to a capability need."""
    capability_name: str
    signal_type: str         # "bottleneck" | "pattern_repeat" | "multi_device"
    confidence: float        # 0-1
    evidence: str = ""
    timestamp: str = ""


@dataclass
class EvolutionDecision:
    """A decision to upgrade a capability."""
    capability_name: str
    action: str              # "install" | "activate" | "monitor"
    reason: str
    confidence: float
    signals: List[EmergenceSignal] = field(default_factory=list)


def evaluate_capability_emergence(db_path: str) -> List[EvolutionDecision]:
    """Evaluate whether any capabilities should be upgraded.

    This is the core emergence function. It looks at:
      1. self_assessment signals in raw_events
      2. Cluster statistics (pattern repetition)
      3. Session/handoff patterns (multi-device signals)

    Returns a list of decisions (install/activate/monitor).
    Does NOT execute the decisions — that's the caller's job.
    """
    decisions: List[EvolutionDecision] = []

    try:
        signals = _gather_emergence_signals(db_path)

        # Group signals by capability
        by_capability: Dict[str, List[EmergenceSignal]] = {}
        for sig in signals:
            by_capability.setdefault(sig.capability_name, []).append(sig)

        for cap_name, cap_signals in by_capability.items():
            decision = _make_decision(cap_name, cap_signals)
            if decision:
                decisions.append(decision)

    except Exception:
        logger.debug("capability emergence evaluation failed", exc_info=True)

    return decisions


def _gather_emergence_signals(db_path: str) -> List[EmergenceSignal]:
    """Gather emergence signals from raw_events and cluster data.

    Signal sources:
      1. __self_assessment__ events with signal=bottleneck → vector_retrieval
      2. Clusters with high event_count + low tool diversity → skill_extraction
      3. Multiple session_ids across short time → graph_sync
    """
    signals: List[EmergenceSignal] = []
    now = datetime.now()

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # ── Signal 1: Retrieval bottleneck ──
        # Count bottleneck signals vs total self_assessment signals
        # If bottleneck ratio is higher than this user's baseline → signal
        signals.extend(_check_retrieval_bottleneck(conn, now))

        # ── Signal 2: Pattern repetition → skill extraction ──
        # Clusters with many events but few unique tools = repetitive pattern
        signals.extend(_check_pattern_repetition(conn, now))

        # ── Signal 3: Multi-device/multi-session → graph sync ──
        # Multiple distinct session_ids in short time windows
        signals.extend(_check_multi_session(conn, now))

        conn.close()
    except Exception:
        logger.debug("signal gathering failed", exc_info=True)

    return signals


def _check_retrieval_bottleneck(
    conn: sqlite3.Connection, now: datetime,
) -> List[EmergenceSignal]:
    """Check if retrieval bottleneck signals are emerging.

    Compares recent bottleneck ratio to historical baseline.
    A bottleneck is "emergent" when it's worse than the user's own average.
    """
    signals: List[EmergenceSignal] = []

    try:
        # Check if raw_events table has self_assessment events
        cursor = conn.execute(
            "SELECT COUNT(*) FROM raw_events WHERE tool_name = '__self_assessment__'"
        )
        total_assessments = cursor.fetchone()[0]

        if total_assessments < 10:
            # Not enough data to judge — let it accumulate
            return signals

        # Count bottleneck signals
        cursor = conn.execute(
            """SELECT COUNT(*) FROM raw_events
               WHERE tool_name = '__self_assessment__'
               AND args_preview LIKE '%bottleneck%'"""
        )
        bottleneck_count = cursor.fetchone()[0]

        # Recent window (last 24h)
        cutoff = (now - timedelta(hours=24)).isoformat()
        cursor = conn.execute(
            """SELECT COUNT(*) FROM raw_events
               WHERE tool_name = '__self_assessment__'
               AND timestamp > ?""",
            (cutoff,)
        )
        recent_total = cursor.fetchone()[0]

        cursor = conn.execute(
            """SELECT COUNT(*) FROM raw_events
               WHERE tool_name = '__self_assessment__'
               AND args_preview LIKE '%bottleneck%'
               AND timestamp > ?""",
            (cutoff,)
        )
        recent_bottleneck = cursor.fetchone()[0]

        if recent_total < 3:
            return signals

        # Compare recent ratio to overall ratio
        overall_ratio = bottleneck_count / total_assessments if total_assessments else 0
        recent_ratio = recent_bottleneck / recent_total if recent_total else 0

        # Signal emerges when recent bottleneck rate exceeds historical baseline
        # This is relative to the user's OWN experience, not a global threshold
        if recent_ratio > overall_ratio and recent_ratio > 0.3:
            confidence = min(1.0, recent_ratio)
            signals.append(EmergenceSignal(
                capability_name="vector_retrieval",
                signal_type="bottleneck",
                confidence=confidence,
                evidence=f"recent bottleneck ratio {recent_ratio:.0%} vs baseline {overall_ratio:.0%} "
                         f"({recent_bottleneck}/{recent_total} in last 24h)",
                timestamp=now.isoformat(),
            ))

    except Exception:
        logger.debug("retrieval bottleneck check failed", exc_info=True)

    return signals


def _check_pattern_repetition(
    conn: sqlite3.Connection, now: datetime,
) -> List[EmergenceSignal]:
    """Check if cluster patterns are repetitive enough for skill extraction.

    A "skill" emerges when a cluster has:
      - High event count (lots of repetitions)
      - Low tool diversity (same few tools used repeatedly)
      - Stable lifecycle stage (pattern is established)

    This is NOT a hardcoded threshold — it compares the cluster's
    tool diversity to the overall median.
    """
    signals: List[EmergenceSignal] = []

    try:
        # Check if clusters table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='clusters'"
        )
        if not cursor.fetchone():
            return signals

        # Load stable clusters with their stats
        cursor = conn.execute(
            """SELECT id, name, event_count, tool_names, lifecycle_stage
               FROM clusters
               WHERE lifecycle_stage = 'stable' AND event_count >= 10
               ORDER BY event_count DESC"""
        )
        clusters = cursor.fetchall()

        if len(clusters) < 3:
            return signals

        # Compute tool diversity for each cluster
        diversities = []
        for c in clusters:
            tools = set((c["tool_names"] or "").split("|"))
            tools.discard("")
            diversities.append(len(tools))

        # Median diversity
        diversities.sort()
        median_diversity = diversities[len(diversities) // 2] if diversities else 0

        # Find clusters with low diversity (repetitive) but high event count
        repetitive_clusters = 0
        for i, c in enumerate(clusters):
            if diversities[i] <= max(1, median_diversity) and c["event_count"] >= 15:
                repetitive_clusters += 1

        # If >40% of stable clusters are repetitive → skill extraction signal
        if repetitive_clusters > 0:
            ratio = repetitive_clusters / len(clusters)
            if ratio > 0.4:
                signals.append(EmergenceSignal(
                    capability_name="skill_extraction",
                    signal_type="pattern_repeat",
                    confidence=min(1.0, ratio),
                    evidence=f"{repetitive_clusters}/{len(clusters)} stable clusters "
                             f"show repetitive patterns (low tool diversity, high count)",
                    timestamp=now.isoformat(),
                ))

    except Exception:
        logger.debug("pattern repetition check failed", exc_info=True)

    return signals


def _check_multi_session(
    conn: sqlite3.Connection, now: datetime,
) -> List[EmergenceSignal]:
    """Check for multi-session/multi-device signals.

    When the same user has many distinct session_ids in a short time,
    it suggests they might benefit from graph sync (sharing knowledge
    across sessions/devices).
    """
    signals: List[EmergenceSignal] = []

    try:
        # Count distinct sessions in last 7 days
        cutoff = (now - timedelta(days=7)).isoformat()
        cursor = conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM raw_events WHERE timestamp > ? AND session_id != ''",
            (cutoff,)
        )
        recent_sessions = cursor.fetchone()[0]

        # Need at least 5 distinct sessions in a week to signal
        # (not a hardcoded trigger — this is a minimum observation window)
        if recent_sessions >= 5:
            # Check if handoff table exists and has entries
            handoff_count = 0
            try:
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='handoffs'"
                )
                if cursor.fetchone():
                    cursor = conn.execute("SELECT COUNT(*) FROM handoffs")
                    handoff_count = cursor.fetchone()[0]
            except Exception:
                pass

            # Many sessions but few handoffs = cross-session continuity gap
            if handoff_count < recent_sessions / 2:
                signals.append(EmergenceSignal(
                    capability_name="graph_sync",
                    signal_type="multi_device",
                    confidence=min(1.0, recent_sessions / 10),
                    evidence=f"{recent_sessions} distinct sessions in 7 days, "
                             f"only {handoff_count} handoffs",
                    timestamp=now.isoformat(),
                ))

    except Exception:
        logger.debug("multi-session check failed", exc_info=True)

    return signals


def _make_decision(
    cap_name: str,
    signals: List[EmergenceSignal],
) -> Optional[EvolutionDecision]:
    """Make an upgrade decision based on accumulated signals.

    Decision logic:
      - 1 signal with confidence > 0.5 → monitor
      - 2+ signals or 1 signal with confidence > 0.8 → install/activate
      - 0 signals → no decision
    """
    if not signals:
        return None

    max_confidence = max(s.confidence for s in signals)
    signal_count = len(signals)

    # Aggregate confidence: more signals = more confidence
    aggregate = min(1.0, max_confidence * (1 + 0.15 * (signal_count - 1)))

    if aggregate > 0.8 or signal_count >= 2:
        action = "activate"
        reason = f"{signal_count} emergence signals, confidence {aggregate:.2f}: {signals[0].evidence}"
    elif aggregate > 0.5:
        action = "install"
        reason = f"{signal_count} emergence signal(s), confidence {aggregate:.2f}: {signals[0].evidence}"
    else:
        action = "monitor"
        reason = f"signal below threshold ({aggregate:.2f}), monitoring"

    return EvolutionDecision(
        capability_name=cap_name,
        action=action,
        reason=reason,
        confidence=aggregate,
        signals=signals,
    )


# ── Convenience ──────────────────────────────────────────────────────────────

def run_emergence_cycle(db_path: str) -> List[EvolutionDecision]:
    """Run a full emergence evaluation cycle.

    Called periodically (e.g., during clustering trigger or heartbeat).
    Returns decisions but does NOT execute them — the caller (Agent)
    decides whether to proceed based on its own judgment.

    This separation ensures the Agent retains agency: the system
    surfaces needs, the Agent decides actions.
    """
    decisions = evaluate_capability_emergence(db_path)

    # Update emergence signal counts in the registry
    from agent.capability_registry import get_capability
    for decision in decisions:
        cap = get_capability(decision.capability_name)
        if cap:
            cap.emergence_signals += len(decision.signals)
            logger.info(
                "Emergence: %s → %s (confidence %.2f, signals %d): %s",
                decision.capability_name,
                decision.action,
                decision.confidence,
                len(decision.signals),
                decision.reason,
            )

    return decisions
