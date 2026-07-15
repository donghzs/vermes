"""
agent/skill_extractor.py — 涌现式技能提取

从稳定的重复模式簇中自动提取"技能"——可复用的操作序列。

与 Hermes 底层的手动创建技能不同，这里是：
  系统观察用户反复做什么 → 提取成技能 → 用户确认 → 入库

不是"用户写一个 skill 定义"，而是"系统从用户行为中提炼技能"。

技能提取流程：
  1. 找到高重复性簇（event_count 高、tool 多样性低）
  2. 提取操作序列模式（哪些工具按什么顺序使用）
  3. 生成技能描述（从 args_preview 中提炼）
  4. 存入 skills 表（状态=pending，等用户确认）
  5. 用户确认后 → 状态=active → 注入 system prompt

用户确认是关键——系统不擅自把行为变成技能，
只是提议，用户决定是否采纳。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vermes.skill_extractor")


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class ExtractedSkill:
    """A skill extracted from user behavior patterns."""
    cluster_id: int                        # required
    name: str                              # required
    description: str                       # required
    id: int = 0
    tool_sequence: List[str] = field(default_factory=list)
    usage_count: int = 0
    success_rate: float = 0.0
    status: str = "pending"     # pending | active | rejected | stale
    extracted_at: str = ""
    confirmed_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_prompt_line(self) -> str:
        """Render as a prompt line for system prompt injection."""
        icon = "💡" if self.status == "active" else "🔍"
        tools = " → ".join(self.tool_sequence[:5])
        return f"  {icon} {self.name}: {tools} ({self.usage_count}x, {self.success_rate:.0%})"


# ── Table Schema ─────────────────────────────────────────────────────────────

SKILLS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS extracted_skills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id      INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    tool_sequence   TEXT DEFAULT '[]',
    usage_count     INTEGER DEFAULT 0,
    success_rate    REAL DEFAULT 0.0,
    status          TEXT DEFAULT 'pending',
    extracted_at    TEXT DEFAULT '',
    confirmed_at    TEXT DEFAULT '',
    metadata        TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (cluster_id) REFERENCES clusters(id)
);
CREATE INDEX IF NOT EXISTS idx_skills_status ON extracted_skills(status);
CREATE INDEX IF NOT EXISTS idx_skills_cluster ON extracted_skills(cluster_id);
"""


def ensure_skill_tables(conn: sqlite3.Connection) -> None:
    """Create skills tables if they don't exist."""
    conn.executescript(SKILLS_TABLE_SQL)
    conn.commit()


# ── Skill Extractor ─────────────────────────────────────────────────────────

