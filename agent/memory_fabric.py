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

logger = logging.getLogger(__name__)

# Memory layers (human-memory analogy)
L0_WORK = "work"
L1_NOTE = "note"
L2_PROCEDURAL = "procedural"
L3_EPISODIC = "episodic"
L4_REFERENCE = "reference"

_LOCK = threading.RLock()


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
                access_count INTEGER NOT NULL DEFAULT 0
            )
            """
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
                "fts_content, updated_at) VALUES(?,?,?,?,?,?,?)",
                ("note", L1_NOTE, "note_text", scope, pointer, content, _now()),
            )
            conn.commit()
        finally:
            conn.close()


def recall(
    query: str,
    layer: Optional[str] = None,
    limit: int = 5,
    scope: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Route retrieval by layer. ``layer=None`` searches across all layers.

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
    fts = " OR ".join(f'"{t}"' for t in terms[:8])
    sql = (
        "SELECT m.id, m.source, m.layer, m.type, m.scope, m.pointer, m.fts_content "
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
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        with _LOCK:
            conn = _get_conn(str(db_path))
            try:
                c = conn.cursor()
                c.execute(sql, params)
                return [
                    {
                        "id": r[0],
                        "source": r[1],
                        "layer": r[2],
                        "type": r[3],
                        "scope": r[4],
                        "pointer": r[5],
                        "content": r[6],
                    }
                    for r in c.fetchall()
                ]
            finally:
                conn.close()
    except Exception:
        logger.warning("memory_fabric.recall failed for %r", query, exc_info=True)
        return []


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
                "fts_content, updated_at) VALUES(?,?,?,?,?,?,?)",
                (source, layer, mtype, scope, pointer, fts_content, _now()),
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
    }


def recall_hierarchical(
    query: str,
    limit: int = 8,
    layers: Optional[List[str]] = None,
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

    fail-closed: hook failures are logged and skipped — the agent must never
    be interrupted by a broken external KB or recall subsystem.
    """
    if not query or not query.strip():
        return []
    results: List[Dict[str, Any]] = []

    # 1) fabric index (covers L1–L4 pointers)
    for h in recall(query, layer=None, limit=max(limit * 3, 10)):
        results.append(_normalize_hit(h, None))

    # 2) optional L3 live recall
    if _L3_LIVE_HOOK is not None and (layers is None or L3_EPISODIC in layers):
        try:
            for h in _L3_LIVE_HOOK(query, limit):
                results.append(_normalize_hit(h, L3_EPISODIC))
        except Exception:
            logger.warning("memory_fabric L3 live hook failed", exc_info=True)

    # 3) optional L4 federation (RAG + external KBs)
    if _L4_FEDERATION_HOOK is not None and (layers is None or L4_REFERENCE in layers):
        try:
            for h in _L4_FEDERATION_HOOK(query, limit):
                results.append(_normalize_hit(h, L4_REFERENCE))
        except Exception:
            logger.warning("memory_fabric L4 federation hook failed", exc_info=True)

    # Order by layer priority (stable → preserves FTS rank within layer).
    results.sort(key=lambda h: _LAYER_PRIORITY.get(h["layer"], 9))

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
            continue
        seen.add(key)
        deduped.append(h)
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
