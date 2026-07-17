"""
agent/raw_event.py — 零分类原始事件记录层

RawEvent 只存观察事实，不做任何分类、评级、标签。
所有分类逻辑将在 P2 (EmergentClusterer) 和 P3 (EmergentInsightExtractor) 中
从用户行为数据中涌现。

设计原则:
  - 只记录 what happened, 不猜测 why 或 categorize
  - 每个事件有 session_id + turn_number，支撑跨会话分析
  - protected 标记用于 P4 洞察溯源保护
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Health check: detect silent emergence chain failure ─────────────────────
# If _maybe_trigger_clustering hasn't successfully completed in >24h,
# log a WARN so the user knows the self-evolution system went silent.
_LAST_EMERGENCE_OK: Optional[datetime] = None
_EMERGENCE_STALE_THRESHOLD = timedelta(hours=24)


# ── Data Class ───────────────────────────────────────────────────────────────

@dataclass
class RawEvent:
    """零分类原始事件。只记录事实，不预设分类体系。

    对比旧 outcomes 表字段:
      outcomes: task(分类), action(参数), tool, success, details, duration, domain(分类), error_type(分类), error_msg, role
      raw_events: tool_name, args_preview, result_preview, success, duration, session_id, turn_number
      新增: session_id, turn_number, cluster_id(P2回填), embedding_id, protected(P4)

    字段说明:
      - timestamp:     ISO 格式，精确到毫秒
      - tool_name:     工具名 (terminal/read_file/write_file/web_search/...)
      - args_preview:  参数摘要 (前 200 字符)
      - result_preview: 结果摘要 (前 500 字符)
      - success:       是否成功 (0/1)
      - duration:      执行耗时 (秒)
      - session_id:    会话 ID (跨会话关联聚类用)
      - turn_number:   会话内回合序号
      - cluster_id:    P2 聚类后回填 (NULL 表示未聚类)
      - embedding_id:  关联 embeddings 表 ID (NULL 表示未嵌入)
      - protected:     洞察溯源保护标记 (0=可淘汰, 1=受洞察保护)

    Example:
        event = RawEvent(
            tool_name="terminal",
            args_preview='{"command": "git commit -m fix"}',
            result_preview="[success] output: ...",
            success=True,
            duration=1.23,
            session_id="abc123",
            turn_number=42,
        )
    """
    timestamp: str
    tool_name: str
    args_preview: str
    result_preview: str
    success: bool
    duration: float
    session_id: str
    turn_number: int = 0
    cluster_id: Optional[int] = None
    embedding_id: Optional[int] = None
    protected: bool = False

    def to_db_row(self) -> tuple:
        """Convert to tuple for INSERT INTO raw_events."""
        return (
            self.timestamp,
            self.tool_name,
            self.args_preview,
            self.result_preview,
            1 if self.success else 0,
            self.duration,
            self.session_id,
            self.turn_number,
            self.cluster_id,
            self.embedding_id,
            1 if self.protected else 0,
        )

    @classmethod
    def from_db_row(cls, row: sqlite3.Row) -> "RawEvent":
        """Reconstruct from a DB row."""
        return cls(
            timestamp=row["timestamp"],
            tool_name=row["tool_name"],
            args_preview=row["args_preview"] or "",
            result_preview=row["result_preview"] or "",
            success=bool(row["success"]),
            duration=row["duration"] or 0.0,
            session_id=row["session_id"],
            turn_number=row["turn_number"] or 0,
            cluster_id=row["cluster_id"],
            embedding_id=row["embedding_id"],
            protected=bool(row["protected"]),
        )


# ── Table Management ─────────────────────────────────────────────────────────

RAW_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS raw_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    tool_name       TEXT    NOT NULL,
    args_preview    TEXT    DEFAULT '',
    result_preview  TEXT    DEFAULT '',
    success         INTEGER NOT NULL DEFAULT 1,
    duration        REAL    DEFAULT 0,
    session_id      TEXT    NOT NULL,
    turn_number     INTEGER DEFAULT 0,
    cluster_id      INTEGER DEFAULT NULL,
    embedding_id    INTEGER DEFAULT NULL,
    protected       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_raw_events_timestamp   ON raw_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_raw_events_session      ON raw_events(session_id);
CREATE INDEX IF NOT EXISTS idx_raw_events_tool         ON raw_events(tool_name);
CREATE INDEX IF NOT EXISTS idx_raw_events_cluster      ON raw_events(cluster_id);
"""


