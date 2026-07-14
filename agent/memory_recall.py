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

Context Richness (data-density-driven injection):
  Rather than a hardcoded "enable prediction after N sessions",
  the system computes a 0-1 richness score from actual data density:
    - total raw_events (usage depth)
    - stable clusters (pattern maturity)
    - session handoffs (cross-session continuity)
    - past sessions (usage breadth)
  The richness score dynamically adjusts how much context gets
  injected — when the user is new (richness < 0.3), the system
  stays lightweight; as usage accumulates (richness > 0.6), context
  injection scales naturally. No user-facing toggle needed.
"""

from __future__ import annotations

import logging
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Token budget
_MAX_BLOCK_CHARS = 1600  # ~400 tokens
_MAX_OUTCOMES = 5
_MAX_RECENT_DOMAINS = 3
_MAX_EMOTION_SNAPSHOT = 1
_RECENT_WINDOW_HOURS = 24

# ── Context Richness ─────────────────────────────────────────────────────────
# Richness thresholds for data-density-driven injection scaling.
# These are NOT hardcoded "enable after N sessions" rules.
# They define the sigmoid curve that naturally controls how much
# context gets injected as the user accumulates data.

_RICHNESS_HIGH = 0.6   # Above this: full context injection ("Vermes knows you well")
_RICHNESS_LOW = 0.3    # Below this: minimal context (still building knowledge)

# Richness component weights (sum to 1.0)
_W_RAW_EVENTS = 0.35    # Raw event volume — most direct measure of usage
_W_STABLE_CLUSTERS = 0.30  # Pattern maturity — emerged from usage, not configured
_W_SESSIONS = 0.20      # Session count — breadth across different contexts
_W_HANDOFFS = 0.15      # Cross-session continuity — knowledge that persists

# Component reference points (values that map to richness ~0.85)
_REF_RAW_EVENTS = 500    # ~500 raw events = substantial usage
_REF_STABLE_CLUSTERS = 10  # ~10 stable clusters = diverse behavior patterns
_REF_SESSIONS = 20       # ~20 sessions = multi-context usage
_REF_HANDOFFS = 10       # ~10 handoffs = strong cross-session memory


@dataclass
class RichnessScore:
    """Data-density richness result.

    A 0-1 score computed from actual data volume — no hardcoded
    session-count gates. Every consumer reads this one signal
    instead of duplicating gating logic.
    """
    value: float = 0.0           # 0.0 (cold start) to 1.0 (deep knowledge)
    tier: str = "cold_start"     # cold_start | building | learning | fluent

    # Per-component breakdown (for debugging/logging)
    raw_event_count: int = 0
    raw_event_density: float = 0.0
    stable_cluster_count: int = 0
    cluster_density: float = 0.0
    session_count: int = 0
    session_density: float = 0.0
    handoff_count: int = 0
    handoff_density: float = 0.0

    def __repr__(self) -> str:
        return (
            f"Richness({self.value:.3f}, tier={self.tier}, "
            f"events={self.raw_event_count}, clusters={self.stable_cluster_count}, "
            f"sessions={self.session_count}, handoffs={self.handoff_count})"
        )


def _sigmoid(x: float, ref: float) -> float:
    """Smooth saturation curve: 0 when x=0, ~0.55 at x=ref/4, ~0.83 at x=ref.

    Formula: ratio / (ratio + 0.2) where ratio = x / ref.
    This is intentionally simple — one line, no branches, no logs.
    Early growth is calibrated to not over-promise (first few events
    don't spike the score), then saturates beyond reference.

    Reference points (ref=500):
      125 events → 0.556 | 250 → 0.714 | 500 → 0.833 | 1000 → 0.909
    """
    if x <= 0 or ref <= 0:
        return 0.0
    ratio = x / ref
    return round(ratio / (ratio + 0.2), 3)


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
    cutoff = (datetime.now() - timedelta(hours=_RECENT_WINDOW_HOURS)).isoformat()
    if not keywords:
        # Just return most recent outcomes
        rows = conn.execute(
            "SELECT task, tool, success, domain, duration, timestamp "
            "FROM outcomes "
            "WHERE timestamp > ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    else:
        # Build OR conditions for each keyword
        conditions = []
        params: List[Any] = []
        for kw in keywords:
            conditions.append("(task LIKE ? OR tool LIKE ? OR domain LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])

        where_clause = " OR ".join(conditions)
        params.insert(0, cutoff)
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


def compute_richness() -> "RichnessScore":
    """Compute data-density richness score (0.0-1.0).

    Queries the underlying data stores to assess how much Vermes
    knows about this user. No hardcoded thresholds on session count
    — the score emerges from actual data volume.

    Returns a RichnessScore with the overall score (0-1) and
    per-component breakdown for debugging.

    This is the single source of truth for "is Vermes ready to
    inject deeper context?". Every consumer (memory_recall,
    evolution_injector, system_prompt) reads this one signal
    instead of implementing their own gating logic.
    """
    score = RichnessScore()

    # ── Raw events (self-model.db / raw_events table) ──
    try:
        self_db = _get_self_model_db()
        if self_db:
            conn = sqlite3.connect(str(self_db))
            # Total events
            row = conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()
            score.raw_event_count = row[0] if row else 0
            score.raw_event_density = _sigmoid(score.raw_event_count, _REF_RAW_EVENTS)

            # Stable clusters (pattern maturity — emerged, not configured)
            row = conn.execute(
                "SELECT COUNT(*) FROM clusters WHERE lifecycle_stage IN ('stable','declining')"
            ).fetchone()
            score.stable_cluster_count = row[0] if row else 0
            score.cluster_density = _sigmoid(score.stable_cluster_count, _REF_STABLE_CLUSTERS)

            conn.close()
    except Exception:
        pass

    # ── Session count (breadth) ──
    try:
        self_db = _get_self_model_db()
        if self_db:
            conn = sqlite3.connect(str(self_db))
            row = conn.execute(
                "SELECT COUNT(DISTINCT session_id) FROM raw_events"
            ).fetchone()
            score.session_count = row[0] if row else 0
            score.session_density = _sigmoid(score.session_count, _REF_SESSIONS)
            conn.close()
    except Exception:
        pass

    # ── Handoffs (cross-session continuity) ──
    try:
        handoff_db = _get_handoff_db()
        if handoff_db:
            conn = sqlite3.connect(str(handoff_db))
            row = conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()
            score.handoff_count = row[0] if row else 0
            score.handoff_density = _sigmoid(score.handoff_count, _REF_HANDOFFS)
            conn.close()
    except Exception:
        pass

    # ── Weighted composite ──
    score.value = round(
        score.raw_event_density * _W_RAW_EVENTS
        + score.cluster_density * _W_STABLE_CLUSTERS
        + score.session_density * _W_SESSIONS
        + score.handoff_density * _W_HANDOFFS,
        3,
    )

    if score.value < 0.01 and score.raw_event_count == 0:
        # Virgin install — no data at all
        score.tier = "cold_start"
    elif score.value < _RICHNESS_LOW:
        score.tier = "building"
    elif score.value < _RICHNESS_HIGH:
        score.tier = "learning"
    else:
        score.tier = "fluent"

    return score


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


def recall_context(user_message: str) -> Dict[str, Any]:
    """Recall relevant context for the current user message.

    Queries multiple data sources and returns a structured dict.
    Always returns a dict (even if empty) — the richness score is
    computed on every call regardless of keyword matches.
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

    # Source 3: hybrid_retriever rich_search (if embedding API configured)
    try:
        from agent.hybrid_retriever import rich_search as _rich_search
        embedding_results = _rich_search(user_message, top_k=3)
        if embedding_results:
            result["embedding_matches"] = embedding_results
    except Exception:
        pass  # No embedding API or DB — skip silently

    if not result:
        # Even if no keyword match, compute richness for the caller
        pass

    # ── Attach richness score ──
    try:
        richness = compute_richness()
        result["richness"] = richness
        # Scale outcome count by richness: fluent users get more context
        # because the data is accurate; new users get less to avoid noise.
        if richness.tier == "fluent":
            result["_recall_depth"] = "deep"
        elif richness.tier == "learning":
            result["_recall_depth"] = "moderate"
        elif richness.tier == "building":
            result["_recall_depth"] = "shallow"
        else:
            result["_recall_depth"] = "minimal"
    except Exception:
        pass

    result["keywords"] = keywords
    return result


