"""Unified memory fabric (logical-unify · Slice 1).

Treats "text files / session logs / RAG / recall DBs / third-party KBs" all as
memory and unifies them under one substrate. This module is the **first slice**
of the logical-unify plan: existing physical stores are kept, and a new Facade +
single *typed meta-index* is added on top. The index stores only
``(source, layer, type, scope, pointer, fts_content)`` — it does **not** copy the
underlying data, the same way a human brain doesn't store a book, it only
remembers *where to look*.

Layers (mirroring human memory):
- L0 work        — current session (SessionDB)
- L1 note        — curated notes (MEMORY.md / USER.md)
- L2 procedural  — skills / habits
- L3 episodic    — recall / experiences (recall DBs)
- L4 reference   — external knowledge bases / third-party KBs

API:
- ``recall(layer, query, limit)`` — route retrieval by layer (L0..L4).
- ``index_note(target, content)`` — L1 note write into the unified index
  (added alongside the legacy ``_sync_rag_index`` bridge in
  ``tools/memory_tool.py``, which still feeds the RAG FTS5 store).
- ``record(memory)`` / ``list_by_type(type)`` — generic write / list.

fail-closed: index operations no longer silently swallow errors (unlike the old
bridge). ``index_note`` raises on failure so the caller can log it visibly;
``recall`` never raises on a missing/corrupt index (it must not interrupt the
agent) but logs the condition.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home
from harness.metrics import get_metrics

logger = logging.getLogger(__name__)

# Memory layers (human-memory analogy)
L0_WORK = "work"
L1_NOTE = "note"
L2_PROCEDURAL = "procedural"
L3_EPISODIC = "episodic"
L4_REFERENCE = "reference"

# ── Route E: 生命周期标签 ──────────────────────────────────
# 同一份 lifecycle_tag 同时管辖记忆层（活多久）与上下文层（多不可压缩）。
LIFECYCLE_TAGS = {
    "ephemeral",   # 临时：单轮/极短命，优先被裁剪/压缩
    "volatile",    # 易变：会话级，压缩时可交割到冷记忆
    "reference",   # 参考（默认）：普通知识
    "decision",    # 决策：用户已拍板，不可压缩、不可裁剪
    "preference",  # 偏好：用户硬约束，不可压缩、不可裁剪
}
_DEFAULT_LIFECYCLE_TAG = "reference"


def _infer_lifecycle_tag(memory: Dict[str, Any]) -> str:
    """从 memory dict 推导 lifecycle_tag。

    优先级：显式传入 > 文本启发式 > 默认 reference。
    """
    tag = memory.get("lifecycle_tag")
    if tag and tag in LIFECYCLE_TAGS:
        return tag
    # 启发式：fts_content 含 @decision/@preference 标记
    content = (memory.get("fts_content") or "").lower()
    if "@decision" in content:
        return "decision"
    if "@preference" in content:
        return "preference"
    return _DEFAULT_LIFECYCLE_TAG


_LOCK = threading.RLock()

# ── B 硬容量护栏（Route B） ──────────────────────────────────
# 铁律：只降级不删除。超阈值时降低 recall limit、跳过低 access_count 层。
# 绝不物理删除 memories 行，绝不 LLM 改写事实内容。
_MAX_MEMORIES_TOTAL = 5000       # memories 表总行数上限
_COLD_ACCESS_THRESHOLD = 2       # access_count ≤ 此值视为"冷"层
_RECALL_LIMIT_COLD_SCALE = 0.5   # 超阈值时 limit 缩放比例
_CAPACITY_WARN_INTERVAL = 50     # 每超阈值 50 行 log 一次（避免刷屏）
_last_capacity_warn_count = 0    # 上次警告时的行数


def _get_index_db() -> Path:
    return Path(get_hermes_home()) / "memory_index.db"


def index_db_path() -> Path:
    """Public accessor for the unified index DB location (e.g. for the
    runtime to decide whether a one-time backfill is needed)."""
    return _get_index_db()


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now().isoformat()


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn(str(db_path))
    try:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                layer TEXT NOT NULL,
                type TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT '',
                pointer TEXT NOT NULL,
                fts_content TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                lifecycle_tag TEXT NOT NULL DEFAULT 'reference'
            )
            """
        )
        # ── Route E P0: 幂等迁移——存量库加 lifecycle_tag 列 ──
        cols = [r[1] for r in c.execute("PRAGMA table_info(memories)").fetchall()]
        if "lifecycle_tag" not in cols:
            c.execute(
                "ALTER TABLE memories ADD COLUMN lifecycle_tag TEXT NOT NULL DEFAULT 'reference'"
            )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_ptr "
            "ON memories(source, pointer, scope)"
        )
        # External-content FTS5 (trigram tokenizer is friendly to CJK + ASCII
        # substring matching alike).
        c.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts "
            "USING fts5(fts_content, content='memories', content_rowid='id', "
            "tokenize='trigram')"
        )
        c.execute(
            "CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN "
            "INSERT INTO memories_fts(rowid, fts_content) VALUES (new.id, new.fts_content); END"
        )
        c.execute(
            "CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN "
            "INSERT INTO memories_fts(memories_fts, rowid, fts_content) "
            "VALUES('delete', old.id, old.fts_content); END"
        )
        c.execute(
            "CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN "
            "INSERT INTO memories_fts(memories_fts, rowid, fts_content) "
            "VALUES('delete', old.id, old.fts_content); "
            "INSERT INTO memories_fts(rowid, fts_content) VALUES (new.id, new.fts_content); END"
        )
        conn.commit()
    finally:
        conn.close()