def ensure_raw_events_table(conn: sqlite3.Connection) -> None:
    """Create raw_events table, indexes, and v_outcomes view if they don't exist.

    v_outcomes is a compatibility view that maps raw_events to the old
    outcomes table schema, so legacy queries (FROM outcomes) can switch
    to FROM v_outcomes without modification. This eliminates dual-write:
    raw_events is the single source of truth.
    """
    conn.executescript(RAW_EVENTS_TABLE_SQL)
    # Compatibility view: maps raw_events → outcomes schema
    # domain/error_type are '' (legacy code never populated them meaningfully)
    # role is 'default' (roles table rarely exists, detect_role always returned 'default')
    conn.execute(
        """CREATE VIEW IF NOT EXISTS v_outcomes AS
           SELECT
             id,
             timestamp,
             tool_name AS task,
             args_preview AS action,
             tool_name AS tool,
             success,
             result_preview AS details,
             duration,
             '' AS domain,
             '' AS error_type,
             CASE WHEN success = 0 THEN result_preview ELSE '' END AS error_msg,
             'default' AS role
           FROM raw_events"""
    )
    conn.commit()


# ── Write ────────────────────────────────────────────────────────────────────

def _write_raw_event_to_db(event: RawEvent, db_path: str) -> Optional[int]:
    """Write a single RawEvent to raw_events table. Returns rowid or None."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        ensure_raw_events_table(conn)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO raw_events
               (timestamp, tool_name, args_preview, result_preview, success,
                duration, session_id, turn_number, cluster_id, embedding_id, protected)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            event.to_db_row(),
        )
        conn.commit()
        rowid = cursor.lastrowid
        conn.close()
        return rowid
    except Exception:
        logger.debug("Failed to write raw_event: %s/%s", event.tool_name, event.session_id, exc_info=True)
        return None


# ── Public API ───────────────────────────────────────────────────────────────

def record_raw_event(
    tool_name: str,
    tool_args: Dict[str, Any],
    result: str,
    is_error: bool,
    duration: float,
    session_id: str = "",
    turn_number: int = 0,
    trigger_clustering: bool = True,
) -> Optional[int]:
    """Record a tool execution as a zero-classification RawEvent.

    This is the core event recording function. It writes raw — no task
    classification, no domain detection, no error categorization, no
    emotional mapping. All of that will emerge from clustering (P2) and
    insight extraction (P3).

    Args:
        tool_name:   Tool name (terminal, read_file, web_search, ...)
        tool_args:   Tool arguments dict
        result:      Tool result string
        is_error:    Whether the execution was an error
        duration:    Execution duration in seconds
        session_id:  Session identifier (for cross-session analysis)
        turn_number: Turn number within the session

    Returns:
        Row ID of the inserted event, or None on failure.
    """
    from agent.evolution_manager import get_self_model_db

    timestamp = datetime.now().isoformat()
    event = RawEvent(
        timestamp=timestamp,
        tool_name=tool_name,
        args_preview=str(tool_args)[:200],
        result_preview=(str(result)[:500] if result else ""),
        success=not is_error,
        duration=duration,
        session_id=session_id,
        turn_number=turn_number,
    )

    db_path = get_self_model_db()
    rowid = _write_raw_event_to_db(event, str(db_path))

    if rowid:
        # ── Write embedding (semantic retrieval) ─────────────────────
        try:
            from agent.hybrid_retriever import store_embedding

            emb_parts = [
                f"Tool: {tool_name}",
                f"Args: {str(tool_args)[:200]}",
            ]
            if is_error:
                emb_parts.append(f"Error: {str(result)[:200]}")
            else:
                emb_parts.append(f"Success: {str(result)[:200]}")
            emb_content = " | ".join(emb_parts)
            store_embedding(emb_content, target=f"raw_event:{rowid}")
        except Exception:
            logger.debug("store_embedding skipped for raw_event:%d", rowid, exc_info=True)

    # ── Health check: is the emergence chain alive? ──
    # If _maybe_trigger_clustering hasn't completed successfully in >24h,
    # something is silently broken (import error, DB schema drift, etc.).
    # WARN the user so they can investigate.
    global _LAST_EMERGENCE_OK
    if _LAST_EMERGENCE_OK is not None:
        stale_for = datetime.now() - _LAST_EMERGENCE_OK
        if stale_for > _EMERGENCE_STALE_THRESHOLD:
            logger.warning(
                "Emergence chain has been silent for %.1f hours — "
                "self-evolution may be broken (check clustering/emergence imports)",
                stale_for.total_seconds() / 3600,
            )

    # ── Clustering trigger: check if enough events accumulated ──
    # Called inline here (not deferred) because the clustering check is a
    # lightweight COUNT query that returns immediately when threshold not met.
    # When threshold is met, clustering runs synchronously — acceptable for
    # a batch operation that runs once per ~50 events.
    # ``trigger_clustering=False`` is used for meta-events whose recording must
    # NOT re-enter the emergence chain (e.g. a capability-activation approval
    # decision), otherwise recording the decision would re-trigger emergence
    # and re-suggest the same activation → an approval/deny loop.
    if trigger_clustering:
        try:
            _maybe_trigger_clustering(session_id)
            _LAST_EMERGENCE_OK = datetime.now()
        except Exception:
            pass

    return rowid


def _maybe_trigger_clustering(session_id: str) -> None:
    """Check if enough events have accumulated to trigger clustering.

    This is a P2 hook — checks accumulation and runs clustering asynchronously.
      1. Count unclustered raw_events
      2. If count >= threshold, run DBSCAN clustering
      3. Back-fill cluster_id into raw_events rows

    Threshold starts at 50 events, then becomes cluster-adaptive in P4.
    """
    try:
        from agent.evolution_manager import get_self_model_db
        from agent.emergent_clusterer import run_clustering_if_needed

        db_path = str(get_self_model_db())
        result = run_clustering_if_needed(db_path)
        if result:
            stats = result.get("update_stats", {})
            logger.info(
                "Emergent clustering: %d events → %d clusters in %.0fms",
                stats.get("events", 0),
                stats.get("clusters_found", 0),
                stats.get("time_ms", 0),
            )

            # ── Chain: clustering → lifecycle → emergence → skill extraction ──
            # After new clusters are created and old ones updated,
            # evaluate whether any cluster should transition stages.
            clusters_found = stats.get("clusters_found", 0)
            if clusters_found > 0:
                try:
                    from agent.cluster_lifecycle import run_lifecycle_evaluation
                    lc_stats = run_lifecycle_evaluation(db_path)
                    logger.info(
                        "Lifecycle evaluation: %d transitioned, %d stayed",
                        lc_stats.get("transitioned", 0),
                        lc_stats.get("stayed", 0),
                    )
                except Exception:
                    logger.debug("Lifecycle evaluation skipped", exc_info=True)

                # ── Emergence evaluation: does the system need new capabilities? ──
                # This is the涌现 trigger — not hardcoded, driven by accumulated
                # self_assessment signals and cluster statistics.
                #
                # NOTE: activate_capability() may run `pip install` (up to 120s
                # for chromadb). We must NOT block record_raw_event() — the
                # user's tool result depends on this function returning.
                # So we evaluate synchronously (fast DB queries) but defer
                # the actual install/activate to a background thread.
                try:
                    from agent.capability_evolver import run_emergence_cycle
                    decisions = run_emergence_cycle(db_path)
                    for d in decisions:
                        if d.action == "activate":
                            import threading
                            def _bg_activate(cap_name=d.capability_name):
                                """Activate a capability, but only after the
                                user confirms via the Gateway/desktop approval
                                flow. This is a privileged self-evolution action
                                (may run ``pip install``), so it must NOT be
                                applied automatically — consistent with the
                                human-in-the-loop gate used by ``self_modify``.
                                """
                                try:
                                    from tools.approval import (
                                        request_gateway_approval,
                                        get_current_session_key,
                                    )
                                    session_key = get_current_session_key()
                                    approval_data = {
                                        "type": "capability_activate",
                                        "capability": cap_name,
                                        "title": f"激活能力：{cap_name}",
                                        "description": (
                                            f"系统涌现决策建议激活能力 «{cap_name}»"
                                            f"（可能执行 pip install 安装依赖）。"
                                            f"是否允许？"
                                        ),
                                        "surface": "gui",
                                    }
                                    decision = request_gateway_approval(
                                        session_key, approval_data, surface="gui"
                                    )
                                    approved = decision.get("choice") in (
                                        "approve", "once", "session", "always"
                                    )
                                    if approved:
                                        from agent.capability_registry import activate_capability
                                        activate_capability(cap_name)
                                        logger.info("Capability auto-activated (user-approved): %s", cap_name)
                                        record_raw_event(
                                            tool_name="capability_activate",
                                            tool_args={"capability": cap_name, "initiator": "system"},
                                            result=f"activated: {cap_name}",
                                            is_error=False,
                                            duration=0.0,
                                            trigger_clustering=False,
                                        )
                                    else:
                                        logger.info("Capability activation denied by user: %s", cap_name)
                                        record_raw_event(
                                            tool_name="capability_activate",
                                            tool_args={"capability": cap_name, "initiator": "system"},
                                            result=f"denied: {cap_name}",
                                            is_error=False,
                                            duration=0.0,
                                            trigger_clustering=False,
                                        )
                                except Exception:
                                    logger.warning("Background capability activation (gated) failed", exc_info=True)
                            threading.Thread(
                                target=_bg_activate,
                                daemon=True,
                                name=f"cap-activate-{d.capability_name}",
                            ).start()
                except Exception:
                    logger.debug("Emergence cycle skipped", exc_info=True)

                # ── Skill extraction: are there repetitive patterns to extract? ──
                try:
                    from agent.skill_extractor import extract_skills
                    new_skills = extract_skills(db_path)
                    if new_skills:
                        logger.info("Skills extracted: %d", len(new_skills))
                except Exception:
                    logger.debug("Skill extraction skipped", exc_info=True)
    except Exception:
        logger.debug("Clustering trigger skipped", exc_info=True)


# ── Retention ────────────────────────────────────────────────────────────────

def cleanup_raw_events(db_path: str, retention_days: int = 90) -> int:
    """Delete raw_events older than retention_days, unless protected.

    Returns the number of deleted rows.
    """
    try:
        conn = sqlite3.connect(db_path)
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM raw_events WHERE timestamp < ? AND protected = 0",
            (cutoff,),
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted:
            logger.info("Retention cleanup: deleted %d raw_events older than %d days", deleted, retention_days)
        return deleted
    except Exception:
        logger.debug("cleanup_raw_events failed", exc_info=True)
        return 0


# ── Query Helpers ────────────────────────────────────────────────────────────

def get_recent_raw_events(
    db_path: str,
    limit: int = 100,
    session_id: Optional[str] = None,
    tool_name: Optional[str] = None,
) -> list[RawEvent]:
    """Query recent raw events, optionally filtered by session or tool."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        ensure_raw_events_table(conn)
        cursor = conn.cursor()

        where_clauses = []
        params: list = []

        if session_id:
            where_clauses.append("session_id = ?")
            params.append(session_id)
        if tool_name:
            where_clauses.append("tool_name = ?")
            params.append(tool_name)

        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        query = f"SELECT * FROM raw_events {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [RawEvent.from_db_row(r) for r in rows]
    except Exception:
        logger.debug("get_recent_raw_events failed", exc_info=True)
        return []


