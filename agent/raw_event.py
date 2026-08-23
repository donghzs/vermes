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
    variant_hash: Optional[str] = None  # P4: active processor variant when this tool ran (None for non-processor tools)

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
            self.variant_hash,
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
            variant_hash=row["variant_hash"] if "variant_hash" in row.keys() else None,
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
    protected       INTEGER DEFAULT 0,
    variant_hash    TEXT    DEFAULT NULL
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
    # ── Migration FIRST: CREATE TABLE IF NOT EXISTS won't add columns to an
    #    already-existing table. If raw_events predates variant_hash (P4),
    #    ALTER it before any index on variant_hash is created. Idempotent. ──
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(raw_events)")}
        if cols and "variant_hash" not in cols:
            conn.execute("ALTER TABLE raw_events ADD COLUMN variant_hash TEXT DEFAULT NULL")
    except Exception:
        logger.debug("variant_hash migration skipped", exc_info=True)
    conn.executescript(RAW_EVENTS_TABLE_SQL)
    # variant_hash index (column now guaranteed to exist on fresh + migrated tables)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_events_variant ON raw_events(variant_hash)")
    except Exception:
        logger.debug("variant_hash index skipped", exc_info=True)
    # Compatibility view: maps raw_events → outcomes schema
    # domain/error_type are '' (legacy code never populated them meaningfully)
    # role is 'default' (roles table rarely exists, detect_role always returned 'default')
    #
    # DROP+CREATE (not IF NOT EXISTS):存量库的旧视图定义不会被 IF NOT EXISTS 更新,
    # 必须显式 DROP 才能把 __verified__/__self_validation__ 过滤写进去。
    # 幂等:DROP IF EXISTS 对全新库无副作用。
    conn.execute("DROP VIEW IF EXISTS v_outcomes")
    conn.execute(
        """CREATE VIEW v_outcomes AS
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
           FROM raw_events
           WHERE tool_name NOT IN ('__verified__', '__self_validation__')"""
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
                duration, session_id, turn_number, cluster_id, embedding_id,
                protected, variant_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
    variant_hash: Optional[str] = None,
    skip_embedding: bool = False,
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
        variant_hash: P4 — active processor variant hash when this tool ran
                      (None for non-processor tools / processors with no variants)

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
        variant_hash=variant_hash,
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
            if not skip_embedding:
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
        except Exception as e:
            logger.debug("raw_event.py: record raw event failed: %s", e)

    return rowid


def record_verification(
    tool_name: str,
    verified: bool,
    detail: str = "",
    agent: Any = None,
) -> Optional[int]:
    """持久化统一的工具验证信号（P4 verified）为一条 raw_event。

    背景：self_validator（每工具结果都跑）与 P0-A outcome_verifier 已经算出
    verdict，但前者只 append warning / 记 ``__self_validation__`` 事件，后者只
    写 ``agent._recent_tool_verify``（当回合内存、供 CriticJudge）。两者都**不**
    产生一个跨会话可聚合的"本工具是否通过验证"信号。本函数补上这一环：每个
    工具调用写一条 ``tool_name="__verified__"`` 的事件，``success`` 列即 verdict，
    供 EvolutionPanel 算"未验证率/接地率"趋势。

    fail-open：任何异常只返回 None，绝不阻断工具执行 / agent loop。
    trigger_clustering=False：验证事件是派生信号，不应再触发涌现链（避免与
    ``__self_validation__`` 重复驱动聚类）。
    """
    try:
        session_id = getattr(agent, "session_id", "") or "" if agent else ""
        turn_number = getattr(agent, "turn_counter", 0) or 0 if agent else 0
        return record_raw_event(
            tool_name="__verified__",
            tool_args={"verified": bool(verified), "tool": tool_name},
            result=detail or "",
            is_error=not verified,
            duration=0.0,
            session_id=session_id,
            turn_number=turn_number,
            trigger_clustering=False,
            skip_embedding=True,
        )
    except Exception as e:  # noqa: BLE001 - 信号持久化失败绝不阻断
        logger.debug("record_verification(%s) failed (fail-open): %s", tool_name, e)
        return None


def record_retraction(
    target_type: str,
    target_name: str,
    reason: str = "",
    session_id: str = "",
    turn_number: int = 0,
) -> Optional[int]:
    """Record a logical retraction of a capability or insight.

    This does NOT delete the original event — it records a new event marking
    the target as retracted. The emergence cycle checks for retraction events
    and filters out retracted items.

    Args:
        target_type:  "capability" or "insight"
        target_name:  Name of the capability/insight being retracted
        reason:       Optional human-readable reason for the retraction
        session_id:   Session identifier
        turn_number:  Turn number within the session

    Returns:
        Row ID of the retraction event, or None on failure.
    """
    return record_raw_event(
        tool_name="__retraction__",
        tool_args={
            "target_type": target_type,
            "target_name": target_name,
            "reason": reason,
        },
        result=f"retracted: {target_type}:{target_name}",
        is_error=False,
        duration=0.0,
        session_id=session_id,
        turn_number=turn_number,
        trigger_clustering=False,  # retraction must NOT re-trigger emergence
    )


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
                    logger.info("Emergence cycle: %d decision(s) generated", len(decisions))
                    for d in decisions:
                        if d.action == "activate":
                            import threading
                            def _bg_activate(cap_name=d.capability_name):
                                """Activate a capability at the tier it deserves (T2).

                                Previously *every* activation blocked on a
                                Gateway popup, with no remembered approval —
                                the "安全的老在问" half of the inverted
                                tiering.  Now the decision is made from the
                                capability itself:

                                - **L2** (``pip install`` needed): still a
                                  popup, but via ``approve_privileged_action``
                                  so an answer is remembered for the whole
                                  ``capability_activate`` scope (T1b), instead
                                  of asking once per capability per cycle.
                                - **L1** (built-in / already installed):
                                  activate silently and drop a notification in
                                  the change ledger.  Nothing durable happens —
                                  ``_CAPABILITIES`` is in-process only, so a
                                  restart already undoes it.
                                """
                                try:
                                    from agent.capability_registry import (
                                        activate_capability,
                                        classify_activation_tier,
                                    )
                                    _t = classify_activation_tier(cap_name)
                                    tier, why = _t["tier"], _t["reason"]
                                    # T6：按用户档位调整。reversible 取自动作
                                    # 本身 —— 要 pip install 的一律不可逆，
                                    # 所以 autonomous 也放宽不了它。
                                    try:
                                        from tools.approval import effective_tier
                                        tier = effective_tier(
                                            tier, reversible=not _t.get("needs_install", False),
                                        )
                                    except Exception:
                                        pass

                                    if tier == "L2":
                                        from tools.approval import (
                                            approve_privileged_action,
                                            get_current_session_key,
                                        )
                                        session_key = get_current_session_key()
                                        approved = approve_privileged_action(
                                            session_key,
                                            {
                                                "type": "capability_activate",
                                                "category": "capability_activate",
                                                "pattern_key": "capability_activate",
                                                "capability": cap_name,
                                                "tier": "L2",
                                                "title": f"激活能力：{cap_name}",
                                                "description": (
                                                    f"系统涌现决策建议激活能力 «{cap_name}»。\n"
                                                    f"⚠ {why}。是否允许？"
                                                ),
                                                "surface": "gui",
                                            },
                                            surface="gui",
                                        )
                                        if not approved:
                                            logger.info("Capability activation denied by user: %s", cap_name)
                                            record_raw_event(
                                                tool_name="capability_activate",
                                                tool_args={"capability": cap_name, "initiator": "system"},
                                                result=f"denied: {cap_name}",
                                                is_error=False,
                                                duration=0.0,
                                                trigger_clustering=False,
                                            )
                                            return

                                    ok, detail = activate_capability(cap_name)
                                    logger.info(
                                        "Capability activation (%s): %s → %s (%s)",
                                        tier, cap_name, ok, detail,
                                    )
                                    record_raw_event(
                                        tool_name="capability_activate",
                                        tool_args={"capability": cap_name, "initiator": "system",
                                                   "tier": tier},
                                        result=f"{'activated' if ok else 'failed'}: {cap_name} ({detail})",
                                        is_error=not ok,
                                        duration=0.0,
                                        trigger_clustering=False,
                                    )
                                    # 通知中心：L1 是「静默执行」，不是「不告诉你」。
                                    # 账本失败只降级，绝不回滚一次成功的激活。
                                    if ok:
                                        try:
                                            from agent.change_ledger import (
                                                record_change, KIND_CAPABILITY_ACTIVATED,
                                            )
                                            record_change(
                                                kind=KIND_CAPABILITY_ACTIVATED,
                                                tier=tier,
                                                title=f"已激活能力：{cap_name}",
                                                summary=why,
                                                detail={"capability": cap_name, "detail": detail},
                                            )
                                        except Exception as _led_err:
                                            logger.warning(
                                                "[Changes] capability notice failed: %s", _led_err,
                                            )
                                except Exception as _act_err:
                                    # Bug 2 visible: a failure here (e.g. frozen
                                    # bundle missing tools.approval) was previously
                                    # swallowed at warning level, making "decision
                                    # generated but never landed" indistinguishable
                                    # from "user denied". Promote to error and
                                    # record a failure event so the two are
                                    # separable in the raw_event stream.
                                    logger.error(
                                        "Background capability activation (gated) failed: %s",
                                        _act_err, exc_info=True,
                                    )
                                    try:
                                        record_raw_event(
                                            tool_name="capability_activate",
                                            tool_args={
                                                "capability": cap_name,
                                                "initiator": "system",
                                                "error": str(_act_err)[:200],
                                            },
                                            result=f"failed: {cap_name}",
                                            is_error=True,
                                            duration=0.0,
                                            trigger_clustering=False,
                                        )
                                    except Exception:
                                        pass
                            threading.Thread(
                                target=_bg_activate,
                                daemon=True,
                                name=f"cap-activate-{d.capability_name}",
                            ).start()
                except Exception:
                    logger.info("Emergence cycle skipped", exc_info=True)

                # ── Skill extraction: are there repetitive patterns to extract? ──
                try:
                    from agent.skill_extractor import extract_skills
                    new_skills = extract_skills(db_path)
                    if new_skills:
                        logger.info("Skills extracted: %d", len(new_skills))
                except Exception:
                    logger.info("Skill extraction skipped", exc_info=True)

                # ── H4.3 评测闭环：提取后评估 active 技能生命周期（fail-open）──
                try:
                    from agent.skill_extractor import evaluate_skill_lifecycle
                    _life = evaluate_skill_lifecycle(db_path)
                    if _life.get("demoted") or _life.get("reactivated") or _life.get("promoted"):
                        logger.info(
                            "Skill lifecycle eval: evaluated=%d demoted=%d promoted=%d reactivated=%d",
                            _life["evaluated"], _life["demoted"], _life["promoted"], _life["reactivated"],
                        )
                except Exception:
                    logger.info("Skill lifecycle eval skipped", exc_info=True)

                # ── P4-E: 变体进化闭环（GRPO 式组内相对排序 + 治理收口晋升）──
                # outcome 已在 P4-A 写入 raw_events.variant_hash；此处按 should_rank
                # 门控（事件驱动 + MIN_INTERVAL）打分，promote_best_variant 按治理
                # 分层落地（L1 自动 / L2·inline 提案）。fail-open，绝不破坏涌现链。
                try:
                    from agent.variant_ranker import run_variant_evolution_for_all
                    _ve = run_variant_evolution_for_all(db_path)
                    _acted = [r for r in _ve if r.get("ranked")]
                    if _acted:
                        logger.info("Variant evolution: %d processor(s) ranked", len(_acted))
                except Exception:
                    logger.debug("Variant evolution skipped", exc_info=True)
    except Exception:
        logger.info("Clustering trigger skipped", exc_info=True)


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


# ── Phase 4.1: 会话重放（事件溯源读取侧闭环） ──────────────────────────────────

@dataclass
class ReplayState:
    """一次会话重放后重建的可重放状态。

    由 replay_session() 产出，是 append-only 事件层（raw_events）的读取侧闭环——
    给定 session_id，按 turn_number 顺序重放全部工具事件，重建出调用序列与统计，
    无需依赖任何运行时内存状态（崩溃后亦可无损重建）。
    """

    session_id: str
    total_events: int
    turns: int
    success_count: int
    error_count: int
    tools_used: Dict[str, int]
    timeline: List[Dict[str, Any]]  # [(turn, tool, success, duration, ts), ...]，按 turn 升序

    @property
    def success_rate(self) -> float:
        if self.total_events == 0:
            return 0.0
        return round(self.success_count / self.total_events * 100, 1)

    def summary(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "total_events": self.total_events,
            "turns": self.turns,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "success_rate": self.success_rate,
            "tools_used": self.tools_used,
        }


def replay_session(db_path: str, session_id: str) -> ReplayState:
    """按 turn_number 升序重放某会话的全部工具事件，重建 ReplayState。

    事件溯源读取闭环：纯函数式从 raw_events 重建，不依赖任何进程内状态。
    排序以 turn_number 为主、timestamp 为辅，保证回合顺序稳定。
    """
    events = get_recent_raw_events(db_path, limit=10_000, session_id=session_id)
    # 升序：turn_number 主序、timestamp 次序（防御同 turn 多事件）
    events.sort(key=lambda e: (e.turn_number, e.timestamp))
    tools_used: Dict[str, int] = {}
    timeline: List[Dict[str, Any]] = []
    success_count = 0
    error_count = 0
    max_turn = 0
    for e in events:
        tools_used[e.tool_name] = tools_used.get(e.tool_name, 0) + 1
        if e.success:
            success_count += 1
        else:
            error_count += 1
        if e.turn_number > max_turn:
            max_turn = e.turn_number
        timeline.append({
            "turn": e.turn_number,
            "tool": e.tool_name,
            "success": e.success,
            "duration": e.duration,
            "timestamp": e.timestamp,
        })
    return ReplayState(
        session_id=session_id,
        total_events=len(events),
        turns=max_turn,
        success_count=success_count,
        error_count=error_count,
        tools_used=tools_used,
        timeline=timeline,
    )