def _sanitize_fts(q: str) -> str:
    # strip FTS5 query-syntax specials; keep alphanumerics / CJK / spaces
    return re.sub(r'["*()|:^\[\]{}\\]', " ", q or "").strip()


def index_note(target: str, content: str, scope: str = "") -> None:
    """Write L1 curated memory into the unified index.

    Is the new L1 path for curated notes. The legacy ``_sync_rag_index`` bridge
    in ``tools/memory_tool.py`` still maintains the separate RAG FTS5 store
    (RAG recall depends on it), so this is additive, not a hard replacement.

    fail-closed: raises on failure so the caller logs it visibly instead of
    swallowing the error.
    """
    if not content or not content.strip():
        return
    db_path = _get_index_db()
    _init_db(db_path)
    # 启发式推导 lifecycle_tag
    _mem = {"fts_content": content}
    lifecycle_tag = _infer_lifecycle_tag(_mem)
    with _LOCK:
        conn = _get_conn(str(db_path))
        try:
            c = conn.cursor()
            pointer = f"note#{target}"
            c.execute(
                "DELETE FROM memories WHERE source='note' AND pointer=? AND scope=?",
                (pointer, scope),
            )
            c.execute(
                "INSERT INTO memories(source, layer, type, scope, pointer, "
                "fts_content, updated_at, lifecycle_tag) VALUES(?,?,?,?,?,?,?,?)",
                ("note", L1_NOTE, "note_text", scope, pointer, content, _now(), lifecycle_tag),
            )
            conn.commit()
        finally:
            conn.close()


def _get_memory_count() -> int:
    """Return total row count of memories table (fail-open: 0 on error)."""
    db_path = _get_index_db()
    if not os.path.exists(str(db_path)):
        return 0
    try:
        with _LOCK:
            conn = _get_conn(str(db_path))
            try:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM memories")
                return c.fetchone()[0]
            finally:
                conn.close()
    except Exception:
        logger.debug("memory_fabric._get_memory_count failed", exc_info=True)
        return 0


