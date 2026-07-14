"""Decision tracker — records decisions and detects contradictions.

Tracks decisions made across sessions, enabling the agent to:
  1. Recall past decisions when similar topics arise
  2. Detect when a new decision contradicts an old one
  3. Mark superseded decisions automatically

Design:
  - Pure SQL + keyword matching (no embedding API required)
  - Lightweight: only records explicit decisions, not every message
  - Contradiction detection uses keyword overlap + negation patterns
  - Stored in evolution DB alongside outcomes/anti_patterns

Schema (in self-model.db):
  decisions table — decision text, context, status, contradiction info
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────

_DECISIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    timestamp REAL NOT NULL,
    decision TEXT NOT NULL,
    context TEXT,
    status TEXT DEFAULT 'active',
    supersedes_id INTEGER,
    contradiction_reason TEXT,
    keywords TEXT
)
"""

_DECISIONS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_decisions_status
ON decisions(status)
"""

# ── Connection ────────────────────────────────────────────────────────


def _get_self_model_db() -> Optional[Path]:
    """Resolve the self-model DB path."""
    hermes_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    db = Path(hermes_home) / "evolution" / "self-model.db"
    return db if db.exists() else None


def _get_conn() -> Optional[sqlite3.Connection]:
    """Get a connection to the self-model DB, initializing the decisions table."""
    db_path = _get_self_model_db()
    if db_path is None:
        return None

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_DECISIONS_SCHEMA)
    conn.execute(_DECISIONS_INDEX)
    conn.commit()
    return conn


# ── Keyword extraction ────────────────────────────────────────────────


def _extract_decision_keywords(text: str) -> List[str]:
    """Extract keywords from a decision text.

    Similar to memory_recall but tuned for decision statements.
    """
    if not text:
        return []

    clean = re.sub(r'[*_`#\[\]()]', ' ', text)

    # Extract Chinese 2-4 char sequences
    chinese_tokens = re.findall(r'[\u4e00-\u9fff]{2,4}', clean)

    # Extract English words >= 3 chars
    english_tokens = re.findall(r'[a-zA-Z]{3,}', clean)

    stop_words = {
        "the", "and", "for", "are", "but", "not", "you", "all",
        "can", "was", "one", "our", "out", "has", "have", "from",
        "this", "that", "with", "they", "will", "each", "make",
        "like", "need", "what", "just", "get", "got", "let", "know",
        "more", "than", "them", "then", "look", "come", "some",
        "take", "want", "here", "there", "where", "should", "would",
        "could", "will", "shall", "may", "might", "must",
    }
    english_tokens = [w.lower() for w in english_tokens if w.lower() not in stop_words]

    # Deduplicate while preserving order
    seen = set()
    all_tokens = chinese_tokens + english_tokens
    result = []
    for t in all_tokens:
        if t not in seen:
            seen.add(t)
            result.append(t)

    return result[:8]  # More keywords for decisions (broader matching)


# ── Contradiction detection ───────────────────────────────────────────

# Negation patterns that indicate a decision reversal
_NEGATION_PATTERNS = [
    r'不要',
    r'不用',
    r'不需要',
    r'不再',
    r'放弃',
    r'取消',
    r'改用',
    r'换成',
    r'改为',
    r'废弃',
    r'停用',
    r'撤销',
    r'反转',
    r'翻转',
    r"don't",
    r'avoid',
    r'stop',
    r'remove',
    r'replace',
    r'deprecate',
    r'instead',
    r'switch',
]

# Transition words that suggest a decision is being revised
_REVISION_PATTERNS = [
    r'重新决定',
    r'改变主意',
    r'改为',
    r'换成',
    r'不再使用',
    r'替代',
    r'upgrade',
    r'migrate',
    r'refactor',
    r'replace',
]


def _check_contradiction(
    new_decision: str,
    old_decision: str,
    new_keywords: List[str],
    old_keywords: List[str],
) -> Optional[str]:
    """Check if new_decision contradicts old_decision.

    Returns a reason string if contradiction detected, None otherwise.
    Uses two signals:
      1. Keyword overlap (decisions about the same topic)
      2. Negation/revision patterns in the new decision
    """
    # Must have keyword overlap (same topic)
    overlap = set(new_keywords) & set(old_keywords)
    if not overlap:
        return None

    # Check if new decision contains negation patterns
    new_lower = new_decision.lower()

    for pattern in _NEGATION_PATTERNS:
        if re.search(pattern, new_decision, re.IGNORECASE):
            # Check if the negation is related to the overlapping keywords
            new_lower = new_decision.lower()
            for kw in overlap:
                if kw.lower() in new_lower:
                    return (
                        f"Negation pattern '{pattern}' found related to '{kw}'"
                    )

    # Check for revision patterns
    for pattern in _REVISION_PATTERNS:
        if re.search(pattern, new_decision, re.IGNORECASE):
            return f"Revision pattern '{pattern}' found"

    # Check direct opposite: old says "use X", new says "use Y" (different)
    # Simple heuristic: if both decisions mention a tool/tech name and they differ
    old_tools = {kw for kw in old_keywords if kw.isalpha() and len(kw) >= 3}
    new_tools = {kw for kw in new_keywords if kw.isalpha() and len(kw) >= 3}
    if old_tools and new_tools:
        only_old = old_tools - new_tools
        only_new = new_tools - old_tools
        if only_old and only_new:
            # Both have unique tools — possible replacement
            return (
                f"Tool shift: {only_old} → {only_new}"
            )

    return None


# ── Public API ────────────────────────────────────────────────────────


def record_decision(
    decision: str,
    context: str = "",
    session_id: str = "",
) -> Dict[str, Any]:
    """Record a decision and check for contradictions.

    Returns a dict with:
      - id: the new decision's row id
      - contradicted: list of superseded decision ids
      - contradiction_reasons: dict of {old_id: reason}
    """
    if not decision.strip():
        return {"id": -1, "contradicted": [], "contradiction_reasons": {}}

    conn = _get_conn()
    if conn is None:
        return {"id": -1, "contradicted": [], "contradiction_reasons": {}}

    try:
        keywords = _extract_decision_keywords(decision)
        now = time.time()

        # Find potentially contradicting active decisions
        contradicted: List[int] = []
        reasons: Dict[int, str] = {}

        if keywords:
            # Build query to find active decisions with keyword overlap
            conditions = []
            params: List[Any] = []
            for kw in keywords:
                conditions.append("keywords LIKE ?")
                params.append(f"%{kw}%")

            where_clause = " OR ".join(conditions)
            params_with_status = ["active"] + params

            old_decisions = conn.execute(
                f"SELECT id, decision, keywords FROM decisions "
                f"WHERE status = ? AND ({where_clause}) "
                f"ORDER BY timestamp DESC LIMIT 10",
                params_with_status,
            ).fetchall()

            for old in old_decisions:
                old_keywords = old["keywords"].split(",") if old["keywords"] else []
                old_keywords = [k.strip() for k in old_keywords if k.strip()]

                reason = _check_contradiction(
                    decision, old["decision"], keywords, old_keywords
                )
                if reason:
                    contradicted.append(old["id"])
                    reasons[old["id"]] = reason

        # Insert the new decision
        cur = conn.execute(
            "INSERT INTO decisions (session_id, timestamp, decision, context, status, keywords) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (session_id, now, decision, context, ",".join(keywords)),
        )
        new_id = cur.lastrowid

        # Mark contradicted decisions as superseded
        for old_id in contradicted:
            conn.execute(
                "UPDATE decisions SET status = 'superseded', supersedes_id = ?, "
                "contradiction_reason = ? WHERE id = ?",
                (new_id, reasons[old_id], old_id),
            )

        conn.commit()

        return {
            "id": new_id,
            "contradicted": contradicted,
            "contradiction_reasons": reasons,
        }
    except Exception as e:
        logger.warning("Failed to record decision: %s", e)
        return {"id": -1, "contradicted": [], "contradiction_reasons": {}}
    finally:
        conn.close()


def get_active_decisions(limit: int = 10) -> List[Dict[str, Any]]:
    """Get all active (non-superseded) decisions, most recent first."""
    conn = _get_conn()
    if conn is None:
        return []

    try:
        rows = conn.execute(
            "SELECT id, session_id, timestamp, decision, context, keywords "
            "FROM decisions WHERE status = 'active' "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()

        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "timestamp": r["timestamp"],
                "decision": r["decision"],
                "context": r["context"],
                "keywords": r["keywords"].split(",") if r["keywords"] else [],
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning("Failed to get active decisions: %s", e)
        return []
    finally:
        conn.close()


def get_superseded_decisions(limit: int = 10) -> List[Dict[str, Any]]:
    """Get superseded decisions with their contradiction reasons."""
    conn = _get_conn()
    if conn is None:
        return []

    try:
        rows = conn.execute(
            "SELECT d.id, d.session_id, d.timestamp, d.decision, d.context, "
            "d.supersedes_id, d.contradiction_reason, "
            "d2.decision as new_decision "
            "FROM decisions d "
            "LEFT JOIN decisions d2 ON d.supersedes_id = d2.id "
            "WHERE d.status = 'superseded' "
            "ORDER BY d.timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()

        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "timestamp": r["timestamp"],
                "decision": r["decision"],
                "contradiction_reason": r["contradiction_reason"],
                "new_decision": r["new_decision"],
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning("Failed to get superseded decisions: %s", e)
        return []
    finally:
        conn.close()


def format_decisions_for_prompt(limit: int = 5) -> str:
    """Format active decisions as a system prompt block.

    Returns a <active_decisions> block. Empty string if no decisions.
    """
    decisions = get_active_decisions(limit)
    if not decisions:
        return ""

    parts = ["<active_decisions>"]
    parts.append("Standing decisions from past sessions:")

    for d in decisions:
        age_hours = (time.time() - d["timestamp"]) / 3600
        if age_hours < 24:
            age_str = f"{age_hours:.0f}h ago"
        else:
            age_str = f"{age_hours / 24:.0f}d ago"

        parts.append(f"  • [{age_str}] {d['decision']}")
        if d.get("context"):
            parts.append(f"    context: {d['context'][:80]}")

    parts.append("</active_decisions>")

    return "\n".join(parts)


def _clear_session_decisions(session_id: str) -> int:
    """Mark all decisions from a session as superseded (for /forget).

    Does NOT delete rows — marks status='superseded'.
    Returns count of decisions cleared.
    """
    try:
        conn = _get_conn()
        cur = conn.execute(
            "UPDATE decisions SET status = 'superseded' "
            "WHERE session_id = ? AND status = 'active'",
            (session_id,),
        )
        conn.commit()
        conn.close()
        return cur.rowcount
    except Exception as e:
        logger.warning("Failed to clear session decisions: %s", e)
        return 0
