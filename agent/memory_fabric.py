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
  (replaces the fragile ``_sync_rag_index`` bridge).
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

    Replaces the old ``_sync_rag_index`` -> ``index_memory_text`` fragile bridge
    (which mirrored MEMORY.md into the separate RAG FTS5 store and silently
    drifted — Bug 1). Notes now live in the single typed index.

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
            pointer = f"note:{target}"
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
