"""Memory recall — automatic per-turn context retrieval.

Queries multiple Vermes data sources at each turn to find relevant
historical context, then injects a concise <recalled_context> block
into the system prompt volatile tier.

Data sources (no embedding API required):
  1. outcomes DB — recent tool outcomes for the current task domain
  2. anti_patterns DB — patterns matching current task keywords
  3. emotional_state DB — current emotional trajectory
  4. session_handoffs DB — previous session summaries

Design:
  - Pure SQL + keyword matching (no embedding API calls)
  - Token budget: ~400 tokens (1600 chars)
  - Best-effort: failures never block the main loop
  - Injected at turn 1 alongside handoff + evolution blocks

This is the "no-API" path. When embedding API is configured
(ONEAPI_KEY set), hybrid_retriever.search() supplements with
semantic recall.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Token budget
_MAX_BLOCK_CHARS = 1600  # ~400 tokens
_MAX_OUTCOMES = 5
_MAX_RECENT_DOMAINS = 3
_MAX_EMOTION_SNAPSHOT = 1
_RECENT_WINDOW_HOURS = 24


def _get_hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))


def _get_self_model_db() -> Optional[Path]:
    """Resolve the self-model DB path."""
    db = _get_hermes_home() / "evolution" / "self-model.db"
    return db if db.exists() else None


def _get_fusion_db() -> Optional[Path]:
    """Resolve the fusion-state DB path."""
    db = _get_hermes_home() / "evolution" / "fusion-state.db"
    return db if db.exists() else None


def _get_handoff_db() -> Optional[Path]:
    """Resolve the session handoff DB path."""
    db = _get_hermes_home() / "session_handoffs.db"
    return db if db.exists() else None


def _extract_keywords(message: str, max_keywords: int = 5) -> List[str]:
    """Extract meaningful keywords from user message.

    Filters out common stop words and short tokens.
    """
    if not message:
        return []

    # Remove markdown/formatting
    clean = re.sub(r'[*_`#\[\]()]', ' ', message)

    # Split into words (support both Chinese and English)
    # Chinese: extract 2-4 char sequences
    # English: extract words >= 3 chars
    chinese_tokens = re.findall(r'[\u4e00-\u9fff]{2,4}', clean)
    english_tokens = re.findall(r'[a-zA-Z]{3,}', clean)

    # English stop words
    stop_words = {
        "the", "and", "for", "are", "but", "not", "you", "all",
        "can", "her", "was", "one", "our", "out", "has", "have",
        "from", "this", "that", "with", "they", "will", "each",
        "make", "like", "need", "what", "just", "get", "got",
        "let", "know", "more", "than", "them", "then", "look",
        "come", "some", "take", "want", "here", "there", "where",
    }
    english_tokens = [w.lower() for w in english_tokens if w.lower() not in stop_words]

    # Combine and deduplicate
    all_tokens = chinese_tokens + english_tokens

    # Score by frequency in message
    freq: Dict[str, int] = {}
    for token in all_tokens:
        freq[token] = freq.get(token, 0) + 1

    # Sort by frequency, then prefer longer tokens
    sorted_tokens = sorted(freq.keys(), key=lambda t: (-freq[t], -len(t)))

    return sorted_tokens[:max_keywords]


def _query_recent_outcomes(
    conn: sqlite3.Connection,
    keywords: List[str],
    limit: int = _MAX_OUTCOMES,
) -> List[Dict[str, Any]]:
    """Find recent outcomes matching keywords.

    Uses LIKE matching on task/tool/domain fields.
    Returns most recent matching outcomes.
    """
    if not keywords:
        # Just return most recent outcomes
        rows = conn.execute(
            "SELECT task, tool, success, domain, duration, timestamp "
            "FROM outcomes "
            "WHERE timestamp > ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (time.time() - _RECENT_WINDOW_HOURS * 3600, limit),
        ).fetchall()
    else:
        # Build OR conditions for each keyword
        conditions = []
        params: List[Any] = []
        for kw in keywords:
            conditions.append("(task LIKE ? OR tool LIKE ? OR domain LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])

        where_clause = " OR ".join(conditions)
        params.insert(0, time.time() - _RECENT_WINDOW_HOURS * 3600)
        params.append(limit)

        rows = conn.execute(
            f"SELECT task, tool, success, domain, duration, timestamp "
            f"FROM outcomes "
            f"WHERE timestamp > ? AND ({where_clause}) "
            f"ORDER BY timestamp DESC LIMIT ?",
            params,
        ).fetchall()

    return [
        {
            "task": r[0],
            "tool": r[1],
            "success": bool(r[2]),
            "domain": r[3],
            "duration": r[4],
        }
        for r in rows
    ]


def _query_domain_stats(
    conn: sqlite3.Connection,
    keywords: List[str],
) -> List[Dict[str, Any]]:
    """Get success rate per domain for matching outcomes."""
    if not keywords:
        return []

    conditions = []
    params: List[Any] = []
    for kw in keywords:
        conditions.append("(task LIKE ? OR tool LIKE ? OR domain LIKE ?)")
        params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])

    where_clause = " OR ".join(conditions)

    rows = conn.execute(
        f"SELECT domain, "
        f"  COUNT(*) as total, "
        f"  SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as success_count, "
        f"  AVG(duration) as avg_duration "
        f"FROM outcomes "
        f"WHERE {where_clause} "
        f"GROUP BY domain "
        f"ORDER BY total DESC LIMIT ?",
        params + [_MAX_RECENT_DOMAINS],
    ).fetchall()

    return [
        {
            "domain": r[0],
            "total": r[1],
            "success_rate": round(r[2] / r[1], 2) if r[1] > 0 else 0,
            "avg_duration": round(r[3], 2) if r[3] else 0,
        }
        for r in rows if r[1] > 0
    ]


def _query_emotion_snapshot(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """Get the latest emotional state snapshot."""
    row = conn.execute(
        "SELECT * FROM emotional_state ORDER BY rowid DESC LIMIT 1"
    ).fetchone()

    if not row:
        return None

    cols = [d[0] for d in conn.execute(
        "SELECT * FROM emotional_state ORDER BY rowid DESC LIMIT 1"
    ).description]

    data = dict(zip(cols, row))

    # Extract the most meaningful fields
    emotion = data.get("emotion") or data.get("state") or ""
    intensity = data.get("intensity", 0)
    valence = data.get("valence", 0)

    if not emotion:
        return None

    return {
        "emotion": emotion,
        "intensity": round(float(intensity), 2) if intensity else 0,
        "valence": round(float(valence), 2) if valence else 0,
    }


def recall_context(user_message: str) -> Optional[Dict[str, Any]]:
    """Recall relevant context for the current user message.

    Queries multiple data sources and returns a structured dict.
    Returns None if no data available.
    """
    keywords = _extract_keywords(user_message)

    result: Dict[str, Any] = {}

    # Source 1: outcomes + domain stats from self-model DB
    self_model_db = _get_self_model_db()
    if self_model_db:
        try:
            conn = sqlite3.connect(str(self_model_db))
            conn.row_factory = sqlite3.Row

            recent_outcomes = _query_recent_outcomes(conn, keywords)
            if recent_outcomes:
                result["recent_outcomes"] = recent_outcomes

            domain_stats = _query_domain_stats(conn, keywords)
            if domain_stats:
                result["domain_stats"] = domain_stats

            conn.close()
        except Exception as e:
            logger.debug("self-model DB query failed: %s", e)

    # Source 2: emotional state from fusion-state DB
    fusion_db = _get_fusion_db()
    if fusion_db:
        try:
            conn = sqlite3.connect(str(fusion_db))
            conn.row_factory = sqlite3.Row

            emotion = _query_emotion_snapshot(conn)
            if emotion:
                result["emotion"] = emotion

            conn.close()
        except Exception as e:
            logger.debug("fusion-state DB query failed: %s", e)

    # Source 3: hybrid_retriever (if embedding API configured)
    try:
        from agent.hybrid_retriever import search as embedding_search
        embedding_results = embedding_search(user_message, top_k=3)
        if embedding_results:
            result["embedding_matches"] = embedding_results
    except Exception:
        pass  # No embedding API or DB — skip silently

    if not result:
        return None

    result["keywords"] = keywords
    return result


def format_recall_for_prompt(recall: Dict[str, Any]) -> str:
    """Format recalled context as a system prompt block.

    Returns a <recalled_context> block. Empty string if nothing to inject.
    """
    parts: List[str] = ["<recalled_context>"]

    # Domain stats
    domain_stats = recall.get("domain_stats", [])
    if domain_stats:
        parts.append("Related task domains:")
        for ds in domain_stats:
            parts.append(
                f"  • {ds['domain']}: {ds['total']} actions, "
                f"{ds['success_rate']:.0%} success"
            )

    # Recent outcomes (failures are more informative)
    outcomes = recall.get("recent_outcomes", [])
    if outcomes:
        failures = [o for o in outcomes if not o["success"]]
        successes = [o for o in outcomes if o["success"]]

        if failures:
            parts.append("\nRecent failures in this area:")
            for f in failures[:3]:
                parts.append(f"  ✗ {f['tool']} ({f['domain']}): {f['task'][:60]}")

        if successes and len(parts) < 10:
            parts.append("\nRecent successes:")
            for s in successes[:2]:
                parts.append(f"  ✓ {s['tool']} ({s['domain']}): {s['task'][:60]}")

    # Emotional state
    emotion = recall.get("emotion")
    if emotion:
        parts.append(
            f"\nCurrent emotional state: {emotion['emotion']} "
            f"(intensity: {emotion['intensity']:.1f}, "
            f"valence: {emotion['valence']:+.1f})"
        )

    # Embedding matches (if available)
    embeddings = recall.get("embedding_matches", [])
    if embeddings:
        parts.append("\nSemantically similar memories:")
        for em in embeddings[:3]:
            content = em.get("content", "")[:80]
            score = em.get("score", 0)
            parts.append(f"  ≈ [{score:.2f}] {content}")

    parts.append("</recalled_context>")

    block = "\n".join(parts)

    # Enforce token budget
    if len(block) > _MAX_BLOCK_CHARS:
        block = block[:_MAX_BLOCK_CHARS] + "\n</recalled_context>"

    # Don't inject if only the wrapper tags are there
    inner = block.replace("<recalled_context>", "").replace("</recalled_context>", "").strip()
    if not inner:
        return ""

    return block


def load_and_format_recall(user_message: str) -> str:
    """Convenience: recall context and format for prompt injection.

    Returns empty string if no recall data available.
    """
    recall = recall_context(user_message)
    if recall is None:
        return ""
    return format_recall_for_prompt(recall)