class SkillExtractor:
    """Extract skills from repetitive cluster patterns.

    A skill is a repeated tool sequence that the user performs frequently.
    The extractor identifies these patterns and proposes them as skills.
    User confirmation is required to activate a skill.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def extract(self) -> List[ExtractedSkill]:
        """Run skill extraction on all qualifying clusters.

        Returns newly extracted skills (status=pending).
        """
        new_skills: List[ExtractedSkill] = []

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            ensure_skill_tables(conn)

            # Find clusters ripe for skill extraction
            candidates = self._find_skill_candidates(conn)
            existing_cluster_ids = self._get_existing_skill_clusters(conn)
            for cluster in candidates:
                if cluster["id"] in existing_cluster_ids:
                    # Already extracted — update stats
                    self._update_skill_stats(conn, cluster)
                    continue

                # Extract skill from cluster's events
                skill = self._extract_skill_from_cluster(conn, cluster)
                if skill:
                    self._insert_skill(conn, skill)
                    new_skills.append(skill)
                    logger.info("Skill extracted: %s (cluster %d, %d uses)",
                               skill.name, skill.cluster_id, skill.usage_count)

            conn.close()
        except Exception:
            logger.debug("skill extraction failed", exc_info=True)

        return new_skills

    def _find_skill_candidates(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        """Find clusters that qualify for skill extraction.

        Qualification (all relative, no presets):
          1. Stable lifecycle
          2. Event count ≥ 15 (enough repetitions to form a pattern)
          3. Tool diversity ≤ median (repetitive, not exploratory)
        """
        try:
            cursor = conn.execute(
                """SELECT id, name, event_count, tool_names, lifecycle_stage,
                          success_count, feature_signature
                   FROM clusters
                   WHERE lifecycle_stage = 'stable' AND event_count >= 15
                   ORDER BY event_count DESC"""
            )
            clusters = [dict(r) for r in cursor.fetchall()]

            if len(clusters) < 1:
                return []

            # Compute tool diversity
            for c in clusters:
                tools = set((c.get("tool_names") or "").split("|"))
                tools.discard("")
                c["_tool_diversity"] = len(tools)

            # Median diversity
            diversities = sorted(c["_tool_diversity"] for c in clusters)
            median = diversities[len(diversities) // 2] if diversities else 0

            # Candidates: low diversity (repetitive) + high count
            candidates = [
                c for c in clusters
                if c["_tool_diversity"] <= max(2, median)
            ]

            return candidates
        except Exception:
            return []

    def _extract_skill_from_cluster(
        self, conn: sqlite3.Connection, cluster: Dict[str, Any],
    ) -> Optional[ExtractedSkill]:
        """Extract a skill definition from a cluster's events.

        Looks at the tool sequence in the cluster's raw_events to
        identify the repeating pattern.
        """
        try:
            # Load events for this cluster, ordered by timestamp
            cursor = conn.execute(
                """SELECT tool_name, args_preview, success
                   FROM raw_events
                   WHERE cluster_id = ?
                   ORDER BY timestamp ASC""",
                (cluster["id"],)
            )
            events = cursor.fetchall()

            if len(events) < 5:
                return None

            # Extract tool sequence pattern
            tool_seq = [e["tool_name"] for e in events]

            # Find the most common subsequence (simplified: most common tools in order)
            tool_counts = Counter(tool_seq)
            top_tools = [t for t, _ in tool_counts.most_common(5)]

            # Generate name from cluster name or top tools
            cluster_name = cluster.get("name", "")
            if cluster_name and cluster_name != "unnamed":
                skill_name = cluster_name
            else:
                skill_name = "_".join(top_tools[:3])

            # Generate description from args
            sample_args = [e["args_preview"] or "" for e in events[:5]]
            description = self._generate_description(skill_name, top_tools, sample_args)

            # Compute success rate
            successes = sum(1 for e in events if e["success"])
            success_rate = successes / len(events) if events else 0.0

            return ExtractedSkill(
                cluster_id=cluster["id"],
                name=skill_name,
                description=description,
                tool_sequence=top_tools,
                usage_count=len(events),
                success_rate=success_rate,
                status="pending",
                extracted_at=datetime.now().isoformat(),
                metadata={
                    "cluster_name": cluster_name,
                    "feature_signature": cluster.get("feature_signature", ""),
                },
            )
        except Exception:
            logger.debug("skill extraction from cluster failed", exc_info=True)
            return None

    def _generate_description(
        self, name: str, tools: List[str], sample_args: List[str],
    ) -> str:
        """Generate a human-readable description for the skill.

        Uses the tool names and sample arguments to create a concise
        description of what this skill does.
        """
        tool_str = " → ".join(tools[:4])
        # Try to extract a common pattern from args
        common_words = set()
        for args in sample_args:
            if args:
                # Simple word extraction
                words = args.replace("{", " ").replace("}", " ").replace(",", " ").split()
                common_words.update(w.strip("\"'") for w in words if len(w) > 3)

        keyword_hint = ""
        if common_words:
            # Pick a few representative words
            sorted_words = sorted(common_words, key=len, reverse=True)[:3]
            keyword_hint = f" (involves: {', '.join(sorted_words)})"

        return f"从重复操作中涌现 — {tool_str}{keyword_hint}"

    def _get_existing_skill_clusters(self, conn: sqlite3.Connection) -> set:
        """Get set of cluster_ids that already have extracted skills."""
        try:
            cursor = conn.execute("SELECT cluster_id FROM extracted_skills")
            return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()

    def _insert_skill(self, conn: sqlite3.Connection, skill: ExtractedSkill) -> int:
        """Insert a new skill into the database."""
        cursor = conn.execute(
            """INSERT INTO extracted_skills
               (cluster_id, name, description, tool_sequence, usage_count,
                success_rate, status, extracted_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                skill.cluster_id,
                skill.name,
                skill.description,
                json.dumps(skill.tool_sequence),
                skill.usage_count,
                skill.success_rate,
                skill.status,
                skill.extracted_at,
                json.dumps(skill.metadata),
            ),
        )
        conn.commit()
        skill.id = cursor.lastrowid
        return skill.id

    def _update_skill_stats(
        self, conn: sqlite3.Connection, cluster: Dict[str, Any],
    ) -> None:
        """Update stats for an existing skill."""
        try:
            conn.execute(
                """UPDATE extracted_skills SET
                   usage_count = ?, success_rate = ?,
                   updated_at = datetime('now')
                   WHERE cluster_id = ?""",
                (cluster["event_count"], 
                 cluster.get("success_count", 0) / max(1, cluster["event_count"]),
                 cluster["id"]),
            )
            conn.commit()
        except Exception:
            pass

    # ── Skill Management ────────────────────────────────────────────────────

    def list_skills(self, status: Optional[str] = None) -> List[ExtractedSkill]:
        """List skills, optionally filtered by status."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            ensure_skill_tables(conn)

            if status:
                cursor = conn.execute(
                    "SELECT * FROM extracted_skills WHERE status = ? ORDER BY usage_count DESC",
                    (status,)
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM extracted_skills ORDER BY usage_count DESC"
                )

            skills = [self._row_to_skill(r) for r in cursor.fetchall()]
            conn.close()
            return skills
        except Exception:
            return []

    def confirm_skill(self, skill_id: int) -> bool:
        """User confirms a pending skill → activate it."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                """UPDATE extracted_skills SET
                   status = 'active', confirmed_at = ?,
                   updated_at = datetime('now')
                   WHERE id = ? AND status = 'pending'""",
                (datetime.now().isoformat(), skill_id),
            )
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False

    def reject_skill(self, skill_id: int) -> bool:
        """User rejects a pending skill.

        Records a raw_event so the system learns the user declined
        this pattern — prevents re-proposing the same skill.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                "UPDATE extracted_skills SET status = 'rejected', updated_at = datetime('now') WHERE id = ?",
                (skill_id,)
            )
            conn.commit()
            conn.close()

            # Record rejection as raw_event for future reference
            try:
                from agent.raw_event import record_raw_event
                record_raw_event(
                    tool_name="skill_extractor",
                    tool_args={"action": "reject", "skill_id": skill_id},
                    result="user_declined",
                    is_error=False,
                    duration=0.0,
                )
            except Exception:
                pass

            return True
        except Exception:
            return False

    def get_active_skills_prompt(self) -> str:
        """Get active skills as a prompt block for system prompt injection."""
        skills = self.list_skills(status="active")
        if not skills:
            return ""

        lines = ["<extracted_skills>"]
        for s in skills:
            lines.append(s.to_prompt_line())
        lines.append("</extracted_skills>")
        return "\n".join(lines)

    def get_pending_skills_prompt(self) -> str:
        """Get pending skills for user confirmation.

        This is surfaced to the Agent so it can ask the user:
        'I noticed you keep doing X, want me to save it as a skill?'

        The Agent should:
        1. Naturally mention the observed pattern in conversation
        2. Ask if the user wants to save it as a skill
        3. On user 'yes' → call confirm_skill(db_path, skill_id)
        4. On user 'no' → call reject_skill(db_path, skill_id)
        5. Only propose ONCE per skill — don't repeat if user declined
        """
        skills = self.list_skills(status="pending")
        if not skills:
            return ""

        lines = ["<pending_skills>"]
        lines.append("从用户行为中涌现的技能候选，等待用户确认。")
        lines.append("")
        lines.append("行为规则：")
        lines.append("- 在回复中自然地提及观察到的模式（不要生硬地列出）")
        lines.append("- 询问用户是否要保存为技能")
        lines.append("- 用户确认 → 调用 confirm_skill(db_path, skill_id)")
        lines.append("- 用户拒绝 → 调用 reject_skill(db_path, skill_id)")
        lines.append("- 每个技能只提议一次，不要重复提议")
        lines.append("")
        lines.append("候选技能：")
        for s in skills:
            lines.append(f"  🔍 [{s.id}] {s.name}: {s.description} ({s.usage_count}x)")
        lines.append("</pending_skills>")
        return "\n".join(lines)

    def _row_to_skill(self, row: sqlite3.Row) -> ExtractedSkill:
        return ExtractedSkill(
            id=row["id"],
            cluster_id=row["cluster_id"],
            name=row["name"],
            description=row["description"] or "",
            tool_sequence=json.loads(row["tool_sequence"]) if row["tool_sequence"] else [],
            usage_count=row["usage_count"] or 0,
            success_rate=row["success_rate"] or 0.0,
            status=row["status"] or "pending",
            extracted_at=row["extracted_at"] or "",
            confirmed_at=row["confirmed_at"] or "",
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )


# ── Convenience Functions ────────────────────────────────────────────────────

def extract_skills(db_path: str) -> List[ExtractedSkill]:
    """Run skill extraction and return newly found skills."""
    extractor = SkillExtractor(db_path)
    return extractor.extract()


def get_active_skills_prompt(db_path: str) -> str:
    """Get active skills for system prompt injection."""
    extractor = SkillExtractor(db_path)
    return extractor.get_active_skills_prompt()


def get_pending_skills_prompt(db_path: str) -> str:
    """Get pending skills for user confirmation."""
    extractor = SkillExtractor(db_path)
    return extractor.get_pending_skills_prompt()


def confirm_skill(db_path: str, skill_id: int) -> bool:
    """User confirms a skill."""
    extractor = SkillExtractor(db_path)
    return extractor.confirm_skill(skill_id)


def reject_skill(db_path: str, skill_id: int) -> bool:
    """User rejects a skill."""
    extractor = SkillExtractor(db_path)
    return extractor.reject_skill(skill_id)
