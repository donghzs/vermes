"""Session plan store — SQLite persistence for SSE reconnect snapshots.

Persists the in-memory ``_session_plan_store`` so plan/todo progress survives
process restart and cross-process takeover. This closes the audit boundary
"跨重启恢复" (vermes_task_pipeline_context_audit_REVISED_20260723.html §6).

Design mirrors ``agent/handoff_store.py``:
  - Best-effort: failures never block the main loop (fail-open).
  - WAL + busy_timeout for safe concurrent access.
  - Deterministic upsert keyed by session_id.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

logger = logging.getLogger(__name__)

_DB_PATH: Optional[Path] = None
_lock = threading.Lock()


def _get_db_path() -> Path:
    """Lazily resolve the session-plans database path under VERMES_HOME."""
    global _DB_PATH
    if _DB_PATH is not None:
        return _DB_PATH
    import os

    VERMES_home = os.environ.get("VERMES_HOME") or os.path.expanduser("~/.vermes")
    _db_dir = Path(VERMES_home)
    _db_dir.mkdir(parents=True, exist_ok=True)
    _DB_PATH = _db_dir / "session_plans.db"
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
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS session_plans (
            session_id TEXT PRIMARY KEY,
            plan_json TEXT,
            todo_states_json TEXT,
            plan_emitted INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        )"""
    )
    try:
        yield conn
    finally:
        conn.close()


def save_plan_state(
    session_id: str,
    plan: object,
    todo_states: Dict[str, str],
    plan_emitted: bool,
) -> None:
    """Persist plan state for a session. Fail-open: any error is logged, never raised."""
    try:
        plan_json = json.dumps(plan, ensure_ascii=False) if plan is not None else None
        todo_json = json.dumps(todo_states or {}, ensure_ascii=False)
        with _lock, _conn() as conn:
            conn.execute(
                """INSERT INTO session_plans (session_id, plan_json, todo_states_json, plan_emitted, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       plan_json=excluded.plan_json,
                       todo_states_json=excluded.todo_states_json,
                       plan_emitted=excluded.plan_emitted,
                       updated_at=excluded.updated_at""",
                (session_id, plan_json, todo_json, 1 if plan_emitted else 0, time.time()),
            )
            conn.commit()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"[session_plan_store] save failed for {session_id}: {e}")


def load_plan_state(session_id: str) -> Optional[Dict]:
    """Load persisted plan state, or None if absent. Fail-open."""
    try:
        with _lock, _conn() as conn:
            row = conn.execute(
                "SELECT plan_json, todo_states_json, plan_emitted FROM session_plans WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        plan = json.loads(row["plan_json"]) if row["plan_json"] else None
        todo_states = json.loads(row["todo_states_json"]) if row["todo_states_json"] else {}
        return {
            "plan": plan,
            "todo_states": todo_states,
            "plan_emitted": bool(row["plan_emitted"]),
        }
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"[session_plan_store] load failed for {session_id}: {e}")
        return None


def delete_plan_state(session_id: str) -> None:
    """Drop persisted plan state for a session (e.g. on explicit reset). Fail-open."""
    try:
        with _lock, _conn() as conn:
            conn.execute("DELETE FROM session_plans WHERE session_id=?", (session_id,))
            conn.commit()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"[session_plan_store] delete failed for {session_id}: {e}")
