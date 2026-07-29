"""Evolution data injector — feeds learned experience into new sessions.

Connects the evolution_manager database (strategies, anti_patterns,
self_model) to the system prompt so the agent starts each session
already aware of past lessons.

Design:
  - Reads from evolution DBs at session start (turn 1)
  - No LLM calls — pure SQL + formatting
  - Token budget: ~500 tokens for the evolution block
  - Best-effort: failures never block the main loop
  - Top-N filtering: only the most relevant/frequent patterns

Injection point: agent._evolution_context → system_prompt volatile tier
(mirrors the session_handoff pattern)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Token budget — roughly 4 chars per token, cap the evolution block
_MAX_BLOCK_CHARS = 2000  # ~500 tokens
_MAX_ANTI_PATTERNS = 5
_MAX_STRATEGIES = 3
_MAX_SELF_MODEL_METRICS = 6
_RECENT_OUTCOME_DAYS = 7  # only consider outcomes from last 7 days


def _get_evolution_db() -> Optional[Path]:
    """Resolve the self-model DB path."""
    VERMES_home = os.environ.get("VERMES_HOME") or os.path.expanduser("~/.Vermes")
    db = Path(VERMES_home) / "evolution" / "self-model.db"
    return db if db.exists() else None


def _get_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def load_evolution_context(user_message: str = "") -> Optional[Dict[str, Any]]:
    """Load evolution data for injection into a new session.

    Returns a dict with:
      - anti_patterns: top N most frequent patterns to avoid
      - strategies: top N most successful strategies
      - self_model: recent aggregate metrics
      - recent_summary: success rate from last 7 days

    Returns None if evolution DB doesn't exist or is empty.
    """
    db_path = _get_evolution_db()
    if db_path is None:
        return None

    try:
        conn = _get_conn(db_path)
    except Exception as e:
        logger.debug("Evolution DB connection failed: %s", e)
        return None

    try:
        anti_patterns = _load_anti_patterns(conn)
        strategies = _load_strategies(conn)
        self_model = _load_self_model_metrics(conn)
        recent_summary = _load_recent_summary(conn)

        if not any([anti_patterns, strategies, self_model, recent_summary]):
            return None

        return {
            "anti_patterns": anti_patterns,
            "strategies": strategies,
            "self_model": self_model,
            "recent_summary": recent_summary,
        }
    except Exception as e:
        logger.warning("Evolution context load failed: %s", e)
        return None
    finally:
        conn.close()


def _load_anti_patterns(conn: sqlite3.Connection) -> List[Dict[str, str]]:
    """Load the most frequent anti-patterns."""
    rows = conn.execute(
        "SELECT pattern, correct, domain, frequency "
        "FROM anti_patterns "
        "WHERE frequency > 2 "
        "ORDER BY frequency DESC "
        "LIMIT ?",
        (_MAX_ANTI_PATTERNS,),
    ).fetchall()

    result = []
    for r in rows:
        # Skip overly generic patterns or corrections
        pattern = r["pattern"]
        correct = r["correct"]
        if _is_too_generic(pattern) or _correct_is_generic(correct):
            continue
        result.append({
            "pattern": pattern,
            "correct": correct,
            "domain": r["domain"],
            "frequency": r["frequency"],
        })
    return result


def _is_too_generic(pattern: str) -> bool:
    """Filter out patterns that are too vague to be actionable.

    Uses length and entropy heuristics (not language-specific keywords)
    so the filter works across all domains and languages.
    """
    stripped = pattern.strip()
    if len(stripped) < 8:
        return True
    # If it's all one repeated character (extremely low information content)
    unique_chars = len(set(stripped))
    if unique_chars < 3:
        return True
    return False


def _correct_is_generic(correct: str) -> bool:
    """Filter out corrections that are too vague to be actionable."""
    return _is_too_generic(correct)


def _load_strategies(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Load the most useful strategies."""
    rows = conn.execute(
        "SELECT task_type, strategy, success_rate_when_used, times_used "
        "FROM strategies "
        "WHERE times_used >= 2 "
        "ORDER BY success_rate_when_used DESC, times_used DESC "
        "LIMIT ?",
        (_MAX_STRATEGIES,),
    ).fetchall()

    return [
        {
            "task_type": r["task_type"],
            "strategy": r["strategy"],
            "success_rate": round(r["success_rate_when_used"], 2),
            "times_used": r["times_used"],
        }
        for r in rows
    ]


