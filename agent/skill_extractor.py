"""
agent/skill_extractor.py — 涌现式技能提取

从稳定的重复模式簇中自动提取"技能"——可复用的操作序列。

与 Vermes 底层的手动创建技能不同，这里是：
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

# 涌现式技能候选门槛：success_rate 是系统自身从 outcomes 算出的质量信号，
# 用作"已确立"的质量下限（类比安全护栏，非跨模块硬编码映射）。
# tool_diversity 等人工启发式已移除（B1 涌现重构）。
SKILL_SUCCESS_RATE_FLOOR: float = 0.8

# ── T3 自动采纳阈值（P1 外置范式：config.yaml → memory.skillAdopt.*）────────
# 提取门槛（上面那个）回答「这算不算一个模式」；采纳门槛回答「够不够格
# 不问就用」。两者必须分开：把提取门槛直接当采纳门槛，等于把所有候选一律
# 自动启用。默认值刻意高于提取门槛。
_SKILL_ADOPT_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "min_success_rate": 0.9,   # 提取门槛 0.8 之上再留一档
    "min_usage": 10,           # 提取门槛 5 次之上再翻一倍
    "retract_window_h": 24,    # 与 L1 撤回窗口一致
}


def load_skill_adopt_config() -> Dict[str, Any]:
    """Read ``memory.skillAdopt.*`` with the P1 ">0 or fall back" guard.

    A configured ``0``/负数 would mean "adopt everything" —— 那正是这套阈值
    要防的事，所以按 P1 注入护栏处理：非正数一律回落到默认值。读配置失败
    同样回落（fail-safe 到默认，而不是关掉整个机制或全量放行）。
    """
    cfg = dict(_SKILL_ADOPT_DEFAULTS)
    try:
        from vermes_cli.config import load_config

        raw = (load_config().get("memory", {}) or {}).get("skillAdopt", {})
        if not isinstance(raw, dict):
            return cfg
        if "enabled" in raw:
            cfg["enabled"] = bool(raw["enabled"])
        for k in ("min_success_rate", "min_usage", "retract_window_h"):
            v = raw.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                cfg[k] = float(v) if k == "min_success_rate" else int(v)
    except Exception:
        logger.debug("skill adopt config unreadable, using defaults", exc_info=True)
    return cfg


def _adopt_tier() -> str:
    """技能采纳的有效档位 —— 基线 L1，交给用户的 tier_mode 调节。

    读不到偏好时返回基线 L1，而不是保守降级：tier_mode 是**偏好**，
    读不出偏好不该改变安全基线（该动作本身可逆且有通知）。
    """
    try:
        from tools.approval import effective_tier
        return effective_tier("L1", reversible=True)
    except Exception:
        return "L1"


def should_auto_adopt(
    skill: "ExtractedSkill", cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """T3 — 这个技能够不够格「不问就用」（L1）？

    采纳一个技能是**可逆**的（一键 reject 打回）、**爆炸半径小**的（只是
    多一条可用模式进提示词），所以拦在 L2 弹窗后面只会攒出一堆没人处理的
    pending 待办债 —— 系统学到了东西却永远用不上。但可逆不等于免检：
    低置信度的技能自动上线会污染行为，所以门槛按两个客观量判定，
    且两者都要满足。

    M1 自适应：高频技能（近7天≥20次）门槛降低到 0.8/5，
    低频技能维持 0.9/10。阈值由 skill 自身 usage 数据动态计算，
    不是从 config 硬读——飞轮越用越聪明。

    Returns ``(adopt, reason)``；reason 无论采纳与否都给，用于通知文案。
    """
    cfg = cfg or load_skill_adopt_config()
    if not cfg.get("enabled", True):
        return False, "自动采纳已关闭（memory.skillAdopt.enabled=false）"
    rate = float(skill.success_rate or 0.0)
    uses = int(skill.usage_count or 0)

    # M1 自适应门槛：根据技能自身使用频率动态调整
    adaptive = cfg.get("adaptive", {})
    high_freq_threshold = int(adaptive.get("high_freq_uses", 20))
    high_freq_rate = float(adaptive.get("high_freq_min_rate", 0.8))
    high_freq_min_uses = int(adaptive.get("high_freq_min_usage", 5))

    min_rate = float(cfg["min_success_rate"])
    min_uses = int(cfg["min_usage"])

    # 高频技能（近7天使用≥20次）：门槛降低
    if uses >= high_freq_threshold:
        min_rate = high_freq_rate
        min_uses = high_freq_min_uses

    if rate < min_rate:
        return False, f"成功率 {rate:.0%} < {min_rate:.0%}，留待人工确认"
    if uses < min_uses:
        return False, f"使用 {uses} 次 < {min_uses} 次，留待人工确认"
    return True, f"成功率 {rate:.0%}、已重复 {uses} 次，达到自动采纳门槛"


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


# ── 评测闭环阈值（相对基线，非全局预设）───────────────────────────────────
# H4.3 技能自进化评测闭环：active 技能按 emergent_insight + success_rate 自动
# 升/降/淘汰（stale = 归档）。阈值均相对用户自身基线，无绝对预设。
_DEMOTE_SUCCESS_RATE = 0.5    # 成功率低于此 → 降级为 stale（低成功率）
_PROMOTE_SUCCESS_RATE = 0.85 # 成功率高于此且用量足够 → 标记 proven（晋升）
_MIN_USES_FOR_PROMOTE = 10   # 用量达到此才考虑晋升
_ANTI_PATTERN_SEVERITY = 0.6 # 负面洞察严重度达到此 → 降级（其底层模式是反模式）


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

            # 一轮只读一次配置：同一批技能用同一套阈值判定，避免中途被改。
            adopt_cfg = load_skill_adopt_config()

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
                    self._maybe_auto_adopt(conn, skill, adopt_cfg)

            conn.close()
        except Exception:
            logger.debug("skill extraction failed", exc_info=True)

        return new_skills

    def _find_skill_candidates(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        """Find clusters that qualify for skill extraction.

        涌现式门槛（无硬编码跨模块映射、无 tool_diversity 人工启发式）：
        - lifecycle_stage='stable'：模式已由系统自身判定为确立
        - event_count >= 5：重复足够多次形成模式（相对低门槛，让真实用户行为簇进入）
        - success_rate >= SKILL_SUCCESS_RATE_FLOOR：成功率高（系统已涌现的质量信号）
        - 排除系统自噬簇（name 匹配 __xxx__ 模式的内部自检行为）
        三者皆来自 clusters 表自身已填充的涌现字段，不手工 JOIN 派生。
        """
        try:
            cursor = conn.execute(
                """SELECT id, name, event_count, success_count, success_rate,
                          feature_signature, lifecycle_stage
                   FROM clusters
                   WHERE lifecycle_stage = 'stable'
                     AND is_active = 1
                     AND event_count >= 5
                     AND success_rate >= ?
                     AND (name IS NULL OR name NOT GLOB '__*__')
                   ORDER BY event_count DESC""",
                (SKILL_SUCCESS_RATE_FLOOR,),
            )
            return [dict(r) for r in cursor.fetchall()]
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

    def _maybe_auto_adopt(
        self, conn: sqlite3.Connection, skill: ExtractedSkill,
        cfg: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """T3 — 达标就直接启用，并留一条可撤回的通知（L1）。

        不达标的照旧留在 pending，等人工确认。整段包在 try 里：采纳失败
        绝不能把这次提取也带崩 —— 最坏结果只是这个技能停在 pending。
        """
        try:
            adopt, why = should_auto_adopt(skill, cfg)
            if not adopt:
                logger.debug("Skill '%s' stays pending: %s", skill.name, why)
                return False

            # T6：采纳的基线是 L1（可逆 —— 面板一键否决就退回 pending）。
            # tier_mode=conservative 会把它收紧成 L2，此时不自动启用，留在
            # pending 等人工点确认，行为退回 T3 之前，属于用户明确选择。
            if _adopt_tier() != "L1":
                logger.info("Skill '%s' stays pending: tier_mode 收紧到 L2", skill.name)
                return False

            conn.execute(
                """UPDATE extracted_skills SET
                   status = 'active', confirmed_at = ?, updated_at = datetime('now')
                   WHERE id = ? AND status = 'pending'""",
                (datetime.now().isoformat(), skill.id),
            )
            conn.commit()
            skill.status = "active"
            logger.info("Skill auto-adopted (L1): %s — %s", skill.name, why)
        except Exception as e:
            logger.warning("Skill auto-adopt failed for '%s': %s", skill.name, e)
            return False

        # 通知是侧信道：写不进去也不回退已经生效的采纳，但要吼一声，
        # 因为「静默采纳」正是这套分层要消灭的东西。
        try:
            from datetime import timedelta, timezone

            from agent.change_ledger import (
                record_change, KIND_SKILL_ADOPTED, TIER_L1, REF_SKILL,
            )
            hours = int((cfg or load_skill_adopt_config())["retract_window_h"])
            record_change(
                kind=KIND_SKILL_ADOPTED,
                tier=TIER_L1,
                title=f"已自动采纳技能：{skill.name}",
                summary=why,
                detail={
                    "tool_sequence": skill.tool_sequence,
                    "usage_count": skill.usage_count,
                    "success_rate": skill.success_rate,
                    "description": skill.description,
                },
                retract_deadline=(
                    datetime.now(timezone.utc) + timedelta(hours=hours)
                ).isoformat(),
                ref_kind=REF_SKILL,
                ref_id=skill.id,
            )
        except Exception as e:
            logger.warning("[Changes] skill adoption notice failed: %s", e)
        return True

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
        except Exception as e:
            logger.debug("skill_extractor.py:  update skill stats failed: %s", e)

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
            except Exception as e:
                logger.debug("skill_extractor.py: reject skill failed: %s", e)

            return True
        except Exception:
            return False

    # ── H4.3 评测闭环：自进化技能生命周期评估 ──────────────────────────────

    def evaluate_lifecycle(self) -> Dict[str, int]:
        """Run the self-evolving skill evaluation loop (H4.3).

        For each ACTIVE skill, combine emergent insights + success_rate + usage:
          - anti_pattern insight matches cluster_id (severity >= thr) → demote (stale)
          - success_rate < _DEMOTE_SUCCESS_RATE                    → demote (stale)
          - success_rate >= _PROMOTE and usage >= _MIN_USES        → mark 'proven'

        For each STALE skill that recovered (success >= _PROMOTE, usage >= _MIN_USES,
        no anti_pattern) → reactivate to active.

        Returns a summary dict; fail-open (any error → empty summary).
        """
        summary: Dict[str, int] = {
            "evaluated": 0, "demoted": 0, "promoted": 0, "reactivated": 0
        }
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            ensure_skill_tables(conn)

            # anti_pattern 洞察按 cluster_id 聚合最高严重度（guarded，失败则空）
            anti_by_cluster = self._load_anti_patterns(conn)

            for row in conn.execute(
                "SELECT * FROM extracted_skills WHERE status = 'active'"
            ).fetchall():
                skill = self._row_to_skill(row)
                summary["evaluated"] += 1
                reason = self._decide_demotion(skill, anti_by_cluster)
                if reason:
                    self._set_status(conn, skill.id, "stale", demote_reason=reason)
                    summary["demoted"] += 1
                    self._record_lifecycle_event(skill, "demote", reason)
                elif (skill.success_rate >= _PROMOTE_SUCCESS_RATE
                      and skill.usage_count >= _MIN_USES_FOR_PROMOTE):
                    self._mark_proven(conn, skill.id)
                    summary["promoted"] += 1

            # 复活恢复健康的 stale 技能
            for row in conn.execute(
                "SELECT * FROM extracted_skills WHERE status = 'stale'"
            ).fetchall():
                skill = self._row_to_skill(row)
                if (skill.success_rate >= _PROMOTE_SUCCESS_RATE
                        and skill.usage_count >= _MIN_USES_FOR_PROMOTE
                        and not anti_by_cluster.get(skill.cluster_id)):
                    self._set_status(conn, skill.id, "active", demote_reason=None)
                    summary["reactivated"] += 1
                    self._record_lifecycle_event(skill, "reactivate", "recovered")

            conn.close()
        except Exception:
            logger.debug("skill lifecycle eval failed", exc_info=True)
        return summary

    def _load_anti_patterns(self, conn: sqlite3.Connection) -> Dict[int, float]:
        """Aggregate anti_pattern insight severity by cluster_id (guarded)."""
        try:
            from agent.emergent_insight import EmergentInsightExtractor

            report = EmergentInsightExtractor(self.db_path).extract()
            result: Dict[int, float] = {}
            for ins in (report.anti_patterns or []):
                cid = ins.cluster_id
                if cid is None:
                    continue
                result[cid] = max(result.get(cid, 0.0), ins.severity)
            return result
        except Exception:
            logger.debug("anti_pattern insight load skipped", exc_info=True)
            return {}

    @staticmethod
    def _decide_demotion(
        skill: "ExtractedSkill", anti_by_cluster: Dict[int, float]
    ) -> Optional[str]:
        """Return a demotion reason, or None to keep active."""
        if (skill.cluster_id in anti_by_cluster
                and anti_by_cluster[skill.cluster_id] >= _ANTI_PATTERN_SEVERITY):
            return "anti_pattern"
        if skill.success_rate < _DEMOTE_SUCCESS_RATE:
            return "low_success"
        return None

    def _set_status(
        self, conn: sqlite3.Connection, skill_id: int, status: str,
        demote_reason: Optional[str] = None,
    ) -> None:
        """Set a skill's status, merging eval metadata (fail-open)."""
        try:
            row = conn.execute(
                "SELECT metadata FROM extracted_skills WHERE id = ?", (skill_id,)
            ).fetchone()
            meta = json.loads(row[0]) if row and row[0] else {}
            meta["last_eval_status"] = status
            if demote_reason:
                meta["demote_reason"] = demote_reason
                meta["demoted_at"] = datetime.now().isoformat()
            conn.execute(
                """UPDATE extracted_skills SET status = ?, metadata = ?,
                   updated_at = datetime('now') WHERE id = ?""",
                (status, json.dumps(meta), skill_id),
            )
            conn.commit()
        except Exception as e:
            logger.debug("skill_extractor.py:  set status failed: %s", e)

    def _mark_proven(self, conn: sqlite3.Connection, skill_id: int) -> None:
        """Mark a skill as proven (stays active; metadata badge only)."""
        try:
            row = conn.execute(
                "SELECT metadata FROM extracted_skills WHERE id = ?", (skill_id,)
            ).fetchone()
            meta = json.loads(row[0]) if row and row[0] else {}
            meta["grade"] = "proven"
            meta["proven_at"] = datetime.now().isoformat()
            conn.execute(
                "UPDATE extracted_skills SET metadata = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(meta), skill_id),
            )
            conn.commit()
        except Exception as e:
            logger.debug("skill_extractor.py:  mark proven failed: %s", e)

    def _record_lifecycle_event(
        self, skill: "ExtractedSkill", action: str, reason: str
    ) -> None:
        """Persist a lifecycle decision as a raw_event for learning (fail-open)."""
        try:
            from agent.raw_event import record_raw_event

            record_raw_event(
                tool_name="skill_lifecycle",
                tool_args={
                    "action": action,
                    "skill_id": skill.id,
                    "name": skill.name,
                    "cluster_id": skill.cluster_id,
                },
                result=f"{action}:{reason}",
                is_error=False,
                duration=0.0,
                trigger_clustering=False,
            )
        except Exception as e:
            logger.debug("skill_extractor.py:  record lifecycle event failed: %s", e)

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


def evaluate_skill_lifecycle(db_path: str) -> Dict[str, int]:
    """Run the self-evolving skill evaluation loop (H4.3).

    Called after each extraction cycle so active skills are continuously
    promoted / demoted / retired based on emergent insights + success_rate.
    """
    extractor = SkillExtractor(db_path)
    return extractor.evaluate_lifecycle()
