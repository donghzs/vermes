"""
agent/cross_session_continuity.py — 跨会话延续

簇和模块在会话间无缝延续：
  - 会话结束时记录簇状态快照
  - 新会话开始时对比快照，展示演化简报
  - 模块启用/禁用状态跨会话持久化
  - clusters.evolved_from + lifecycle_events 形成完整演化链
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class ClusterSnapshot:
    """A point-in-time snapshot of all clusters for session handoff."""
    session_id: str
    timestamp: str
    clusters: List[Dict[str, Any]] = field(default_factory=list)
    module_count: int = 0
    total_events: int = 0
    active_clusters: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "clusters": self.clusters,
            "module_count": self.module_count,
            "total_events": self.total_events,
            "active_clusters": self.active_clusters,
        }


@dataclass
class ContinuityBriefing:
    """A briefing comparing the current state to the last snapshot."""
    new_clusters: List[str] = field(default_factory=list)
    evolved_clusters: List[str] = field(default_factory=list)
    dormant_clusters: List[str] = field(default_factory=list)
    dead_clusters: List[str] = field(default_factory=list)
    new_modules: List[str] = field(default_factory=list)
    total_clusters: int = 0
    total_events_since: int = 0
    last_snapshot_time: str = ""

    def is_empty(self) -> bool:
        return not any([
            self.new_clusters, self.evolved_clusters,
            self.dormant_clusters, self.dead_clusters,
            self.new_modules
        ])

    def to_prompt_text(self) -> str:
        """Render as a brief text for session start."""
        if self.is_empty():
            return ""

        lines: List[str] = []

        if self.new_clusters:
            lines.append(f"新行为模式: {', '.join(self.new_clusters[:3])}")

        if self.evolved_clusters:
            lines.append(f"模式演化: {', '.join(self.evolved_clusters[:3])}")

        if self.dormant_clusters:
            lines.append(f"进入休眠: {', '.join(self.dormant_clusters[:2])}")

        if self.new_modules:
            lines.append(f"新领域模块: {', '.join(self.new_modules)}")

        if self.total_events_since > 0:
            lines.append(f"自上次会话以来: {self.total_events_since} 次操作")

        return "\n".join(lines)


# ── Snapshot Store ───────────────────────────────────────────────────────────

SNAPSHOTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS cluster_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    data        TEXT NOT NULL,  -- JSON blob
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_snapshots_session ON cluster_snapshots(session_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON cluster_snapshots(timestamp);
"""

# ── B 硬容量护栏：快照数量上限 ──
# 超限时删除最旧快照（删的是快照索引，不是事实数据；快照本身是状态副本）
_MAX_SNAPSHOTS = 100