def get_unclustered_count(db_path: str) -> int:
    """Count raw_events that haven't been clustered yet (for P2 trigger)."""
    try:
        conn = sqlite3.connect(db_path)
        ensure_raw_events_table(conn)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM raw_events WHERE cluster_id IS NULL")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def get_raw_event_stats(db_path: str) -> Dict[str, Any]:
    """Summary stats of the raw_events table."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        ensure_raw_events_table(conn)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) as total FROM raw_events")
        total = cursor.fetchone()["total"]

        cursor.execute(
            "SELECT success, COUNT(*) as cnt FROM raw_events GROUP BY success"
        )
        success_counts = {row["success"]: row["cnt"] for row in cursor.fetchall()}

        cursor.execute("SELECT COUNT(DISTINCT session_id) as sessions FROM raw_events")
        sessions = cursor.fetchone()["sessions"]

        cursor.execute(
            "SELECT tool_name, COUNT(*) as cnt FROM raw_events GROUP BY tool_name ORDER BY cnt DESC LIMIT 10"
        )
        top_tools = [(row["tool_name"], row["cnt"]) for row in cursor.fetchall()]

        cursor.execute("SELECT COUNT(*) as clustered FROM raw_events WHERE cluster_id IS NOT NULL")
        clustered = cursor.fetchone()["clustered"]

        conn.close()

        success_count = success_counts.get(1, 0)
        error_count = success_counts.get(0, 0)

        return {
            "total": total,
            "success_count": success_count,
            "error_count": error_count,
            "success_rate": round(success_count / total * 100, 1) if total > 0 else 0,
            "sessions": sessions,
            "top_tools": top_tools,
            "clustered": clustered,
            "unclustered": total - clustered,
        }
    except Exception:
        logger.debug("get_raw_event_stats failed", exc_info=True)
        return {"total": 0, "error": "stats unavailable"}
