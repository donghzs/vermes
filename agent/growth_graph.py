"""agent/growth_graph.py — 学习成长图谱

对标上游 ``learning_graph.py`` 的 "learning made visible" 理念，但数据源换成
Vermes 自有的涌现式自进化体系：

- 技能节点：``extracted_skills`` 表（agent 涌现提取的重复模式技能）
- 记忆节点：``memories`` 表 L1_NOTE layer（MEMORY.md / USER.md 策展笔记）
- 边：
  * 技能 ↔ 技能：``tool_sequence`` 交集 ≥1（替代上游 ``related_skills`` 双向去重）
  * 技能 ↔ 记忆：词法重叠（技能 description 关键词 vs 记忆 fts_content）
- 时间线：``agent_changes`` 表中 ``ref_kind='skill'`` 的变更记录
  （KIND_SKILL_ADOPTED 等），按 created 倒序

与 ``/api/evolution/dag`` 互补：dag 是 ``relations`` 表的边聚合（涌现系统关系图），
growth_graph 是技能 + 记忆节点的学习成长图——给用户看见"越用越懂你"。

Fail-open 原则：任何子模块失败返回空，不影响其他部分；整个图谱构建失败返回空图谱。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vermes.growth_graph")

# 与 agent/memory_fabric.py 一致（避免循环 import，常量复制）
_L1_NOTE_LAYER = "note"
# 与 agent/change_ledger.py 一致
_REF_SKILL = "skill"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class GraphNode:
    id: str                 # "skill:12" | "memory:3"
    kind: str               # "skill" | "memory"
    label: str
    status: str = ""        # skill: pending|active|rejected|stale
    usage_count: int = 0
    success_rate: float = 0.0
    grade: str = ""         # metadata.grade: proven | ""
    tool_sequence: List[str] = field(default_factory=list)
    source: str = ""       # memory: source (USER.md / MEMORY.md / …)
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    source: str
    target: str
    rel: str                # "tool_overlap" | "lexical"
    weight: int = 1


@dataclass
class TimelineEntry:
    ts: str
    action: str             # extracted|auto_adopted|confirmed|rejected|promoted|demoted|reactivated
    skill_id: int
    skill_name: str
    detail: str = ""


@dataclass
class GrowthGraph:
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    timeline: List[TimelineEntry]
    totals: Dict[str, int]


# ── Builder ──────────────────────────────────────────────────────────────────

def build_growth_graph(
    skill_db_path: Optional[str] = None,
    memory_db_path: Optional[str] = None,
) -> GrowthGraph:
    """构建学习成长图谱。

    Args:
        skill_db_path: self-model.db 路径（含 extracted_skills + agent_changes 表）。
                       默认 ``evolution_manager.get_self_model_db()``。
        memory_db_path: memory_index.db 路径（含 memories 表）。
                        默认 ``memory_fabric._get_index_db()``。

    Returns:
        GrowthGraph，任何子模块失败对应部分为空，整体失败返回空图谱。
    """
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []
    timeline: List[TimelineEntry] = []

    try:
        if skill_db_path is None:
            from agent.evolution_manager import get_self_model_db
            skill_db_path = str(get_self_model_db())
    except Exception:
        logger.debug("skill_db_path resolve failed", exc_info=True)

    # 1. 技能节点 + 时间线（同库 self-model.db）
    skill_nodes: List[Tuple[Any, GraphNode]] = []
    if skill_db_path and Path(skill_db_path).exists():
        try:
            from agent.skill_extractor import SkillExtractor
            extractor = SkillExtractor(skill_db_path)
            for s in extractor.list_skills():  # 全状态，按 usage_count desc
                grade = ""
                try:
                    grade = (s.metadata or {}).get("grade", "")
                except Exception:
                    pass
                node = GraphNode(
                    id=f"skill:{s.id}",
                    kind="skill",
                    label=s.name,
                    status=s.status,
                    usage_count=s.usage_count,
                    success_rate=s.success_rate,
                    grade=grade,
                    tool_sequence=s.tool_sequence or [],
                    detail={"description": s.description, "cluster_id": s.cluster_id},
                )
                nodes.append(node)
                skill_nodes.append((s, node))
        except Exception:
            logger.debug("skill nodes load failed", exc_info=True)

        # 时间线：agent_changes 表 ref_kind='skill'
        try:
            conn = sqlite3.connect(skill_db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=3000")
            try:
                rows = conn.execute(
                    """SELECT kind, title, summary, ref_kind, ref_id, created
                       FROM agent_changes
                       WHERE ref_kind = ?
                       ORDER BY created DESC
                       LIMIT 100""",
                    (_REF_SKILL,),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []  # 表不存在则空（fail-open）
            for r in rows:
                timeline.append(TimelineEntry(
                    ts=r["created"] or "",
                    action=_infer_action(r["kind"]),
                    skill_id=int(r["ref_id"] or 0),
                    skill_name=_extract_skill_name(r["title"] or ""),
                    detail=r["summary"] or "",
                ))
            conn.close()
        except Exception:
            logger.debug("timeline load failed", exc_info=True)

    # 2. 记忆节点（memory_index.db 的 memories 表 L1_NOTE layer）
    memory_nodes: List[Tuple[Any, GraphNode]] = []
    try:
        if memory_db_path is None:
            from agent.memory_fabric import _get_index_db
            memory_db_path = str(_get_index_db())
    except Exception:
        logger.debug("memory_db_path resolve failed", exc_info=True)

    if memory_db_path and Path(memory_db_path).exists():
        try:
            conn = sqlite3.connect(memory_db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=3000")
            try:
                rows = conn.execute(
                    """SELECT id, source, fts_content, layer
                       FROM memories
                       WHERE layer = ?
                       ORDER BY id
                       LIMIT 200""",
                    (_L1_NOTE_LAYER,),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            for r in rows:
                content = r["fts_content"] or ""
                label = content[:60].replace("\n", " ").strip()
                if not label:
                    label = f"memory#{r['id']}"
                node = GraphNode(
                    id=f"memory:{r['id']}",
                    kind="memory",
                    label=label,
                    source=r["source"] or "",
                    detail={"layer": r["layer"]},
                )
                nodes.append(node)
                memory_nodes.append((r, node))
            conn.close()
        except Exception:
            logger.debug("memory nodes load failed", exc_info=True)

    # 3. 边：技能 ↔ 技能（tool_sequence 交集 ≥1，双向去重）
    seen_edges: set = set()
    for i, (s_a, n_a) in enumerate(skill_nodes):
        set_a = set(s_a.tool_sequence or [])
        if not set_a:
            continue
        for s_b, n_b in skill_nodes[i + 1:]:
            overlap = set_a & set(s_b.tool_sequence or [])
            if not overlap:
                continue
            key = tuple(sorted([n_a.id, n_b.id]))
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(GraphEdge(
                source=n_a.id, target=n_b.id, rel="tool_overlap",
                weight=len(overlap),
            ))

    # 4. 边：技能 ↔ 记忆（词法重叠）
    for s, n_s in skill_nodes:
        desc_words = set(_tokenize(s.description or ""))
        if not desc_words:
            continue
        for r, n_m in memory_nodes:
            content_words = set(_tokenize(r["fts_content"] or ""))
            overlap = desc_words & content_words
            # 过滤过短 token（len≤2 多为停用词/单字）
            overlap = {w for w in overlap if len(w) > 2}
            if overlap:
                edges.append(GraphEdge(
                    source=n_s.id, target=n_m.id, rel="lexical",
                    weight=len(overlap),
                ))

    totals = {
        "nodes": len(nodes),
        "edges": len(edges),
        "timeline": len(timeline),
        "skills": sum(1 for n in nodes if n.kind == "skill"),
        "memories": sum(1 for n in nodes if n.kind == "memory"),
    }
    return GrowthGraph(nodes=nodes, edges=edges, timeline=timeline, totals=totals)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tokenizer_cache() -> None:
    """Reserved for future precomputed stopword set; currently no-op."""
    return None


def _tokenize(text: str) -> List[str]:
    """简单分词：按非字母数字分割，保留长度>1 的 token。

    对齐上游 learning_graph 的 lexical overlap 思路（不追求语义，只做字面交集）。
    中文按字序列也接受（单字会被 len>1 过滤掉，剩 2-3 字组合作为弱信号）。
    """
    if not text:
        return []
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
    return [t for t in tokens if len(t) > 1]


# kind → 成长动作（对齐 skill_extractor 生命周期事件命名）
_ACTION_MAP = {
    "skill_adopted": "auto_adopted",
    "skill_confirmed": "confirmed",
    "skill_rejected": "rejected",
    "skill_promoted": "promoted",
    "skill_demoted": "demoted",
    "skill_reactivated": "reactivated",
}


def _infer_action(kind: str) -> str:
    """从 change_ledger kind 推断成长动作。"""
    if not kind:
        return "unknown"
    return _ACTION_MAP.get(kind, kind)


def _extract_skill_name(title: str) -> str:
    """从 change title 提取技能名（如「已自动采纳技能：周报生成」→「周报生成」）。"""
    if not title:
        return ""
    for sep in ("：", ":", "—", "-"):
        if sep in title:
            return title.split(sep, 1)[1].strip()
    return title


# ── Serialization ────────────────────────────────────────────────────────────

def growth_graph_to_dict(g: GrowthGraph) -> Dict[str, Any]:
    """序列化给 HTTP 端点 ``GET /api/emergence/graph``。"""
    return {
        "nodes": [
            {
                "id": n.id,
                "kind": n.kind,
                "label": n.label,
                "status": n.status,
                "usage_count": n.usage_count,
                "success_rate": round(n.success_rate, 3),
                "grade": n.grade,
                "tool_sequence": n.tool_sequence,
                "source": n.source,
                "detail": n.detail,
            }
            for n in g.nodes
        ],
        "edges": [
            {"source": e.source, "target": e.target, "rel": e.rel, "weight": e.weight}
            for e in g.edges
        ],
        "timeline": [
            {
                "ts": t.ts,
                "action": t.action,
                "skill_id": t.skill_id,
                "skill_name": t.skill_name,
                "detail": t.detail,
            }
            for t in g.timeline
        ],
        "totals": g.totals,
    }


def get_growth_graph_dict() -> Dict[str, Any]:
    """Convenience：构建 + 序列化，供 HTTP 端点直接返回。fail-open 返回空结构。"""
    try:
        g = build_growth_graph()
        return growth_graph_to_dict(g)
    except Exception as e:
        logger.warning("growth graph build failed: %s", e)
        return {"nodes": [], "edges": [], "timeline": [], "totals": {}, "error": str(e)}
