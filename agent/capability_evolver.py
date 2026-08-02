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

# 涌现式"已确立"质量下限（与 skill_extractor 共用同一涌现信号定义）。
# success_rate 由系统自身从 outcomes 算出，用作质量护栏，非跨模块硬编码映射。
SKILL_SUCCESS_RATE_FLOOR: float = 0.8


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
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

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

    A "skill" emerges when a cluster shows strong self-emergent behavioral
    signals — no hardcoded cross-module mapping, no tool_diversity heuristic:
      - lifecycle_stage='stable'：模式已由系统判定为确立
      - event_count >= 5：重复足够多次（与 skill_extractor 门槛一致）
      - success_rate >= SKILL_SUCCESS_RATE_FLOOR：成功率高（系统已涌现的质量信号）
      - 排除 __xxx__ 模式的系统自噬簇
    The >40% ratio logic is preserved but driven by emergent success_rate,
    not a median tool-diversity comparison.
    """
    signals: List[EmergenceSignal] = []

    try:
        # Check if clusters table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='clusters'"
        )
        if not cursor.fetchone():
            return signals

        # Load stable clusters with their stats (exclude system self-checks)
        cursor = conn.execute(
            """SELECT id, name, event_count, success_rate, lifecycle_stage
               FROM clusters
               WHERE lifecycle_stage = 'stable' AND is_active = 1 AND event_count >= 5
                 AND (name IS NULL OR name NOT GLOB '__*__')
               ORDER BY event_count DESC"""
        )
        clusters = cursor.fetchall()

        # No hard minimum on cluster count: a single well-established stable
        # cluster (event_count>=5, success_rate>=floor) is enough signal to
        # trigger emergence. The old `len(clusters) < 3` brake froze the whole
        # flywheel whenever few clusters were alive (Bug 1 fallout). We keep the
        # data-driven >40% established ratio below as the quality gate.
        if not clusters:
            return signals

        # 涌现式"已确立"判定：用系统自身算出的成功率信号，而非人工 tool_diversity
        established = sum(
            1 for c in clusters
            if (c["success_rate"] or 0) >= SKILL_SUCCESS_RATE_FLOOR
            and c["event_count"] >= 5
        )

        # If >40% of stable clusters are established → skill extraction signal
        if established > 0:
            ratio = established / len(clusters)
            if ratio > 0.4:
                signals.append(EmergenceSignal(
                    capability_name="skill_extraction",
                    signal_type="pattern_repeat",
                    confidence=min(1.0, ratio),
                    evidence=f"{established}/{len(clusters)} stable clusters "
                             f"are established (high event count + high success rate)",
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
            except Exception as e:
                logger.debug("capability_evolver.py:  check multi session failed: %s", e)

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

    # ── Negative-feedback guard ──
    # A capability the user recently DENIED via the Gateway approval flow must
    # not be re-suggested on every clustering cycle (that would spam approval
    # prompts). The denial is itself data: we respect it for a window, after
    # which the signal can re-emerge naturally if the need persists. This keeps
    # the self-evolution loop data-driven and non-annoying.
    denied = _recently_denied_capabilities(db_path, days=7)
    if denied:
        filtered = []
        for d in decisions:
            if d.action in ("activate", "install") and d.capability_name in denied:
                logger.info(
                    "Emergence suppressed (recently denied by user): %s",
                    d.capability_name,
                )
                continue
            filtered.append(d)
        decisions = filtered

    # ── Retraction filter ──
    # Capabilities the user explicitly RETRACTED are permanently suppressed
    # (until the retraction event is itself aged out or manually cleared).
    # Unlike denials (which decay after 7 days), retractions are durable —
    # they represent a deliberate "this was wrong, don't suggest again" signal.
    retracted = _retracted_capabilities(db_path)
    if retracted:
        filtered = []
        for d in decisions:
            if d.action in ("activate", "install") and d.capability_name in retracted:
                logger.info(
                    "Emergence suppressed (retracted by user): %s",
                    d.capability_name,
                )
                continue
            filtered.append(d)
        decisions = filtered

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


def _recently_denied_capabilities(db_path: str, days: int = 7) -> set:
    """Return capability names the user denied via the approval flow recently.

    Reads ``raw_events`` rows written by the gated activation in
    ``agent/raw_event.py`` (tool_name='capability_activate',
    result='denied: <cap>'). A denial within *days* suppresses re-suggestion.
    """
    try:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT DISTINCT substr(result_preview, 9) AS cap
               FROM raw_events
               WHERE tool_name = 'capability_activate'
                 AND result_preview LIKE 'denied: %'
                 AND timestamp > ?""",
            (cutoff,),
        ).fetchall()
        conn.close()
        return {r["cap"] for r in rows}
    except Exception:
        logger.debug("recently-denied lookup failed", exc_info=True)
        return set()


def _retracted_capabilities(db_path: str) -> set:
    """Return capability names the user explicitly retracted.

    Reads ``raw_events`` rows with tool_name='__retraction__' and
    target_type='capability'. Unlike denials, retractions have no time window —
    they are durable until manually un-retracted.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT DISTINCT result_preview
               FROM raw_events
               WHERE tool_name = '__retraction__'
                 AND result_preview LIKE 'retracted: capability:%'""",
        ).fetchall()
        conn.close()
        # result_preview holds the structured "retracted: capability:<name>"
        # (see record_retraction in agent/raw_event.py). Parse it directly -
        # do NOT rely on the truncated args_preview dict repr, which is fragile
        # and can silently fail to extract the name, letting a retracted
        # capability re-emerge.
        result = set()
        for r in rows:
            body = (r["result_preview"] or "").replace("retracted: ", "", 1).strip()
            # body == "capability:<name>"
            if body.startswith("capability:"):
                result.add(body.split(":", 1)[1])
        return result
    except Exception:
        logger.debug("retracted-capabilities lookup failed", exc_info=True)
        return set()
