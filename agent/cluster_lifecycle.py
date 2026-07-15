"""
agent/cluster_lifecycle.py — 簇驱动生命周期管理

每个簇有自己的生命周期状态机，阈值从自身事件间隔涌现。
不做一刀切定时删除——不同用户的不同簇有不同的活跃节奏。

状态机:
  emerging → stable → declining → dormant → dead
  dormant 可被新事件唤醒 → stable
  dead 可被新事件复活 → emerging (保留 evolved_from 链)
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class LifecycleThresholds:
    """Per-cluster lifecycle thresholds (all derived from cluster's own data)."""
    n_declining: float    # inactive duration to enter declining (seconds)
    m_dormant: float      # inactive duration to enter dormant (seconds)
    k_dead: float         # inactive duration to enter dead (seconds)

    @property
    def description(self) -> str:
        """Human-readable threshold summary."""
        def fmt(secs: float) -> str:
            if secs >= 86400:
                return f"{secs / 86400:.0f}d"
            if secs >= 3600:
                return f"{secs / 3600:.1f}h"
            return f"{secs / 60:.0f}min"
        return f"decline>{fmt(self.n_declining)}, dormant>{fmt(self.m_dormant)}, dead>{fmt(self.k_dead)}"


# ── Lifecycle Manager ────────────────────────────────────────────────────────

class ClusterLifecycleManager:
    """Manages lifecycle state transitions for all clusters.

    Each cluster's thresholds are computed from its own event interval
    distribution, so a trading cluster (avg 15min interval) and a monthly
    report cluster (avg 30d interval) get completely different lifecycles.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def evaluate_all(self) -> Dict[str, int]:
        """Evaluate lifecycle transitions for all active clusters.

        Returns:
            {"transitioned": N, "stayed": M, "errors": E}
        """
        stats = {"transitioned": 0, "stayed": 0, "errors": 0}

        try:
            clusters = self._load_active_clusters()
            for cluster in clusters:
                old_stage = cluster.get("lifecycle_stage", "emerging")
                new_stage = self._evaluate_cluster(cluster)

                if new_stage != old_stage:
                    self._record_transition(cluster["id"], old_stage, new_stage)
                    self._update_stage(cluster["id"], new_stage)
                    stats["transitioned"] += 1
                else:
                    stats["stayed"] += 1

        except Exception:
            logger.debug("Lifecycle evaluation failed", exc_info=True)
            stats["errors"] += 1

        return stats

    def on_new_event(self, cluster_id: int) -> Optional[str]:
        """Handle new event arriving in a cluster.

        - dormant → stable (wake up)
        - dead → emerging (resurrect, preserve evolved_from)
        - other stages: no change (just update last_active_at)

        Returns the new stage if transitioned, None otherwise.
        """
        try:
            cluster = self._load_cluster(cluster_id)
            if cluster is None:
                return None

            stage = cluster.get("lifecycle_stage", "emerging")
            now = datetime.now().isoformat()

            if stage == "dormant":
                self._record_transition(cluster_id, "dormant", "stable", "new_event_wake")
                self._update_stage(cluster_id, "stable")
                self._touch_last_active(cluster_id, now)
                logger.info("Cluster %d woke up: dormant → stable", cluster_id)
                return "stable"

            elif stage == "dead":
                self._record_transition(cluster_id, "dead", "emerging", "new_event_resurrect")
                self._update_stage(cluster_id, "emerging")
                self._touch_last_active(cluster_id, now)
                logger.info("Cluster %d resurrected: dead → emerging", cluster_id)
                return "emerging"

            else:
                # emerging/stable/declining: just update last_active_at
                self._touch_last_active(cluster_id, now)
                return None

        except Exception:
            logger.debug("on_new_event failed for cluster %d", cluster_id, exc_info=True)
            return None

    def compute_thresholds(self, cluster: Dict[str, Any]) -> LifecycleThresholds:
        """Compute lifecycle thresholds from cluster's own event interval distribution.

        N (declining) = avg_interval × 3
        M (dormant)   = avg_interval × 6
        K (dead)      = avg_interval × 15

        For a cluster with 15min avg interval:
          N=45min, M=90min, K=225min

        For a cluster with 30d avg interval:
          N=90d, M=180d, K=450d
        """
        avg_interval = self._compute_avg_interval(cluster)

        # Fallback if no interval data: use conservative defaults
        if avg_interval <= 0:
            # Default: 1h interval assumption
            avg_interval = 3600.0

        return LifecycleThresholds(
            n_declining=avg_interval * 3,
            m_dormant=avg_interval * 6,
            k_dead=avg_interval * 15,
        )

    def _compute_avg_interval(self, cluster: Dict[str, Any]) -> float:
        """Compute average event interval for a cluster (seconds)."""
        cluster_id = cluster.get("id")
        if cluster_id is None:
            return 0.0

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                """SELECT timestamp FROM raw_events
                   WHERE cluster_id = ?
                   ORDER BY timestamp ASC""",
                (cluster_id,)
            )
            timestamps = [row[0] for row in cursor.fetchall()]
            conn.close()

            if len(timestamps) < 2:
                # Fallback: use cluster's first_seen → last_seen span
                first = cluster.get("first_seen")
                last = cluster.get("last_seen")
                if first and last:
                    try:
                        t1 = datetime.fromisoformat(first)
                        t2 = datetime.fromisoformat(last)
                        span = (t2 - t1).total_seconds()
                        count = max(cluster.get("event_count", 1), 1)
                        return span / count if count > 0 else 0.0
                    except (ValueError, TypeError):
                        pass
                return 0.0

            # Compute average interval from consecutive timestamps
            intervals = []
            for i in range(1, len(timestamps)):
                try:
                    t1 = datetime.fromisoformat(timestamps[i-1])
                    t2 = datetime.fromisoformat(timestamps[i])
                    gap = (t2 - t1).total_seconds()
                    if gap > 0:
                        intervals.append(gap)
                except (ValueError, TypeError):
                    continue

            return sum(intervals) / len(intervals) if intervals else 0.0

        except Exception:
            return 0.0

    def _evaluate_cluster(self, cluster: Dict[str, Any]) -> str:
        """Determine the correct lifecycle stage for a cluster."""
        stage = cluster.get("lifecycle_stage", "emerging")
        last_active = cluster.get("last_active_at") or cluster.get("last_seen", "")
        event_count = cluster.get("event_count", 0)

        if not last_active:
            return stage

        try:
            last_time = datetime.fromisoformat(last_active)
        except (ValueError, TypeError):
            return stage

        inactive_seconds = (datetime.now() - last_time).total_seconds()
        thresholds = self.compute_thresholds(cluster)

        # Promotion: emerging → stable (based on cluster quality, not recency)
        # When a cluster has enough events, it graduates — regardless of how
        # long ago they happened. Inactivity is handled by the demotion chain
        # with absolute time thresholds, not the cluster's own interval.
        if stage == "emerging":
            if event_count >= 5:
                # Long inactivity while still emerging means the cluster
                # never really 'took off'. Use absolute thresholds.
                if inactive_seconds >= 7 * 86400:  # 7 days
                    return "dead"
                return "stable"
            return stage

        # Demotion chain: stable → declining → dormant → dead
        if stage == "stable":
            if inactive_seconds >= thresholds.k_dead:
                return "dead"
            elif inactive_seconds >= thresholds.m_dormant:
                return "dormant"
            elif inactive_seconds >= thresholds.n_declining:
                return "declining"
            return "stable"

        if stage == "declining":
            if inactive_seconds >= thresholds.k_dead:
                return "dead"
            elif inactive_seconds >= thresholds.m_dormant:
                return "dormant"
            return "declining"

        if stage == "dormant":
            if inactive_seconds >= thresholds.k_dead:
                return "dead"
            return "dormant"

        return stage

    # ── DB Operations ───────────────────────────────────────────────────────

    def _load_active_clusters(self) -> List[Dict[str, Any]]:
        """Load all active clusters for evaluation."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM clusters WHERE is_active = 1"
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _load_cluster(self, cluster_id: int) -> Optional[Dict[str, Any]]:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM clusters WHERE id = ?", (cluster_id,))
            row = cursor.fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception:
            return None

    def _update_stage(self, cluster_id: int, new_stage: str) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE clusters SET lifecycle_stage = ? WHERE id = ?",
                (new_stage, cluster_id)
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.debug("Failed to update stage for cluster %d", cluster_id, exc_info=True)

    def _touch_last_active(self, cluster_id: int, timestamp: str) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE clusters SET last_active_at = ?, last_seen = ? WHERE id = ?",
                (timestamp, timestamp, cluster_id)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _record_transition(
        self, cluster_id: int, from_stage: str, to_stage: str, reason: str = ""
    ) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """INSERT INTO cluster_lifecycle_events
                   (cluster_id, from_stage, to_stage, reason)
                   VALUES (?, ?, ?, ?)""",
                (cluster_id, from_stage, to_stage, reason)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    # ── Data Retention ──────────────────────────────────────────────────────

    def cleanup_dead_clusters(self, max_events_to_delete: int = 1000) -> int:
        """Clean up raw_events from dead clusters (preserving protected events).

        Protected events (protected=1) are never deleted.
        Returns count of deleted events.
        """
        deleted = 0
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Find dead cluster IDs
            cursor.execute(
                "SELECT id FROM clusters WHERE lifecycle_stage = 'dead'"
            )
            dead_ids = [row[0] for row in cursor.fetchall()]

            if not dead_ids:
                conn.close()
                return 0

            # Delete unprotected events from dead clusters
            # SQLite doesn't support DELETE ... LIMIT, so use subquery
            placeholders = ",".join("?" * len(dead_ids))
            cursor.execute(
                f"""DELETE FROM raw_events
                    WHERE id IN (
                      SELECT id FROM raw_events
                      WHERE cluster_id IN ({placeholders})
                        AND protected = 0
                      LIMIT ?
                    )""",
                (*dead_ids, max_events_to_delete)
            )
            deleted = cursor.rowcount

            # Also clean up lifecycle_events for dead clusters
            cursor.execute(
                f"""DELETE FROM cluster_lifecycle_events
                    WHERE cluster_id IN ({placeholders})
                      AND id NOT IN (
                        SELECT MAX(id) FROM cluster_lifecycle_events
                        WHERE cluster_id IN ({placeholders})
                        GROUP BY cluster_id
                      )""",
                (*dead_ids, *dead_ids),
            )
            _events_deleted = cursor.rowcount
            conn.commit()
            conn.close()

            if deleted > 0:
                logger.info("Cleaned up %d events from dead clusters", deleted)
            if _events_deleted > 0:
                logger.info("Cleaned up %d lifecycle_events for dead clusters", _events_deleted)

        except Exception:
            logger.debug("Dead cluster cleanup failed", exc_info=True)

        return deleted


# ── Convenience Functions ────────────────────────────────────────────────────

def run_lifecycle_evaluation(db_path: str) -> Dict[str, int]:
    """Run lifecycle evaluation for all clusters. Returns transition stats."""
    manager = ClusterLifecycleManager(db_path)
    return manager.evaluate_all()


def wake_cluster_on_event(db_path: str, cluster_id: int) -> Optional[str]:
    """Handle new event arriving in a cluster (wake if dormant/dead)."""
    manager = ClusterLifecycleManager(db_path)
    return manager.on_new_event(cluster_id)