class CrossSessionContinuity:
    """Manages cluster/module continuity across sessions."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def ensure_tables(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SNAPSHOTS_TABLE_SQL)
        conn.commit()
        conn.close()

    def save_snapshot(self, session_id: str) -> ClusterSnapshot:
        """Save a snapshot of current cluster state at session end."""
        self.ensure_tables()

        clusters = self._load_cluster_states()
        module_count = self._count_active_modules()
        total_events = sum(c.get("event_count", 0) for c in clusters)
        active = sum(1 for c in clusters if c.get("is_active", 0))

        snapshot = ClusterSnapshot(
            session_id=session_id,
            timestamp=datetime.now().isoformat(),
            clusters=clusters,
            module_count=module_count,
            total_events=total_events,
            active_clusters=active,
        )

        # Save to DB
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO cluster_snapshots (session_id, timestamp, data) VALUES (?, ?, ?)",
            (session_id, snapshot.timestamp, json.dumps(snapshot.to_dict()))
        )
        # B 硬容量护栏：超限时删除最旧快照
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM cluster_snapshots")
            total = cursor.fetchone()[0]
            if total > _MAX_SNAPSHOTS:
                excess = total - _MAX_SNAPSHOTS
                cursor.execute(
                    "DELETE FROM cluster_snapshots WHERE id IN ("
                    "SELECT id FROM cluster_snapshots ORDER BY id ASC LIMIT ?)",
                    (excess,)
                )
                logger.info(
                    "Capacity guard: trimmed %d old cluster snapshots (total was %d, limit %d)",
                    excess, total, _MAX_SNAPSHOTS,
                )
        except Exception:
            logger.debug("Snapshot capacity trim failed", exc_info=True)
        conn.commit()
        conn.close()

        return snapshot

    def load_last_snapshot(self) -> Optional[ClusterSnapshot]:
        """Load the most recent snapshot."""
        self.ensure_tables()
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT session_id, timestamp, data FROM cluster_snapshots ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            data = json.loads(row[2])
            return ClusterSnapshot(
                session_id=row[0],
                timestamp=row[1],
                clusters=data.get("clusters", []),
                module_count=data.get("module_count", 0),
                total_events=data.get("total_events", 0),
                active_clusters=data.get("active_clusters", 0),
            )
        except Exception:
            return None

    def generate_briefing(self) -> ContinuityBriefing:
        """Generate a briefing comparing current state to last snapshot."""
        briefing = ContinuityBriefing()
        last = self.load_last_snapshot()

        if last is None:
            return briefing

        briefing.last_snapshot_time = last.timestamp

        current_clusters = self._load_cluster_states()
        current_by_id = {c["id"]: c for c in current_clusters}
        last_by_id = {c["id"]: c for c in last.clusters}

        # New clusters (in current but not in last)
        for cid, cluster in current_by_id.items():
            if cid not in last_by_id:
                briefing.new_clusters.append(cluster.get("name", f"cluster_{cid}"))
            else:
                old = last_by_id[cid]
                # Evolved: name changed or stage transitioned
                if (old.get("name") != cluster.get("name") or
                    old.get("lifecycle_stage") != cluster.get("lifecycle_stage")):
                    if cluster.get("lifecycle_stage") == "dormant":
                        briefing.dormant_clusters.append(cluster.get("name", ""))
                    elif cluster.get("lifecycle_stage") == "dead":
                        briefing.dead_clusters.append(cluster.get("name", ""))
                    else:
                        briefing.evolved_clusters.append(cluster.get("name", ""))

        # New modules since last snapshot
        try:
            from agent.domain_modules import DomainModuleManager
            manager = DomainModuleManager(self.db_path)
            current_modules = manager.list_modules(active_only=True)
            last_module_count = last.module_count
            if len(current_modules) > last_module_count:
                for m in current_modules[last_module_count:]:
                    briefing.new_modules.append(m.name)
        except Exception:
            pass

        # Events since last snapshot
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM raw_events WHERE timestamp > ?",
                (last.timestamp,)
            )
            briefing.total_events_since = cursor.fetchone()[0]
            conn.close()
        except Exception:
            pass

        briefing.total_clusters = len(current_clusters)
        return briefing

    def get_session_start_prompt(self) -> str:
        """Generate a continuity prompt for session start.

        Returns empty string if no previous snapshot or no changes.
        """
        briefing = self.generate_briefing()
        if briefing.is_empty() and briefing.total_events_since == 0:
            return ""

        text = briefing.to_prompt_text()
        if not text:
            return ""

        return f"<continuity>\n{text}\n</continuity>"

    # ── DB Helpers ──────────────────────────────────────────────────────────

    def _load_cluster_states(self) -> List[Dict[str, Any]]:
        """Load current state of all clusters."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            from agent.emergent_clusterer import ensure_cluster_tables
            ensure_cluster_tables(conn)

            cursor.execute("SELECT * FROM clusters ORDER BY id")
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _count_active_modules(self) -> int:
        """Count active domain modules."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM domain_modules WHERE is_active = 1")
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0


# ── Convenience Functions ────────────────────────────────────────────────────

def save_session_snapshot(db_path: str, session_id: str) -> ClusterSnapshot:
    """Save a snapshot at session end."""
    continuity = CrossSessionContinuity(db_path)
    return continuity.save_snapshot(session_id)


def get_continuity_prompt(db_path: str) -> str:
    """Get a continuity prompt for session start."""
    continuity = CrossSessionContinuity(db_path)
    return continuity.get_session_start_prompt()
