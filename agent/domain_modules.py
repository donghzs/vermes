"""
agent/domain_modules.py — 垂直领域模块热插拔

从稳定活跃的簇中自动涌现"业务模块"，不同用户自动涌现不同模块。
模块可启用/禁用（热插拔），启用时策略/反模式注入 system prompt。

升级条件（全部涌现，不预设阈值）:
  1. 稳定性: lifecycle_stage == 'stable' 且持续稳定
  2. 规模: event_count > 所有簇中位数 × 1.5
  3. 洞察密度: 该簇已产生反模式或策略
  4. 独特性: 特征向量与其他簇余弦距离 > 0.3
  满足 ≥3 个 → 自动标记为模块候选
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class DomainModule:
    """A vertical domain module emerged from cluster data."""
    id: int
    cluster_id: int
    name: str
    description: str = ""
    event_count: int = 0
    success_rate: float = 0.0
    is_active: bool = True
    activated_at: str = ""
    insights_summary: str = ""  # brief summary for prompt injection
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_prompt_block(self) -> str:
        """Render as a prompt block for system prompt injection."""
        lines = [f"[{self.name}] {'启用中' if self.is_active else '已禁用'}"]
        if self.event_count > 0:
            lines.append(f"  操作: {self.event_count} 次 | 成功率: {self.success_rate:.0%}")
        if self.insights_summary:
            lines.append(f"  {self.insights_summary}")
        return "\n".join(lines)


# ── Module Emergence Detector ────────────────────────────────────────────────

class ModuleEmergenceDetector:
    """Detects which stable clusters qualify as domain modules.

    Conditions (all relative, no presets):
      1. Stability: lifecycle_stage == 'stable'
      2. Scale: event_count > median(all_clusters) × 1.5
      3. Insight density: cluster has anti_patterns or strategies
      4. Uniqueness: feature signature is distinct from other clusters

      Satisfy ≥3 → module candidate
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def detect_emerging_modules(self) -> List[DomainModule]:
        """Scan all stable clusters and return module candidates."""
        candidates: List[DomainModule] = []

        try:
            clusters = self._load_stable_clusters()
            if not clusters:
                return candidates

            # Compute median event count
            counts = sorted(c["event_count"] for c in clusters)
            median_count = counts[len(counts) // 2] if counts else 0

            # Compute feature uniqueness
            signatures = [c.get("feature_signature", "") for c in clusters]

            for cluster in clusters:
                conditions_met = 0

                # Condition 1: Stability (already filtered to stable)
                conditions_met += 1

                # Condition 2: Scale
                if cluster["event_count"] >= median_count * 1.5 and cluster["event_count"] >= 10:
                    conditions_met += 1

                # Condition 3: Insight density (or sufficient activity as proxy)
                has_insights = self._cluster_has_insights(cluster["id"])
                if has_insights or cluster["event_count"] >= 20:
                    conditions_met += 1

                # Condition 4: Uniqueness
                sig = cluster.get("feature_signature", "")
                is_unique = self._is_unique_signature(sig, signatures)
                if is_unique:
                    conditions_met += 1

                if conditions_met >= 3:
                    module = self._build_module(cluster)
                    candidates.append(module)

        except Exception:
            logger.debug("Module detection failed", exc_info=True)

        return candidates

    def _load_stable_clusters(self) -> List[Dict[str, Any]]:
        """Load all stable clusters."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            from agent.emergent_clusterer import ensure_cluster_tables
            ensure_cluster_tables(conn)

            cursor.execute(
                """SELECT * FROM clusters
                   WHERE is_active = 1 AND lifecycle_stage = 'stable'
                   ORDER BY event_count DESC"""
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _cluster_has_insights(self, cluster_id: int) -> bool:
        """Check if cluster has any associated insights (anti_patterns/strategies)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Check if insights table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='insights'"
            )
            if cursor.fetchone():
                cursor.execute(
                    "SELECT COUNT(*) FROM insights WHERE cluster_id = ? AND is_active = 1",
                    (cluster_id,)
                )
                count = cursor.fetchone()[0]
                conn.close()
                return count > 0

            conn.close()
            return False
        except Exception:
            return False

    def _is_unique_signature(self, sig: str, all_sigs: List[str]) -> bool:
        """Check if a feature signature is distinct from others (Jaccard > 0.3)."""
        if not sig:
            return False

        sig_set = set(sig.split("|"))
        for other in all_sigs:
            if other == sig:
                continue
            other_set = set(other.split("|"))
            if not sig_set or not other_set:
                continue
            intersection = sig_set & other_set
            union = sig_set | other_set
            similarity = len(intersection) / len(union) if union else 0
            if similarity > 0.7:
                return False  # Too similar to another cluster
        return True

    def _build_module(self, cluster: Dict[str, Any]) -> DomainModule:
        """Build a DomainModule from a cluster dict."""
        success_rate = (
            cluster["success_count"] / cluster["event_count"]
            if cluster["event_count"] > 0 else 0.0
        )

        # Generate description from cluster name and stats
        name = cluster.get("name", "unknown")
        description = f"从用户行为中涌现 — {name}"

        # Build insights summary
        insights = self._get_cluster_insights(cluster["id"])
        summary_parts = []
        if insights.get("anti_patterns"):
            summary_parts.append(f"反模式: {len(insights['anti_patterns'])} 条")
        if insights.get("strategies"):
            summary_parts.append(f"策略: {len(insights['strategies'])} 条")
        insights_summary = " | ".join(summary_parts) if summary_parts else ""

        return DomainModule(
            id=0,  # Will be assigned by DB
            cluster_id=cluster["id"],
            name=name,
            description=description,
            event_count=cluster["event_count"],
            success_rate=success_rate,
            is_active=True,
            activated_at=datetime.now().isoformat(),
            insights_summary=insights_summary,
            metadata={
                "feature_signature": cluster.get("feature_signature", ""),
                "first_seen": cluster.get("first_seen", ""),
                "lifecycle_stage": cluster.get("lifecycle_stage", "stable"),
            }
        )

    def _get_cluster_insights(self, cluster_id: int) -> Dict[str, List[str]]:
        """Get insight summaries for a cluster."""
        result = {"anti_patterns": [], "strategies": []}
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='insights'"
            )
            if cursor.fetchone():
                cursor.execute(
                    "SELECT kind, description FROM insights WHERE cluster_id = ? AND is_active = 1",
                    (cluster_id,)
                )
                for kind, desc in cursor.fetchall():
                    if kind == "anti_pattern":
                        result["anti_patterns"].append(desc)
                    elif kind == "strategy":
                        result["strategies"].append(desc)

            conn.close()
        except Exception as e:
            logger.debug("domain_modules.py:  get cluster insights failed: %s", e)
        return result