def _check_capacity() -> dict:
    """Check memory capacity and return degradation directives.

    Returns dict with:
      - over_capacity: bool
      - total_count: int
      - limit_scale: float (1.0 normal, <1.0 degraded)
      - skip_cold: bool (True = skip low access_count entries)
    """
    global _last_capacity_warn_count
    count = _get_memory_count()
    over = count > _MAX_MEMORIES_TOTAL
    if over and (count - _last_capacity_warn_count) >= _CAPACITY_WARN_INTERVAL:
        logger.warning(
            "Memory capacity: %d rows > %d limit \u2014 degrading recall (skip_cold=True, limit_scale=%.1f)",
            count, _MAX_MEMORIES_TOTAL, _RECALL_LIMIT_COLD_SCALE,
        )
        _last_capacity_warn_count = count
        try:
            from agent.metrics import record_count as _rc
            _rc("memory_capacity_degraded")
        except Exception as e:
            logger.debug("memory_fabric.py:  check capacity failed: %s", e)
    return {
        "over_capacity": over,
        "total_count": count,
        "limit_scale": _RECALL_LIMIT_COLD_SCALE if over else 1.0,
        "skip_cold": over,
    }


def recall(
    query: str,
    layer: Optional[str] = None,
    limit: int = 5,
    scope: Optional[str] = None,
    tag_filter: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Route retrieval by layer. ``layer=None`` searches across all layers.

    Args:
        tag_filter: Optional lifecycle_tag filter (e.g. ["decision", "preference"])
            to restrict results to specific lifecycle categories.

    fail-closed: a missing/corrupt index returns ``[]`` and logs (must not
    interrupt the agent).
    """
    if not query or not query.strip():
        return []
    db_path = _get_index_db()
    if not os.path.exists(str(db_path)):
        return []
    terms = _sanitize_fts(query).split()
    if not terms:
        return []

    # ── B 硬容量护栏：超阈值时降级 ──
    cap = _check_capacity()
    effective_limit = max(1, int(limit * cap["limit_scale"]))
    skip_cold = cap["skip_cold"]

    fts = " OR ".join(f'"{t}"' for t in terms[:8])
    sql = (
        "SELECT m.id, m.source, m.layer, m.type, m.scope, m.pointer, "
        "m.fts_content, m.access_count, m.lifecycle_tag "
        "FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid "
        "WHERE memories_fts MATCH ?"
    )
    params: List[Any] = [fts]
    if layer:
        sql += " AND m.layer=?"
        params.append(layer)
    if scope:
        sql += " AND m.scope=?"
        params.append(scope)
    if tag_filter:
        placeholders = ",".join("?" for _ in tag_filter)
        sql += f" AND m.lifecycle_tag IN ({placeholders})"
        params.extend(tag_filter)
    # B 硬容量护栏：超阈值时跳低 access_count 层（冷归档，不删除）
    if skip_cold:
        sql += " AND m.access_count > ?"
        params.append(_COLD_ACCESS_THRESHOLD)
    # 涌现式自适应：FTS 相关性(rank) 主排序；同相关性档内，被召回次数
    # (access_count) 高者靠前。边界由真实使用分布自然涌现，不预设阈值。
    sql += " ORDER BY rank, m.access_count DESC LIMIT ?"
    params.append(effective_limit)
    try:
        with _LOCK:
            conn = _get_conn(str(db_path))
            try:
                c = conn.cursor()
                c.execute(sql, params)
                rows = c.fetchall()
                # Route E P1: tag_filter 回退——FTS 查询词可能与标签记忆内容
                # 不匹配（如查 "decision" 但记忆是中文），回退到纯 tag 查询
                if tag_filter and not rows:
                    _fb_sql = (
                        "SELECT m.id, m.source, m.layer, m.type, m.scope, "
                        "m.pointer, m.fts_content, m.access_count, m.lifecycle_tag "
                        "FROM memories m WHERE m.lifecycle_tag IN (%s)"
                        % placeholders
                    )
                    _fb_params: List[Any] = list(tag_filter)
                    if layer:
                        _fb_sql += " AND m.layer=?"
                        _fb_params.append(layer)
                    if scope:
                        _fb_sql += " AND m.scope=?"
                        _fb_params.append(scope)
                    if skip_cold:
                        _fb_sql += " AND m.access_count > ?"
                        _fb_params.append(_COLD_ACCESS_THRESHOLD)
                    _fb_sql += " ORDER BY m.access_count DESC LIMIT ?"
                    _fb_params.append(effective_limit)
                    c.execute(_fb_sql, _fb_params)
                    rows = c.fetchall()
                # 涌现式传感器：每次被召回即 +1（复用预留 access_count 列，
                # 锁内同连接、fail-open；不影响召回结果，绝不删除/改写事实）。
                bumped = False
                try:
                    for _r in rows:
                        c.execute(
                            "UPDATE memories SET access_count = access_count + 1 "
                            "WHERE id = ?",
                            (_r[0],),
                        )
                    conn.commit()
                    bumped = True
                except Exception:
                    logger.debug(
                        "memory_fabric.recall hit-count bump skipped", exc_info=True
                    )
                # 返回的 access_count 反映本次命中（+1）；fail-open 时保持原值。
                return [
                    {
                        "id": r[0],
                        "source": r[1],
                        "layer": r[2],
                        "type": r[3],
                        "scope": r[4],
                        "pointer": r[5],
                        "content": r[6],
                        "access_count": r[7] + (1 if bumped else 0),
                        "lifecycle_tag": r[8],
                    }
                    for r in rows
                ]
            finally:
                conn.close()
    except Exception:
        logger.warning("memory_fabric.recall failed for %r", query, exc_info=True)
        return []


def get_memory_stats() -> Dict[str, Any]:
    """Return per-layer entry counts and cumulative recall hits.

    Observability probe for the emergent cold/hot layering: lets the runtime
    watch whether recall is concentrating on a few high-frequency memories.
    fail-closed: returns ``{}`` on any error.
    """
    db_path = _get_index_db()
    if not os.path.exists(str(db_path)):
        return {}
    try:
        with _LOCK:
            conn = _get_conn(str(db_path))
            try:
                c = conn.cursor()
                c.execute(
                    "SELECT layer, COUNT(*), COALESCE(SUM(access_count), 0) "
                    "FROM memories GROUP BY layer"
                )
                rows = c.fetchall()
            finally:
                conn.close()
    except Exception:
        logger.warning("memory_fabric.get_memory_stats failed", exc_info=True)
        return {}
    return {r["layer"]: {"count": r[1], "total_hits": r[2]} for r in rows}


def search_notes(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Convenience: recall only L1 curated notes."""
    return recall(query, layer=L1_NOTE, limit=limit)


def record(memory: Dict[str, Any]) -> None:
    """Generic upsert: index one memory by ``(source, pointer, scope)``."""
    source = memory.get("source")
    pointer = memory.get("pointer")
    if not source or not pointer:
        raise ValueError("record() requires 'source' and 'pointer'")
    fts_content = memory.get("fts_content", "")
    if not fts_content.strip():
        return
    layer = memory.get("layer", L4_REFERENCE)
    mtype = memory.get("type", "generic")
    scope = memory.get("scope", "")
    lifecycle_tag = _infer_lifecycle_tag(memory)
    db_path = _get_index_db()
    _init_db(db_path)
    with _LOCK:
        conn = _get_conn(str(db_path))
        try:
            c = conn.cursor()
            c.execute(
                "DELETE FROM memories WHERE source=? AND pointer=? AND scope=?",
                (source, pointer, scope),
            )
            c.execute(
                "INSERT INTO memories(source, layer, type, scope, pointer, "
                "fts_content, updated_at, lifecycle_tag) VALUES(?,?,?,?,?,?,?,?)",
                (source, layer, mtype, scope, pointer, fts_content, _now(), lifecycle_tag),
            )
            conn.commit()
        finally:
            conn.close()


def list_by_type(mtype: str, limit: int = 50) -> List[Dict[str, Any]]:
    db_path = _get_index_db()
    if not os.path.exists(str(db_path)):
        return []
    try:
        with _LOCK:
            conn = _get_conn(str(db_path))
            try:
                c = conn.cursor()
                c.execute(
                    "SELECT id, source, layer, scope, pointer, fts_content "
                    "FROM memories WHERE type=? ORDER BY updated_at DESC LIMIT ?",
                    (mtype, limit),
                )
                return [dict(r) for r in c.fetchall()]
            finally:
                conn.close()
    except Exception:
        logger.warning("memory_fabric.list_by_type failed for %r", mtype, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Slice 2 — hierarchical recall pipeline
# ---------------------------------------------------------------------------
# Layer priority for unified recall ordering (lower number = surfaced first).
# Curated notes (L1) and procedural skills (L2) are more reliable than
# episodic (L3) / reference (L4), so they win ties.
_LAYER_PRIORITY = {
    L1_NOTE: 0,
    L2_PROCEDURAL: 1,
    L3_EPISODIC: 2,
    L4_REFERENCE: 3,
}

# Pluggable federation hooks. The runtime injects them (e.g.
# ``MemoryManager.search_all`` for L4, a live recall adapter for L3) so this
# module stays decoupled from heavy subsystems (no circular imports, unit-
# testable in isolation).
_L4_FEDERATION_HOOK = None
_L3_LIVE_HOOK = None


def set_l4_federation_hook(fn) -> None:
    """Register a callable ``fn(query, limit) -> List[Dict]`` that fans out to
    live reference stores (RAG + external KBs). Its hits are merged into
    ``recall_hierarchical`` as L4. Pass ``None`` to clear."""
    global _L4_FEDERATION_HOOK
    _L4_FEDERATION_HOOK = fn


def get_l4_federation_hook():
    """Return the currently registered L4 federation hook (or ``None``)."""
    return _L4_FEDERATION_HOOK


def set_l3_live_hook(fn) -> None:
    """Register a callable ``fn(query, limit) -> List[Dict]`` that returns live
    episodic recall hits (adapter around ``memory_recall.recall_context`` etc.).
    Optional extension point; not required for the index-based path."""
    global _L3_LIVE_HOOK
    _L3_LIVE_HOOK = fn


def _normalize_hit(hit: Dict[str, Any], default_layer: Optional[str]) -> Dict[str, Any]:
    """Coerce a heterogeneous hit dict into the unified recall shape."""
    layer = hit.get("layer") or default_layer or L4_REFERENCE
    content = hit.get("content") or hit.get("preview") or ""
    return {
        "layer": layer,
        "source": hit.get("source", ""),
        "pointer": hit.get("pointer", ""),
        "content": content,
        "score": float(hit.get("score", 0.0) or 0.0),
        "lifecycle_tag": hit.get("lifecycle_tag", _DEFAULT_LIFECYCLE_TAG),
    }


def recall_hierarchical(
    query: str,
    limit: int = 8,
    layers: Optional[List[str]] = None,
    prioritize_tags: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Unified hierarchical recall across L1–L4 with ordering.

    Pipeline:
      1. fabric index (L1 notes / L2 skills / L3 recall rows / L4 pointers)
         — already ordered by FTS5 rank within the result set.
      2. optional L3 live hook (richer episodic recall).
      3. optional L4 federation hook (live RAG + external KBs).

    Ordering: **layer priority first** (L1 > L2 > L3 > L4), then FTS5 rank
    within a layer (Python's sort is stable, so the rank order returned by
    ``recall`` is preserved inside each layer). Results are de-duplicated by
    ``pointer`` (or content fingerprint when no pointer).

    Args:
        prioritize_tags: If provided, memories with these lifecycle_tags
            (e.g. ["decision", "preference"]) are fetched first and
            guaranteed a slot in the result, so user hard constraints are
            never truncated away by volume noise.

    fail-closed: hook failures are logged and skipped — the agent must never
    be interrupted by a broken external KB or recall subsystem.
    """
    if not query or not query.strip():
        return []
    metrics = get_metrics()
    import time as _time
    _t0 = _time.monotonic()
    results: List[Dict[str, Any]] = []

    # 1) fabric index (covers L1–L4 pointers)
    # Route E P1: 优先召回 @decision/@preference，保证用户硬约束不被容量截断
    _prioritized: List[Dict[str, Any]] = []
    if prioritize_tags:
        try:
            for h in recall(query, layer=None, limit=max(limit, 5),
                            tag_filter=prioritize_tags):
                _prioritized.append(_normalize_hit(h, None))
        except Exception:
            logger.debug("recall_hierarchical: prioritize_tags query failed",
                         exc_info=True)
    # 普通召回（无 tag_filter，包含全部标签）
    for h in recall(query, layer=None, limit=max(limit * 3, 10)):
        results.append(_normalize_hit(h, None))
    metrics.record_recall_layer("L1_L2_index", hits=len(results))
    # 合并：优先标签结果置顶
    results = _prioritized + results

    # 2) optional L3 live recall
    if _L3_LIVE_HOOK is not None and (layers is None or L3_EPISODIC in layers):
        try:
            _l3_hits = 0
            for h in _L3_LIVE_HOOK(query, limit):
                results.append(_normalize_hit(h, L3_EPISODIC))
                _l3_hits += 1
            metrics.record_recall_layer("L3", hits=_l3_hits)
        except Exception:
            logger.warning("memory_fabric L3 live hook failed", exc_info=True)

    # 3) optional L4 federation (RAG + external KBs)
    if _L4_FEDERATION_HOOK is not None and (layers is None or L4_REFERENCE in layers):
        try:
            _l4_hits = 0
            for h in _L4_FEDERATION_HOOK(query, limit):
                results.append(_normalize_hit(h, L4_REFERENCE))
                _l4_hits += 1
            metrics.record_recall_layer("L4_federation", hits=_l4_hits)
        except Exception:
            logger.warning("memory_fabric L4 federation hook failed", exc_info=True)

    # Order by: 1) prioritized tags first (Route E P1), 2) layer priority.
    # Stable sort preserves FTS rank within each ordering tier.
    _prio_set = set(prioritize_tags) if prioritize_tags else set()
    results.sort(
        key=lambda h: (
            0 if h.get("lifecycle_tag", "") in _prio_set else 1,
            _LAYER_PRIORITY.get(h["layer"], 9),
        )
    )

    # De-duplicate. The unified pointer format is ``{source}#{id}`` (A2), so
    # key primarily on it. As a safety net against legacy/heterogeneous
    # pointers that reference the SAME underlying memory with different
    # strings (e.g. a seeded ``rag#{doc}#0`` vs a live ``rag#{doc}#0`` that
    # drifted in format), also fold in a content fingerprint within the same
    # layer, so true duplicates are dropped regardless of format drift.
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for h in results:
        content_fp = (h.get("content") or "")[:80]
        key = (
            f"{h['pointer']}|{h['layer']}:{content_fp}"
            if h["pointer"]
            else f"{h['layer']}:{content_fp}"
        )
        if key in seen:
            metrics.record_dedup_collision(h["layer"])
            continue
        seen.add(key)
        deduped.append(h)
    _elapsed_ms = (_time.monotonic() - _t0) * 1000.0
    metrics.record_recall_latency_ms(_elapsed_ms)
    return deduped[:limit]


def index_skills(skills: List[Dict[str, Any]], scope: str = "") -> int:
    """Bulk-index L2 procedural memory from an injected skill list.

    ``skills`` is a list of dicts (typically from ``tools.skills_tool.
    _find_all_skills`` / ``agent.skill_extractor``) with ``name``,
    ``description``, optional ``category``/``pointer``. Each skill becomes one
    L2 entry keyed by a stable ``pointer`` (``skill:<category>/<name>``).
    Returns the number of skills indexed.
    """
    count = 0
    for s in skills:
        name = (s.get("name") or "").strip()
        if not name:
            continue
        desc = (s.get("description") or "").strip()
        category = (s.get("category") or "").strip()
        pointer = s.get("pointer") or (
            f"skill#{category}/{name}" if category else f"skill#{name}"
        )
        content = f"{name}: {desc}".strip()
        if not content:
            continue
        try:
            record(
                {
                    "source": "skill",
                    "layer": L2_PROCEDURAL,
                    "type": "skill",
                    "scope": scope,
                    "pointer": pointer,
                    "fts_content": content,
                }
            )
            count += 1
        except Exception:
            logger.warning("index_skills failed for %r", name, exc_info=True)
    return count


# ── usage telemetry (越用越懂用户) ──────────────────────────────
# Lightweight, local-only capability-usage ledger. Each launch of an expert or
# skill writes one row into the unified index (source="usage"); the shared
# logical ``pointer`` lets get_usage_counts GROUP BY it for true frequency.
# Everything lives in the local memory_index.db — zero network upload.

def record_usage(kind: str, item_id: str, title: str = "", scope: str = "") -> None:
    """Record one usage event for a capability (expert/skill) as an L1 memory.

    fail-closed: raises on failure so callers can log it visibly instead of
    silently dropping the signal.
    """
    if not kind or not item_id:
        return
    content = f"{title} {item_id}".strip()
    if not content:
        return
    db_path = _get_index_db()
    _init_db(db_path)
    pointer = f"usage:{kind}:{item_id}"
    with _LOCK:
        conn = _get_conn(str(db_path))
        try:
            c = conn.cursor()
            c.execute(
                "INSERT INTO memories(source, layer, type, scope, pointer, "
                "fts_content, updated_at, lifecycle_tag) VALUES(?,?,?,?,?,?,?,?)",
                ("usage", L1_NOTE, f"usage_{kind}", scope, pointer, content, _now(), "ephemeral"),
            )
            conn.commit()
        finally:
            conn.close()


def get_usage_counts(kind: Optional[str] = None, scope: str = "",
                     limit: int = 6) -> List[Dict[str, Any]]:
    """Return most-used capabilities ranked by frequency then recency.

    Returns a list of ``{"kind", "id", "count", "last_used"}`` dicts.
    fail-closed: a missing/corrupt index returns ``[]``.
    """
    db_path = _get_index_db()
    if not os.path.exists(str(db_path)):
        return []
    sql = (
        "SELECT pointer, COUNT(*) AS cnt, MAX(updated_at) AS last "
        "FROM memories WHERE source='usage'"
    )
    params: List[Any] = []
    if kind:
        sql += " AND type=?"
        params.append(f"usage_{kind}")
    if scope:
        sql += " AND scope=?"
        params.append(scope)
    sql += " GROUP BY pointer ORDER BY cnt DESC, last DESC LIMIT ?"
    params.append(limit)
    try:
        with _LOCK:
            conn = _get_conn(str(db_path))
            try:
                c = conn.cursor()
                c.execute(sql, params)
                rows = c.fetchall()
            finally:
                conn.close()
    except Exception:
        logger.warning("memory_fabric.get_usage_counts failed", exc_info=True)
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        pointer = r["pointer"]
        parts = pointer.split(":", 2)
        out.append({
            "kind": parts[1] if len(parts) > 1 else (kind or ""),
            "id": parts[2] if len(parts) > 2 else pointer,
            "count": r["cnt"],
            "last_used": r["last"],
        })
    return out
