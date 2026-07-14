"""
agent/self_assessment.py — 涌现式能力自检

在每次 recall 时静默评估检索质量，将信号写入 raw_events。
不触发任何动作，不安装依赖，不改变行为。
信号参与聚类 → 涌现洞察 → 系统自己发现瓶颈。

设计原则:
  - 只观测，不决策
  - 信号和工具事件同等对待（都是 raw_events）
  - 瓶颈模式由聚类+洞察层涌现，不由这里硬编码
  - 零外部依赖

信号类型:
  - recall_hit: 关键词命中了几个数据源
  - recall_miss: 有多少 stable 簇但没被命中
  - bottleneck: richness 高但 hit_rate 低（潜在检索能力不足）
  - capacity_ok: richness 和 hit_rate 都健康

这些不是"功能开关"，是观测数据。系统是否需要升级检索能力，
由 emergent_insight 从这些信号中涌现判断，不在这里预设结论。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, Optional

logger = logging.getLogger("vermes.self_assessment")


def assess_and_record(
    recall_result: Dict[str, Any],
    keywords: list,
    session_id: str = "",
    turn_number: int = 0,
) -> Dict[str, Any]:
    """Assess recall quality and record signal as a raw_event.

    Called at the end of recall_context(). Records:
      - How many data sources were hit (recall_hit)
      - How many stable clusters exist but weren't matched (recall_miss)
      - Whether richness vs hit_rate suggests a bottleneck

    The bottleneck signal is NOT a trigger — it's an observation.
    When enough bottleneck signals accumulate, clustering will group
    them, and emergent_insight will surface "retrieval capacity gap"
    as a pattern. That's the system discovering its own limitation.

    Args:
        recall_result: The dict returned by recall_context()
        keywords:      Keywords extracted from user message
        session_id:    Current session ID
        turn_number:   Current turn number

    Returns:
        Assessment dict (for debugging/logging; not consumed by callers)
    """
    assessment: Dict[str, Any] = {}
    try:
        assessment = _compute_recall_quality(recall_result, keywords)
    except Exception:
        logger.debug("self-assessment computation failed", exc_info=True)
        return assessment

    # Record as raw_event — same channel as tool events
    # so it participates in clustering and insight extraction
    try:
        _record_assessment_event(assessment, session_id, turn_number)
    except Exception:
        logger.debug("self-assessment recording failed", exc_info=True)

    return assessment


def _compute_recall_quality(
    recall: Dict[str, Any],
    keywords: list,
) -> Dict[str, Any]:
    """Compute recall quality metrics from recall result.

    Metrics (all data-driven, no thresholds):
      - sources_hit: which data sources returned data
      - hit_count: number of sources with data
      - stable_cluster_count: from richness
      - cluster_coverage: did any keyword match cluster-related domains?
      - richness_value: from richness
      - richness_tier: from richness
      - signal: bottleneck | capacity_ok | cold_start | no_keywords
    """
    richness = recall.get("richness")
    has_outcomes = "recent_outcomes" in recall
    has_domains = "domain_stats" in recall
    has_emotion = "emotion" in recall
    has_embeddings = "embedding_matches" in recall

    sources_hit = []
    if has_outcomes:
        sources_hit.append("outcomes")
    if has_domains:
        sources_hit.append("domains")
    if has_emotion:
        sources_hit.append("emotion")
    if has_embeddings:
        sources_hit.append("embeddings")

    hit_count = len(sources_hit)
    stable_clusters = richness.stable_cluster_count if richness else 0
    richness_value = richness.value if richness else 0.0
    richness_tier = richness.tier if richness else "cold_start"

    # Determine signal — observation, not decision
    if not keywords:
        signal = "no_keywords"
    elif richness_tier == "cold_start":
        signal = "cold_start"
    elif stable_clusters >= 5 and hit_count <= 1:
        # Many patterns exist but recall barely found anything
        # This IS a bottleneck observation (not a trigger)
        signal = "bottleneck"
    elif hit_count >= 2 or (richness_tier in ("building",) and hit_count >= 1):
        signal = "capacity_ok"
    else:
        signal = "capacity_ok"

    return {
        "signal": signal,
        "sources_hit": sources_hit,
        "hit_count": hit_count,
        "keyword_count": len(keywords),
        "stable_cluster_count": stable_clusters,
        "richness_value": round(richness_value, 3),
        "richness_tier": richness_tier,
    }


def _record_assessment_event(
    assessment: Dict[str, Any],
    session_id: str,
    turn_number: int,
) -> None:
    """Write assessment as a raw_event.

    Uses tool_name="__self_assessment__" to distinguish from real tool calls.
    The event participates in clustering like any other event — if bottleneck
    signals accumulate, they'll form their own cluster, and emergent_insight
    will surface the pattern.
    """
    from agent.raw_event import record_raw_event

    signal = assessment["signal"]
    # Encode assessment as args_preview for raw_event
    args_preview = (
        f"signal={signal}, "
        f"hit={assessment['hit_count']}/4, "
        f"clusters={assessment['stable_cluster_count']}, "
        f"richness={assessment['richness_value']}"
    )
    result_preview = (
        f"sources_hit={assessment['sources_hit']}, "
        f"tier={assessment['richness_tier']}, "
        f"keywords={assessment['keyword_count']}"
    )

    record_raw_event(
        tool_name="__self_assessment__",
        tool_args={"signal": signal, "assessment": assessment},
        result=result_preview,
        is_error=False,
        duration=0.0,
        session_id=session_id,
        turn_number=turn_number,
    )