# ── Module Manager ───────────────────────────────────────────────────────────

MODULES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS domain_modules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id      INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    event_count     INTEGER DEFAULT 0,
    success_rate    REAL DEFAULT 0.0,
    is_active       INTEGER DEFAULT 1,
    activated_at    TEXT DEFAULT '',
    deactivated_at  TEXT DEFAULT '',
    insights_summary TEXT DEFAULT '',
    metadata        TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (cluster_id) REFERENCES clusters(id)
);
CREATE INDEX IF NOT EXISTS idx_modules_active ON domain_modules(is_active);
CREATE INDEX IF NOT EXISTS idx_modules_cluster ON domain_modules(cluster_id);
"""


class DomainModuleManager:
    """Manages the lifecycle of domain modules (activate/deactivate/list)."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def ensure_tables(self) -> None:
        """Create modules table if it doesn't exist."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript(MODULES_TABLE_SQL)
        conn.commit()
        conn.close()

    def scan_and_create_modules(self) -> List[DomainModule]:
        """Scan for new module candidates and create them.

        Returns newly created modules.
        """
        self.ensure_tables()
        detector = ModuleEmergenceDetector(self.db_path)
        candidates = detector.detect_emerging_modules()

        # Filter out already-existing modules
        existing = self._load_existing_module_cluster_ids()
        new_modules: List[DomainModule] = []

        for candidate in candidates:
            if candidate.cluster_id in existing:
                # Update existing module stats
                self._update_module(candidate)
            else:
                # Create new module
                module_id = self._insert_module(candidate)
                candidate.id = module_id
                new_modules.append(candidate)
                logger.info("New domain module emerged: %s (cluster %d)",
                            candidate.name, candidate.cluster_id)

        return new_modules

    def list_modules(self, active_only: bool = False) -> List[DomainModule]:
        """List all domain modules."""
        self.ensure_tables()
        return self._load_modules(active_only)

    def activate_module(self, module_id: int) -> bool:
        """Activate a module."""
        return self._set_module_active(module_id, True)

    def deactivate_module(self, module_id: int) -> bool:
        """Deactivate a module."""
        return self._set_module_active(module_id, False)

    def get_active_prompt_blocks(self) -> str:
        """Get prompt blocks for all active modules.

        Returns a combined string for system prompt injection.
        """
        modules = self.list_modules(active_only=True)
        if not modules:
            return ""

        blocks = []
        for m in modules:
            blocks.append(m.to_prompt_block())

        header = "<domain_modules>"
        footer = "</domain_modules>"
        return "\n".join([header] + blocks + [footer])

    # ── DB Operations ───────────────────────────────────────────────────────

    def _load_modules(self, active_only: bool = False) -> List[DomainModule]:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if active_only:
                cursor.execute(
                    "SELECT * FROM domain_modules WHERE is_active = 1 ORDER BY event_count DESC"
                )
            else:
                cursor.execute(
                    "SELECT * FROM domain_modules ORDER BY event_count DESC"
                )
            rows = cursor.fetchall()
            conn.close()

            return [self._row_to_module(r) for r in rows]
        except Exception:
            return []

    def _load_existing_module_cluster_ids(self) -> Set[int]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT cluster_id FROM domain_modules")
            ids = {row[0] for row in cursor.fetchall()}
            conn.close()
            return ids
        except Exception:
            return set()

    def _insert_module(self, module: DomainModule) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO domain_modules
               (cluster_id, name, description, event_count, success_rate,
                is_active, activated_at, insights_summary, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                module.cluster_id,
                module.name,
                module.description,
                module.event_count,
                module.success_rate,
                1 if module.is_active else 0,
                module.activated_at,
                module.insights_summary,
                json.dumps(module.metadata),
            )
        )
        conn.commit()
        module_id = cursor.lastrowid
        conn.close()
        return module_id

    def _update_module(self, module: DomainModule) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """UPDATE domain_modules SET
               name = ?, event_count = ?, success_rate = ?,
               insights_summary = ?, metadata = ?,
               updated_at = datetime('now')
               WHERE cluster_id = ?""",
            (
                module.name,
                module.event_count,
                module.success_rate,
                module.insights_summary,
                json.dumps(module.metadata),
                module.cluster_id,
            )
        )
        conn.commit()
        conn.close()

    def _set_module_active(self, module_id: int, active: bool) -> bool:
        try:
            conn = sqlite3.connect(self.db_path)
            now = datetime.now().isoformat()
            if active:
                conn.execute(
                    "UPDATE domain_modules SET is_active = 1, activated_at = ? WHERE id = ?",
                    (now, module_id)
                )
            else:
                conn.execute(
                    "UPDATE domain_modules SET is_active = 0, deactivated_at = ? WHERE id = ?",
                    (now, module_id)
                )
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False

    def _row_to_module(self, row: sqlite3.Row) -> DomainModule:
        return DomainModule(
            id=row["id"],
            cluster_id=row["cluster_id"],
            name=row["name"],
            description=row["description"] or "",
            event_count=row["event_count"] or 0,
            success_rate=row["success_rate"] or 0.0,
            is_active=bool(row["is_active"]),
            activated_at=row["activated_at"] or "",
            insights_summary=row["insights_summary"] or "",
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )


# ── Convenience Functions ────────────────────────────────────────────────────

def scan_modules(db_path: str) -> List[DomainModule]:
    """Scan for new domain modules and return newly created ones."""
    manager = DomainModuleManager(db_path)
    return manager.scan_and_create_modules()


def get_active_modules_prompt(db_path: str) -> str:
    """Get active module prompt blocks for system prompt injection."""
    manager = DomainModuleManager(db_path)
    return manager.get_active_prompt_blocks()


def list_all_modules(db_path: str, active_only: bool = False) -> List[DomainModule]:
    """List all domain modules."""
    manager = DomainModuleManager(db_path)
    return manager.list_modules(active_only)
