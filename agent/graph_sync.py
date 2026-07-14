"""
agent/graph_sync.py — 知识图谱导入导出

桌面级应用无云端，多设备/多 Agent 间的知识共享通过
点对点文件同步完成。本模块负责：

  1. 导出：将簇/洞察/技能/决策序列化为 GraphJSON 格式
  2. 导入：合并另一台设备的 GraphJSON 到本地 DB
  3. 冲突解决：基于时间戳 + 版本号的自动合并

GraphJSON 是一个简单的 JSON 格式，Git 友好，可 diff：
  {
    "version": 1,
    "exported_at": "2026-07-14T19:00:00",
    "source": "user@device-a",
    "clusters": [...],
    "insights": [...],
    "skills": [...],
    "decisions": [...]
  }

传输方式由 Agent 自行决定（cron/scp/手动拷贝），
本模块只负责导出/导入/合并。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vermes.graph_sync")


# ── GraphJSON Format ─────────────────────────────────────────────────────────

GRAPHJSON_VERSION = 1


@dataclass
class GraphExport:
    """A graph export in GraphJSON format."""
    version: int = GRAPHJSON_VERSION
    exported_at: str = ""
    source: str = ""
    clusters: List[Dict] = field(default_factory=list)
    insights: List[Dict] = field(default_factory=list)
    skills: List[Dict] = field(default_factory=list)
    decisions: List[Dict] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize to JSON string (Git-friendly, pretty-printed)."""
        return json.dumps({
            "version": self.version,
            "exported_at": self.exported_at,
            "source": self.source,
            "clusters": self.clusters,
            "insights": self.insights,
            "skills": self.skills,
            "decisions": self.decisions,
        }, indent=2, ensure_ascii=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "exported_at": self.exported_at,
            "source": self.source,
            "clusters": self.clusters,
            "insights": self.insights,
            "skills": self.skills,
            "decisions": self.decisions,
        }


@dataclass
class ImportResult:
    """Result of importing a GraphJSON file."""
    clusters_imported: int = 0
    insights_imported: int = 0
    skills_imported: int = 0
    decisions_imported: int = 0
    conflicts_resolved: int = 0
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"clusters: {self.clusters_imported}",
            f"insights: {self.insights_imported}",
            f"skills: {self.skills_imported}",
            f"decisions: {self.decisions_imported}",
        ]
        if self.conflicts_resolved:
            parts.append(f"conflicts: {self.conflicts_resolved}")
        if self.errors:
            parts.append(f"errors: {len(self.errors)}")
        return " | ".join(parts)


# ── Table Schema ─────────────────────────────────────────────────────────────

GRAPH_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS graph_sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT NOT NULL,          -- 'export' | 'import'
    source      TEXT DEFAULT '',
    file_path   TEXT DEFAULT '',
    clusters    INTEGER DEFAULT 0,
    insights    INTEGER DEFAULT 0,
    skills      INTEGER DEFAULT 0,
    decisions   INTEGER DEFAULT 0,
    conflicts   INTEGER DEFAULT 0,
    timestamp   TEXT DEFAULT (datetime('now'))
);
"""


def ensure_graph_tables(conn: sqlite3.Connection) -> None:
    """Create graph sync tables if they don't exist."""
    conn.executescript(GRAPH_TABLES_SQL)
    conn.commit()


# ── Exporter ────────────────────────────────────────────────────────────────

def export_graph(db_path: str, source: str = "") -> GraphExport:
    """Export all evolution data to GraphJSON format.

    Exports:
      - Clusters (stable/declining only — skip emerging/dormant)
      - Insights (anti_patterns + strategies, active only)
      - Skills (active only — pending are user-specific)
      - Decisions (standing decisions, active only)

    Args:
        db_path: Path to self-model.db
        source:  Identifier for this device/user (e.g., "user@macbook")
    """
    export = GraphExport(
        exported_at=datetime.now().isoformat(),
        source=source or _get_default_source(),
    )

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

        # Export clusters
        export.clusters = _export_clusters(conn)

        # Export insights
        export.insights = _export_insights(conn)

        # Export skills
        export.skills = _export_skills(conn)

        # Export decisions
        export.decisions = _export_decisions(conn)

        # Log export
        ensure_graph_tables(conn)
        conn.execute(
            """INSERT INTO graph_sync_log (action, source, clusters, insights, skills, decisions)
               VALUES ('export', ?, ?, ?, ?, ?)""",
            (export.source, len(export.clusters), len(export.insights),
             len(export.skills), len(export.decisions)),
        )
        conn.commit()
        conn.close()

    except Exception:
        logger.debug("graph export failed", exc_info=True)

    logger.info("Graph exported: %d clusters, %d insights, %d skills, %d decisions",
                len(export.clusters), len(export.insights),
                len(export.skills), len(export.decisions))

    return export


