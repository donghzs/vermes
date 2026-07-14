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
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

_DB_PATH: Optional[Path] = None


def _get_db_path() -> Path:
    """Lazily resolve the handoff database path."""
    global _DB_PATH
    if _DB_PATH is not None:
        return _DB_PATH
    import os
    hermes_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    _db_dir = Path(hermes_home)
    _db_dir.mkdir(parents=True, exist_ok=True)
    _DB_PATH = _db_dir / "session_handoffs.db"
    return _DB_PATH


@contextmanager
def _conn(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Context-managed DB connection — guarantees close on exit.

    Usage:
        with _conn() as conn:
            conn.execute(...)
            conn.commit()
    """
    path = db_path or _get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


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
            superseded_by INTEGER,
            keywords TEXT
        )
    """)
    # Add keywords column to existing tables (migration)
    try:
        conn.execute("ALTER TABLE session_handoffs ADD COLUMN keywords TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_handoffs_session "
        "ON session_handoffs(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_handoffs_created "
        "ON session_handoffs(created_at DESC)"
    )
    # Migration: is_active column for incognito/forget
    try:
        conn.execute("ALTER TABLE session_handoffs ADD COLUMN is_active INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
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
    keywords: List[str] = None,
) -> int:
    """Store a session handoff record. Returns the row id, or -1 on failure."""
    try:
        with _conn() as conn:
            now = time.time()
            cur = conn.execute(
                """INSERT INTO session_handoffs
                   (session_id, created_at, user_request, tools_used,
                    decisions, pending_tasks, open_questions, summary_text, keywords)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    now,
                    user_request,
                    json.dumps(tools_used or [], ensure_ascii=False),
                    json.dumps(decisions or [], ensure_ascii=False),
                    json.dumps(pending_tasks or [], ensure_ascii=False),
                    json.dumps(open_questions or [], ensure_ascii=False),
                    summary_text,
                    ",".join(keywords or []),
                ),
            )
            conn.commit()
            row_id = cur.lastrowid
        logger.debug(
            "Stored handoff for session %s (row %s): %s",
            session_id, row_id, summary_text[:80] if summary_text else "(no summary)",
        )
        return row_id
    except Exception as e:
        logger.warning("Failed to store handoff: %s", e)
        return -1


def get_latest_handoff(session_id: str = "") -> Optional[Dict[str, Any]]:
    """Get the most recent non-superseded handoff record.

    If session_id is provided, get the latest handoff for that session.
    Otherwise, get the globally latest handoff.
    """
    try:
        with _conn() as conn:
            if session_id:
                row = conn.execute(
                    "SELECT * FROM session_handoffs "
                    "WHERE session_id = ? AND superseded_by IS NULL AND is_active = 1 "
                    "ORDER BY created_at DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM session_handoffs "
                    "WHERE superseded_by IS NULL AND is_active = 1 "
                    "ORDER BY created_at DESC LIMIT 1",
                ).fetchone()
        if not row:
            return None
        return _row_to_dict(row)
    except Exception as e:
        logger.warning("Failed to get handoff: %s", e)
        return None


def get_global_latest_handoff() -> Optional[Dict[str, Any]]:
    """Get the most recent handoff across all sessions."""
    return get_latest_handoff("")


def get_relevant_handoff(
    query: str,
    *,
    max_age_days: int = 7,
    limit: int = 5,
) -> Optional[Dict[str, Any]]:
    """Get the most relevant handoff for the given query.

    Unlike get_global_latest_handoff() which just returns the newest,
    this function scores handoffs by keyword overlap with the query
    and returns the best match. Falls back to latest if no keyword match.

    Args:
        query: The new session's first user message.
        max_age_days: Only consider handoffs within this age.
        limit: Max candidates to consider.

    Returns:
        Best-matching handoff dict, or None if no handoffs exist.
    """
    if not query or not query.strip():
        return get_global_latest_handoff()

    try:
        from agent.memory_recall import _extract_keywords
        query_keywords = _extract_keywords(query, max_keywords=8)
    except Exception:
        query_keywords = []

    try:
        with _conn() as conn:
            cutoff = time.time() - max_age_days * 86400
            rows = conn.execute(
                "SELECT * FROM session_handoffs "
                "WHERE superseded_by IS NULL AND is_active = 1 AND created_at > ? "
                "ORDER BY created_at DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()

            if not rows:
                return None
            if len(rows) == 1:
                return _row_to_dict(rows[0])

            # Score each handoff by keyword overlap
            best_score = -1
            best_row = rows[0]  # fallback: latest

            for row in rows:
                d = _row_to_dict(row)
                # Score from keywords field
                handoff_keywords = []
                kw_str = d.get("keywords") or ""
                if kw_str:
                    handoff_keywords = [k.strip() for k in kw_str.split(",") if k.strip()]
                # Also extract from user_request + summary_text
                combined_text = (
                    (d.get("user_request") or "") + " " +
                    (d.get("summary_text") or "")
                )
                if not handoff_keywords:
                    try:
                        handoff_keywords = _extract_keywords(combined_text, max_keywords=8)
                    except Exception:
                        handoff_keywords = []

                # Calculate overlap score
                if query_keywords and handoff_keywords:
                    overlap = set(query_keywords) & set(handoff_keywords)
                    # Normalize: overlap count / max keywords
                    score = len(overlap) / max(
                        len(query_keywords), len(handoff_keywords)
                    )
                else:
                    score = 0

                # Recency boost: newer handoffs get slight advantage
                age_hours = (time.time() - d.get("created_at", 0)) / 3600
                recency_boost = max(0, 0.1 - age_hours / (max_age_days * 24 * 10))
                score += recency_boost

                if score > best_score:
                    best_score = score
                    best_row = row

            return _row_to_dict(best_row)
    except Exception as e:
        logger.warning("Failed to get relevant handoff: %s", e)
        return get_global_latest_handoff()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for key in ("tools_used", "decisions", "pending_tasks", "open_questions"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key] = []
        else:
            d[key] = []
    return d


def mark_superseded(row_id: int, by_row_id: int) -> None:
    """Mark a handoff as superseded by a newer one."""
    try:
        with _conn() as conn:
            conn.execute(
                "UPDATE session_handoffs SET superseded_by = ? WHERE id = ?",
                (by_row_id, row_id),
            )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to mark handoff superseded: %s", e)


def deactivate_handoff(session_id: str) -> bool:
    """Deactivate (incognito) a session's handoff so next session won't load it.

    Does NOT delete data — just marks is_active=0.
    Returns True on success.
    """
    try:
        with _conn() as conn:
            conn.execute(
                "UPDATE session_handoffs SET is_active = 0 WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()
        return True
    except Exception as e:
        logger.warning("Failed to deactivate handoff: %s", e)
        return False


def forget_session(session_id: str) -> Dict[str, Any]:
    """Incognito mode: deactivate session memory and decisions.

    Called by the /forget command. Deactivates the session's handoff and
    marks all decisions as superseded. Does NOT delete data (recoverable).

    Returns a summary of what was cleared.
    """
    result = {"handoff": False, "decisions_count": 0, "errors": []}

    # 1. Deactivate handoff
    try:
        result["handoff"] = deactivate_handoff(session_id)
    except Exception as e:
        result["errors"].append(f"handoff: {e}")

    # 2. Mark decisions as superseded
    try:
        from agent.decision_tracker import _clear_session_decisions
        result["decisions_count"] = _clear_session_decisions(session_id)
    except Exception as e:
        result["errors"].append(f"decisions: {e}")

    return result
