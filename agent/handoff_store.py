"""Session handoff store — SQLite persistence for cross-session continuity.

Stores structured summaries generated at session end, loaded at new session
start to provide cross-session task continuity.

Design philosophy (mirrors conversation_compression.py):
  - Deterministic extraction > LLM summarization
  - No API calls, no latency
  - Best-effort: failures never block the main loop
  - Structured fields for programmatic use + free-text summary for display
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DB_PATH: Optional[Path] = None


def _get_db_path() -> Path:
    """Lazily resolve the handoff database path."""
    global _DB_PATH
    if _DB_PATH is not None:
        return _DB_PATH
    # Default: ~/.hermes/session_handoffs.db
    import os
    hermes_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    _db_dir = Path(hermes_home)
    _db_dir.mkdir(parents=True, exist_ok=True)
    _DB_PATH = _db_dir / "session_handoffs.db"
    return _DB_PATH


def _get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or _get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            user_request TEXT,
            tools_used TEXT,
            decisions TEXT,
            pending_tasks TEXT,
            open_questions TEXT,
            summary_text TEXT,
            next_session_id TEXT,
            superseded_by INTEGER
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_handoffs_session "
        "ON session_handoffs(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_handoffs_created "
        "ON session_handoffs(created_at DESC)"
    )
    conn.commit()


def store_handoff(
    session_id: str,
    *,
    user_request: str = "",
    tools_used: List[Dict[str, Any]] = None,
    decisions: List[Dict[str, Any]] = None,
    pending_tasks: List[Dict[str, Any]] = None,
    open_questions: List[str] = None,
    summary_text: str = "",
) -> int:
    """Store a session handoff record. Returns the row id."""
    try:
        conn = _get_conn()
        now = time.time()
        cur = conn.execute(
            """INSERT INTO session_handoffs
               (session_id, created_at, user_request, tools_used,
                decisions, pending_tasks, open_questions, summary_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                now,
                user_request,
                json.dumps(tools_used or [], ensure_ascii=False),
                json.dumps(decisions or [], ensure_ascii=False),
                json.dumps(pending_tasks or [], ensure_ascii=False),
                json.dumps(open_questions or [], ensure_ascii=False),
                summary_text,
            ),
        )
        conn.commit()
        row_id = cur.lastrowid
        conn.close()
        logger.debug(
            "Stored handoff for session %s (row %s): %s",
            session_id, row_id, summary_text[:80] if summary_text else "(no summary)",
        )
        return row_id
    except Exception as e:
        logger.warning("Failed to store handoff: %s", e)
        return -1


def get_latest_handoff(session_id: str = "") -> Optional[Dict[str, Any]]:
    """Get the most recent handoff record.

    If session_id is provided, get the latest handoff for that session.
    Otherwise, get the globally latest handoff.
    """
    try:
        conn = _get_conn()
        if session_id:
            row = conn.execute(
                "SELECT * FROM session_handoffs "
                "WHERE session_id = ? AND superseded_by IS NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM session_handoffs "
                "WHERE superseded_by IS NULL "
                "ORDER BY created_at DESC LIMIT 1",
            ).fetchone()
        conn.close()
        if not row:
            return None
        return _row_to_dict(row)
    except Exception as e:
        logger.warning("Failed to get handoff: %s", e)
        return None


def get_global_latest_handoff() -> Optional[Dict[str, Any]]:
    """Get the most recent handoff across all sessions."""
    return get_latest_handoff("")


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for key in ("tools_used", "decisions", "pending_tasks", "open_questions"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key] = [] if key != "open_questions" else []
        else:
            d[key] = [] if key != "open_questions" else []
    return d


def mark_superseded(row_id: int, by_row_id: int) -> None:
    """Mark a handoff as superseded by a newer one."""
    try:
        conn = _get_conn()
        conn.execute(
            "UPDATE session_handoffs SET superseded_by = ? WHERE id = ?",
            (by_row_id, row_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed to mark handoff superseded: %s", e)