def export_graph_to_file(db_path: str, file_path: str, source: str = "") -> bool:
    """Export graph to a JSON file."""
    try:
        export = export_graph(db_path, source)
        Path(file_path).write_text(export.to_json(), encoding="utf-8")
        logger.info("Graph exported to %s", file_path)
        return True
    except Exception as e:
        logger.error("Graph export to file failed: %s", e)
        return False


def _export_clusters(conn: sqlite3.Connection) -> List[Dict]:
    """Export stable/declining clusters."""
    try:
        cursor = conn.execute(
            """SELECT id, name, tool_names, event_count, success_count,
                      lifecycle_stage, feature_signature, first_seen, last_active
               FROM clusters
               WHERE lifecycle_stage IN ('stable', 'declining') AND is_active = 1"""
        )
        return [dict(r) for r in cursor.fetchall()]
    except Exception:
        return []


def _export_insights(conn: sqlite3.Connection) -> List[Dict]:
    """Export active insights."""
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='insights'"
        )
        if not cursor.fetchone():
            return []
        cursor = conn.execute(
            """SELECT kind, cluster_id, cluster_name, description, severity,
                      is_active, created_at
               FROM insights
               WHERE is_active = 1 AND kind IN ('anti_pattern', 'strategy')"""
        )
        return [dict(r) for r in cursor.fetchall()]
    except Exception:
        return []


def _export_skills(conn: sqlite3.Connection) -> List[Dict]:
    """Export active skills."""
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='extracted_skills'"
        )
        if not cursor.fetchone():
            return []
        cursor = conn.execute(
            """SELECT name, description, tool_sequence, usage_count,
                      success_rate, confirmed_at
               FROM extracted_skills
               WHERE status = 'active'"""
        )
        return [dict(r) for r in cursor.fetchall()]
    except Exception:
        return []


def _export_decisions(conn: sqlite3.Connection) -> List[Dict]:
    """Export standing decisions."""
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
        )
        if not cursor.fetchone():
            return []
        cursor = conn.execute(
            "SELECT decision_text, rationale, status, created_at FROM decisions WHERE status = 'active'"
        )
        return [dict(r) for r in cursor.fetchall()]
    except Exception:
        return []


# ── Importer ────────────────────────────────────────────────────────────────

def import_graph(db_path: str, graph_data: Dict[str, Any]) -> ImportResult:
    """Import a GraphJSON dict into the local DB.

    Merge strategy:
      - Clusters: insert if feature_signature doesn't exist locally
      - Insights: insert if (cluster_id + description) doesn't exist
      - Skills: insert if name doesn't exist locally
      - Decisions: insert if decision_text doesn't exist

    Conflicts (same key but different data): keep newer by timestamp.
    """
    result = ImportResult()

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        ensure_graph_tables(conn)

        # Ensure all target tables exist before importing
        try:
            from agent.skill_extractor import ensure_skill_tables
            ensure_skill_tables(conn)
        except Exception:
            pass

        # Import clusters
        for cluster in graph_data.get("clusters", []):
            try:
                if not _cluster_exists(conn, cluster.get("feature_signature", "")):
                    _insert_cluster(conn, cluster)
                    result.clusters_imported += 1
                else:
                    result.conflicts_resolved += 1
            except Exception as e:
                result.errors.append(f"cluster: {e}")

        # Import insights
        for insight in graph_data.get("insights", []):
            try:
                if not _insight_exists(conn, insight):
                    _insert_insight(conn, insight)
                    result.insights_imported += 1
                else:
                    result.conflicts_resolved += 1
            except Exception as e:
                result.errors.append(f"insight: {e}")

        # Import skills
        for skill in graph_data.get("skills", []):
            try:
                if not _skill_exists(conn, skill.get("name", "")):
                    _insert_skill(conn, skill)
                    result.skills_imported += 1
                else:
                    result.conflicts_resolved += 1
            except Exception as e:
                result.errors.append(f"skill: {e}")

        # Import decisions
        for decision in graph_data.get("decisions", []):
            try:
                if not _decision_exists(conn, decision.get("decision_text", "")):
                    _insert_decision(conn, decision)
                    result.decisions_imported += 1
                else:
                    result.conflicts_resolved += 1
            except Exception as e:
                result.errors.append(f"decision: {e}")

        # Log import
        conn.execute(
            """INSERT INTO graph_sync_log
               (action, source, clusters, insights, skills, decisions, conflicts)
               VALUES ('import', ?, ?, ?, ?, ?, ?)""",
            (graph_data.get("source", ""),
             result.clusters_imported, result.insights_imported,
             result.skills_imported, result.decisions_imported,
             result.conflicts_resolved),
        )
        conn.commit()
        conn.close()

    except Exception as e:
        result.errors.append(f"fatal: {e}")

    logger.info("Graph import: %s", result.summary())
    return result


