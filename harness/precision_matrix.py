"""H4.2 — Tool precision matrix (read-only derivation + H2 routing feed-back).

This module derives a per-tool precision profile by combining two *existing*
data sources, without creating any new storage:

  1. Success rate per tool  — from the ``v_outcomes`` view in the evolution
     database (``self-model.db``). This is the same data the self-evolution
     system already records via ``record_tool_outcome`` → raw_events.
  2. Failure-type distribution per tool — from the ``FailureLedger`` (H4.1),
     which records classified failure patterns.

The result is a sortable "precision matrix": every tool with enough samples
gets a success rate plus a breakdown of *why* it fails. ``precision_guidance``
turns that into a human/model-readable warning that the harness injects into
the tool result, steering the agent toward alternatives when a tool has proven
unreliable. This is the "反哺 H2 能力矩阵路由" step: learned precision feeds
back into the harness's per-tool execution gate.

Design
------
* Read-only: never writes to the evolution DB or the ledger.
* Fail-open: any failure returns a neutral profile / no guidance.
* No new tables: reuses v_outcomes + FailureLedger.
* DB path is resolved locally (env HERMES_HOME) to avoid a harness → agent
  import edge and keep the module self-contained.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("harness.precision_matrix")

# Minimum number of outcome samples before we trust the success rate enough
# to warn. Below this we stay silent (not enough evidence).
_MIN_SAMPLES = 5

# A tool whose success rate is below this (with enough samples) is considered
# low-precision and triggers routing guidance.
_LOW_PRECISION_THRESHOLD = 0.5


def _self_model_db_path() -> Path:
    """Resolve the evolution self-model DB path (mirrors memory_recall)."""
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    return Path(hermes_home) / "evolution" / "self-model.db"


@dataclass
class ToolPrecision:
    """Precision profile for a single tool (read-only derivation)."""

    tool: str
    total_calls: int = 0  # outcomes recorded for this tool
    success_count: int = 0
    success_rate: float = 0.0
    failure_types: Dict[str, int] = field(default_factory=dict)  # pattern -> count
    failure_total: int = 0  # sum of recorded failure pattern counts
    low_precision: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _query_outcome_stats(tool_name: str) -> tuple:
    """Return ``(total, success_count)`` from v_outcomes, or ``(0, 0)``.

    Fail-open: any error yields neutral stats.
    """
    try:
        db_path = _self_model_db_path()
        if not db_path.exists():
            return (0, 0)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) "
                "FROM v_outcomes WHERE tool = ?",
                (tool_name,),
            ).fetchone()
        finally:
            conn.close()
        total = row[0] or 0
        success = row[1] or 0
        return (total, success)
    except Exception as exc:
        logger.debug("precision_matrix outcome query failed (fail-open): %s", exc)
        return (0, 0)


def _query_failure_types(tool_name: str) -> Dict[str, int]:
    """Return ``{pattern_str: count}`` from the FailureLedger (H4.1).

    Fail-open: any error yields an empty dict.
    """
    try:
        from .failure_learning import get_ledger

        patterns = get_ledger().get_patterns(tool_name)
        return {p.pattern_str: p.count for p in patterns}
    except Exception as exc:
        logger.debug("precision_matrix failure query failed (fail-open): %s", exc)
        return {}


def get_tool_precision(tool_name: str) -> ToolPrecision:
    """Compute the precision profile for a tool (read-only).

    Fail-open: returns a neutral profile on any error.
    """
    try:
        total, success = _query_outcome_stats(tool_name)
        failure_types = _query_failure_types(tool_name)
        failure_total = sum(failure_types.values())
        success_rate = (success / total) if total > 0 else 0.0
        low = total >= _MIN_SAMPLES and success_rate < _LOW_PRECISION_THRESHOLD
        return ToolPrecision(
            tool=tool_name,
            total_calls=total,
            success_count=success,
            success_rate=success_rate,
            failure_types=failure_types,
            failure_total=failure_total,
            low_precision=low,
        )
    except Exception as exc:
        logger.debug("get_tool_precision failed (fail-open): %s", exc)
        return ToolPrecision(tool=tool_name)


def precision_guidance(tool_name: str) -> Optional[str]:
    """Return a routing-guidance warning if the tool is low-precision.

    Returns ``None`` when there isn't enough evidence or the tool is reliable.
    The returned string is meant to be injected into the tool result so the
    agent sees it and can choose an alternative or verify inputs.

    Performance: the ledger is only consulted when the tool is already known
    to be low-precision (outcome stats decide first), so the common reliable
    path does zero extra I/O.
    """
    try:
        total, success = _query_outcome_stats(tool_name)
        if total < _MIN_SAMPLES:
            return None
        success_rate = (success / total) if total > 0 else 0.0
        if success_rate >= _LOW_PRECISION_THRESHOLD:
            return None

        # Low precision confirmed → enrich with failure types for the message.
        failure_types = _query_failure_types(tool_name)
        top = sorted(failure_types.items(), key=lambda kv: kv[1], reverse=True)[:3]
        if top:
            fail_desc = ", ".join(f"{k} ({v}x)" for k, v in top)
        else:
            fail_desc = "no classified failure pattern"
        return (
            f"[harness precision] tool '{tool_name}' has low historical "
            f"reliability: {success_rate:.0%} success over {total} calls. "
            f"Common failures: {fail_desc}. Consider an alternative approach "
            f"or double-check inputs before retrying."
        )
    except Exception as exc:
        logger.debug("precision_guidance failed (fail-open): %s", exc)
        return None


def get_precision_matrix(min_samples: int = 1) -> List[ToolPrecision]:
    """Return precision profiles for all tools with >= ``min_samples`` outcomes.

    Sorted by success_rate ascending (least reliable first) — useful for
    inspection / ops dashboards. Read-only.
    """
    try:
        db_path = _self_model_db_path()
        if not db_path.exists():
            return []
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            rows = conn.execute(
                "SELECT tool, COUNT(*), "
                "SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) "
                "FROM v_outcomes GROUP BY tool"
            ).fetchall()
        finally:
            conn.close()

        profiles: List[ToolPrecision] = []
        for tool, total, success in rows:
            if total < min_samples:
                continue
            failure_types = _query_failure_types(tool)
            success_rate = (success / total) if total > 0 else 0.0
            profiles.append(
                ToolPrecision(
                    tool=tool,
                    total_calls=total,
                    success_count=success or 0,
                    success_rate=success_rate,
                    failure_types=failure_types,
                    failure_total=sum(failure_types.values()),
                    low_precision=(
                        total >= _MIN_SAMPLES
                        and success_rate < _LOW_PRECISION_THRESHOLD
                    ),
                )
            )
        profiles.sort(key=lambda p: p.success_rate)
        return profiles
    except Exception as exc:
        logger.debug("get_precision_matrix failed (fail-open): %s", exc)
        return []


__all__ = [
    "ToolPrecision",
    "get_tool_precision",
    "precision_guidance",
    "get_precision_matrix",
]