def format_recall_for_prompt(recall: Dict[str, Any]) -> str:
    """Format recalled context as a system prompt block.

    Returns a <recalled_context> block. Empty string if nothing to inject.
    
    Recall depth scales with richness:
      - fluent (richness > 0.6):  deep recall, more context lines
      - learning (0.3-0.6):      moderate recall
      - building (< 0.3):        shallow recall, only keyword match
      - cold_start:              minimal — no meaningful history yet
    """
    richness: Optional[RichnessScore] = recall.get("richness")
    depth = recall.get("_recall_depth", "moderate")

    parts: List[str] = ["<recalled_context>"]

    # Richness header — only shown when richness >= learning
    # Lets Vermes self-calibrate: "I know this user well" vs "still learning"
    if richness and richness.tier in ("fluent", "learning"):
        parts.append(
            f"[Richness: {richness.value:.2f} | "
            f"{richness.raw_event_count} events, "
            f"{richness.stable_cluster_count} stable patterns, "
            f"{richness.session_count} sessions]"
        )

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

    Returns empty string if no meaningful recall data available
    (richness score alone is not considered meaningful recall — it's
    metadata, not context to inject).
    """
    recall = recall_context(user_message)
    # Only format if there's actual recall data (outcomes, domains, etc.)
    # The richness score alone doesn't warrant a <recalled_context> block.
    has_content = any(
        k in recall for k in ("recent_outcomes", "domain_stats", "emotion", "embedding_matches")
    )
    if not has_content:
        return ""
    return format_recall_for_prompt(recall)