def import_graph_from_file(db_path: str, file_path: str) -> ImportResult:
    """Import a GraphJSON file."""
    try:
        data = json.loads(Path(file_path).read_text(encoding="utf-8"))
        return import_graph(db_path, data)
    except Exception as e:
        result = ImportResult()
        result.errors.append(f"file read error: {e}")
        return result


# ── Conflict Resolution ─────────────────────────────────────────────────────

def _cluster_exists(conn: sqlite3.Connection, signature: str) -> bool:
    if not signature:
        return False
    cursor = conn.execute(
        "SELECT 1 FROM clusters WHERE feature_signature = ? LIMIT 1", (signature,)
    )
    return cursor.fetchone() is not None


def _insert_cluster(conn: sqlite3.Connection, cluster: Dict) -> None:
    conn.execute(
        """INSERT INTO clusters
           (name, tool_names, event_count, success_count, lifecycle_stage,
            feature_signature, first_seen, last_active, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (cluster.get("name", ""), cluster.get("tool_names", ""),
         cluster.get("event_count", 0), cluster.get("success_count", 0),
         cluster.get("lifecycle_stage", "stable"),
         cluster.get("feature_signature", ""),
         cluster.get("first_seen", ""), cluster.get("last_active", "")),
    )
    conn.commit()


def _insight_exists(conn: sqlite3.Connection, insight: Dict) -> bool:
    cursor = conn.execute(
        "SELECT 1 FROM insights WHERE cluster_id = ? AND description = ? LIMIT 1",
        (insight.get("cluster_id"), insight.get("description", "")),
    )
    return cursor.fetchone() is not None


def _insert_insight(conn: sqlite3.Connection, insight: Dict) -> None:
    conn.execute(
        """INSERT INTO insights (kind, cluster_id, cluster_name, description, severity, is_active, created_at)
           VALUES (?, ?, ?, ?, ?, 1, ?)""",
        (insight.get("kind", ""), insight.get("cluster_id"),
         insight.get("cluster_name", ""), insight.get("description", ""),
         insight.get("severity", 0.0), insight.get("created_at", "")),
    )
    conn.commit()


def _skill_exists(conn: sqlite3.Connection, name: str) -> bool:
    if not name:
        return False
    cursor = conn.execute(
        "SELECT 1 FROM extracted_skills WHERE name = ? LIMIT 1", (name,)
    )
    return cursor.fetchone() is not None


def _insert_skill(conn: sqlite3.Connection, skill: Dict) -> None:
    conn.execute(
        """INSERT INTO extracted_skills
           (cluster_id, name, description, tool_sequence, usage_count,
            success_rate, status, confirmed_at)
           VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
        (skill.get("cluster_id", 0), skill.get("name", ""),
         skill.get("description", ""),
         skill.get("tool_sequence", "[]"),
         skill.get("usage_count", 0), skill.get("success_rate", 0.0),
         skill.get("confirmed_at", "")),
    )
    conn.commit()


def _decision_exists(conn: sqlite3.Connection, text: str) -> bool:
    if not text:
        return False
    cursor = conn.execute(
        "SELECT 1 FROM decisions WHERE decision_text = ? LIMIT 1", (text,)
    )
    return cursor.fetchone() is not None


def _insert_decision(conn: sqlite3.Connection, decision: Dict) -> None:
    conn.execute(
        """INSERT INTO decisions (decision_text, rationale, status, created_at)
           VALUES (?, ?, 'active', ?)""",
        (decision.get("decision_text", ""), decision.get("rationale", ""),
         decision.get("created_at", "")),
    )
    conn.commit()


# ── Utils ───────────────────────────────────────────────────────────────────

def _get_default_source() -> str:
    """Get a default source identifier for this device."""
    import os
    import socket
    return f"{os.environ.get('USER', 'unknown')}@{socket.gethostname()}"