def _load_self_model_metrics(conn: sqlite3.Connection) -> Dict[str, float]:
    """Load recent aggregate metrics from self_model."""
    # Get the latest value for each distinct metric
    rows = conn.execute(
        "SELECT metric, value, details "
        "FROM self_model "
        "WHERE id IN (SELECT MAX(id) FROM self_model GROUP BY metric) "
        "ORDER BY metric "
        "LIMIT ?",
        (_MAX_SELF_MODEL_METRICS,),
    ).fetchall()

    metrics = {}
    for r in rows:
        metric = r["metric"]
        value = r["value"]
        # Only include meaningful metrics
        if metric.startswith("tool.") or metric.startswith("agent."):
            short_name = metric.split(".")[-1] if "." in metric else metric
            # Skip if details is just a repeat of the metric
            if metric in metrics:
                continue
            metrics[metric] = round(value, 3) if isinstance(value, float) else value

    return metrics


def _load_recent_summary(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """Load a brief summary of recent outcomes."""
    cutoff = (datetime.now() - timedelta(days=_RECENT_OUTCOME_DAYS)).isoformat()
    try:
        row = conn.execute(
            "SELECT "
            "  COUNT(*) as total, "
            "  SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count, "
            "  AVG(CASE WHEN duration > 0 THEN duration ELSE NULL END) as avg_duration "
            "FROM v_outcomes WHERE timestamp > ?",
            (cutoff,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None

    if not row or row["total"] == 0:
        return None

    total = row["total"]
    success = row["success_count"] or 0
    avg_dur = row["avg_duration"]

    return {
        "total_actions": total,
        "success_rate": round(success / total, 3) if total > 0 else 0,
        "avg_duration_ms": round(avg_dur, 1) if avg_dur else 0,
        "period_days": _RECENT_OUTCOME_DAYS,
    }


def format_evolution_for_prompt(evolution: Dict[str, Any]) -> str:
    """Format evolution data as a system prompt block.

    Returns a <learned_experience> block for the system prompt.
    Empty string if nothing to inject.
    """
    parts: List[str] = ["<learned_experience>"]

    # Anti-patterns
    anti = evolution.get("anti_patterns", [])
    if anti:
        parts.append("Past mistakes to avoid:")
        for ap in anti:
            parts.append(f"  ✗ {ap['pattern']}")
            if ap.get("correct"):
                parts.append(f"    → {ap['correct']}")

    # Strategies
    strats = evolution.get("strategies", [])
    if strats:
        parts.append("\nSuccessful strategies:")
        for s in strats:
            parts.append(
                f"  ✓ {s['strategy']} "
                f"(success rate: {s['success_rate']:.0%}, used {s['times_used']} times)"
            )

    # Self-model metrics
    metrics = evolution.get("self_model", {})
    if metrics:
        parts.append("\nRecent performance:")
        for metric, value in metrics.items():
            if isinstance(value, float) and value <= 1.0:
                parts.append(f"  {metric}: {value:.1%}")
            else:
                parts.append(f"  {metric}: {value}")

    # Recent summary
    summary = evolution.get("recent_summary")
    if summary:
        parts.append(
            f"\nLast {summary['period_days']} days: "
            f"{summary['total_actions']} actions, "
            f"{summary['success_rate']:.0%} success rate"
        )

    parts.append("</learned_experience>")

    block = "\n".join(parts)

    # Enforce token budget
    if len(block) > _MAX_BLOCK_CHARS:
        block = block[:_MAX_BLOCK_CHARS] + "\n</learned_experience>"

    return block


def load_and_format_evolution(user_message: str = "") -> str:
    """Convenience: load evolution data and format for prompt injection.

    Tries emergent insight extraction (P3) first. Falls back to legacy
    anti_patterns/strategies tables if no cluster data exists yet.

    Returns empty string if no evolution data available.
    """
    # P3: Try emergent insights from clusters first
    db_path = _get_evolution_db()
    if db_path is not None:
        try:
            from agent.emergent_insight import build_insight_prompt_block
            emergent_block = build_insight_prompt_block(str(db_path), max_lines=12)
            if emergent_block:
                return f"<learned_experience>\n{emergent_block}\n</learned_experience>"
        except Exception:
            logger.debug("Emergent insight extraction failed, falling back", exc_info=True)

    # Legacy: fall back to old anti_patterns/strategies tables
    evolution = load_evolution_context(user_message)
    if evolution is None:
        return ""
    return format_evolution_for_prompt(evolution)
