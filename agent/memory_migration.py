"""Backfill existing memory into the unified fabric index (Slice 4).

The logical-unify plan keeps every physical store as its own source of truth
and adds a single *typed meta-index* on top (``agent.memory_fabric``). For the
index to be useful, it must be seeded with the memory that already exists on
disk. This module backfills four layers:

  L1 note       — MEMORY.md / USER.md (curated notes)
  L2 procedural — skills (curated + emergent)
  L3 episodic   — recall DBs (raw_events / emotional_state / session_handoffs)
  L4 reference  — RAG documents.db (pointers + previews, body left in place)

Design notes:
  * Idempotent: ``index_note`` / ``record`` de-dupe by (source, pointer, scope),
    so re-running only refreshes changed rows.
  * Best-effort / fail-soft: each layer is isolated — a missing DB or a read
    error logs and continues, it never aborts the whole migration.
  * Dependency-light: reads sibling stores via ``sqlite3`` / plain file I/O
    and imports heavy discovery helpers (``skills_tool``) lazily, so this
    module can be unit-tested with a throwaway ``HERMES_HOME`` and no agent
    runtime.

Run once (or whenever you want to refresh the index):
    python -m agent.memory_migration
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

from agent.memory_fabric import (
    L1_NOTE,
    L2_PROCEDURAL,
    L3_EPISODIC,
    L4_REFERENCE,
    index_note,
    index_skills,
    record,
)

logger = logging.getLogger(__name__)

_MAX_LEN = 4000  # cap per-entry fts_content so the index stays small


def _trunc(text: str, n: int = _MAX_LEN) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


def _safe_connect(db_path: Path) -> Optional[sqlite3.Connection]:
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.warning("memory_migration: cannot open %s: %s", db_path, e)
        return None


# ---------------------------------------------------------------------------
# L1 — curated notes (MEMORY.md / USER.md)
# ---------------------------------------------------------------------------

def _migrate_notes(home: Path) -> int:
    count = 0
    mem_dir = home / "memories"
    for target, fname in (("memory", "MEMORY.md"), ("user", "USER.md")):
        path = mem_dir / fname
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("memory_migration: read %s failed: %s", path, e)
            continue
        if not text.strip():
            continue
        try:
            index_note(target, text)
            count += 1
        except Exception as e:  # fail-closed in fabric → log, keep going
            logger.warning("memory_migration: index_note(%s) failed: %s", target, e)
    return count


# ---------------------------------------------------------------------------
# L2 — procedural memory (skills)
# ---------------------------------------------------------------------------

def _discover_skills() -> List[Dict[str, Any]]:
    """Lazily discover curated skills without importing the agent runtime."""
    try:
        from tools.skills_tool import _find_all_skills

        return _find_all_skills() or []
    except Exception as e:
        logger.warning("memory_migration: skill discovery failed: %s", e)
        return []


def _migrate_skills(home: Path, skills: Optional[List[Dict[str, Any]]] = None) -> int:
    if skills is None:
        skills = _discover_skills()
    if not skills:
        return 0
    try:
        return index_skills(skills)
    except Exception as e:
        logger.warning("memory_migration: index_skills failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# L3 — episodic memory (recall DBs)
# ---------------------------------------------------------------------------

def _migrate_recall(home: Path) -> int:
    count = 0
    # self-model.db → raw_events
    conn = _safe_connect(home / "evolution" / "self-model.db")
    if conn is not None:
        try:
            c = conn.cursor()
            for row in c.execute(
                "SELECT id, tool_name, args_preview, result_preview "
                "FROM raw_events ORDER BY id DESC LIMIT 5000"
            ):
                content = _trunc(
                    f"{row['tool_name']}: {row['args_preview']} -> {row['result_preview']}"
                )
                if not content:
                    continue
                record(
                    {
                        "source": "recall",
                        "layer": L3_EPISODIC,
                        "type": "raw_event",
                        "pointer": f"recall:self-model.db#raw_events:{row['id']}",
                        "fts_content": content,
                    }
                )
                count += 1
        except sqlite3.Error as e:
            logger.warning("memory_migration: self-model.db read failed: %s", e)
        finally:
            conn.close()
    # fusion-state.db → emotional_state
    conn = _safe_connect(home / "evolution" / "fusion-state.db")
    if conn is not None:
        try:
            c = conn.cursor()
            for row in c.execute(
                "SELECT id, emotion, intensity, trigger, context "
                "FROM emotional_state ORDER BY id DESC LIMIT 2000"
            ):
                content = _trunc(
                    f"{row['emotion']} ({row['intensity']}): {row['trigger']} / {row['context']}"
                )
                if not content:
                    continue
                record(
                    {
                        "source": "recall",
                        "layer": L3_EPISODIC,
                        "type": "emotion",
                        "pointer": f"recall:fusion-state.db#emotional_state:{row['id']}",
                        "fts_content": content,
                    }
                )
                count += 1
        except sqlite3.Error as e:
            logger.warning("memory_migration: fusion-state.db read failed: %s", e)
        finally:
            conn.close()
    # session_handoffs.db → session_handoffs
    conn = _safe_connect(home / "session_handoffs.db")
    if conn is not None:
        try:
            c = conn.cursor()
            for row in c.execute(
                "SELECT id, user_request, summary_text, decisions "
                "FROM session_handoffs ORDER BY id DESC LIMIT 2000"
            ):
                content = _trunc(
                    f"{row['user_request']}\n{row['summary_text']}\n{row['decisions']}"
                )
                if not content:
                    continue
                record(
                    {
                        "source": "recall",
                        "layer": L3_EPISODIC,
                        "type": "handoff",
                        "pointer": f"recall:session_handoffs.db#{row['id']}",
                        "fts_content": content,
                    }
                )
                count += 1
        except sqlite3.Error as e:
            logger.warning("memory_migration: session_handoffs.db read failed: %s", e)
        finally:
            conn.close()
    return count


# ---------------------------------------------------------------------------
# L4 — reference memory (RAG documents)
# ---------------------------------------------------------------------------

def _migrate_rag(home: Path) -> int:
    conn = _safe_connect(home / "rag" / "documents.db")
    if conn is None:
        return 0
    count = 0
    try:
        c = conn.cursor()
        for row in c.execute(
            "SELECT d.id, d.filename, c.content "
            "FROM documents d LEFT JOIN chunks c ON c.doc_id = d.id AND c.chunk_index = 0 "
            "ORDER BY d.id"
        ):
            content = _trunc(f"{row['filename']}\n{row['content'] or ''}")
            if not content.strip():
                continue
            record(
                {
                    "source": "rag",
                    "layer": L4_REFERENCE,
                    "type": "document",
                    "pointer": f"rag:documents.db#{row['id']}",
                    "fts_content": content,
                }
            )
            count += 1
    except sqlite3.Error as e:
        logger.warning("memory_migration: documents.db read failed: %s", e)
    finally:
        conn.close()
    return count


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def migrate_memories_to_fabric(
    hermes_home: Optional[str] = None,
    skills: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, int]:
    """Backfill every physical memory store into the unified index.

    Args:
        hermes_home: override (tests); defaults to ``get_hermes_home()``.
        skills: pre-discovered skill list (tests); defaults to lazy discovery.

    Returns a per-layer count summary.
    """
    home = Path(hermes_home) if hermes_home else Path(get_hermes_home())
    summary = {
        "L1_note": _migrate_notes(home),
        "L2_procedural": _migrate_skills(home, skills),
        "L3_episodic": _migrate_recall(home),
        "L4_reference": _migrate_rag(home),
    }
    logger.info("memory_migration summary: %s", summary)
    return summary


def main() -> None:
    import json

    summary = migrate_memories_to_fabric()
    print(json.dumps({"ok": True, "migrated": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
