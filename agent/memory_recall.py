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

from harness.metrics import get_metrics

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


def _get_vermes_home() -> Path:
    return Path(os.environ.get("VERMES_HOME") or os.path.expanduser("~/.vermes"))


def _get_self_model_db() -> Optional[Path]:
    """Resolve the self-model DB path."""
    db = _get_vermes_home() / "evolution" / "self-model.db"
    return db if db.exists() else None


def _get_fusion_db() -> Optional[Path]:
    """Resolve the fusion-state DB path."""
    db = _get_vermes_home() / "evolution" / "fusion-state.db"
    return db if db.exists() else None


def _get_handoff_db() -> Optional[Path]:
    """Resolve the session handoff DB path."""
    db = _get_vermes_home() / "session_handoffs.db"
    return db if db.exists() else None


# 中文高频虚词/代词——参与切分会稀释主题词元
_CJK_STOP_CHARS = set(
    "的了是在我你他她它们这那个一不有和人就都而与或但如果因为所以"
    "吗吧呢啊呀哦嗯把被让给对从向到过着为之其此该等则也还很更再又"
)


def _extract_chinese_tokens(text: str, sizes: tuple = (2, 3)) -> List[str]:
    """重叠 n-gram 滑窗切分中文（保留重复以便词频打分）。

    旧实现是 ``re.findall(r'[\\u4e00-\\u9fff]{2,4}')`` —— 那是**非重叠定长切窗**，
    不是分词：切窗起点由文本前缀决定，同一短语在写入侧和查询侧会落到不同偏移
    上，交集恒为空（实测 5 条中文改写提问 5/5 零命中）。重叠滑窗保证任意 2/3 字
    连续子串在两侧都会被产出，从而可以相交。

    停用字在切分前剔除，两侧做同样变换，故一致性不受影响。
    """
    tokens: List[str] = []
    for seg in re.findall(r'[\u4e00-\u9fff]+', text or ""):
        filtered = ''.join(c for c in seg if c not in _CJK_STOP_CHARS)
        for size in sizes:
            for i in range(len(filtered) - size + 1):
                tokens.append(filtered[i:i + size])
    return tokens


def _extract_keywords(message: str, max_keywords: int = 5) -> List[str]:
    """Extract meaningful keywords from user message.

    Filters out common stop words and short tokens.
    """
    if not message:
        return []

    # Remove markdown/formatting
    clean = re.sub(r'[*_`#\[\]()]', ' ', message)

    # Split into words (support both Chinese and English)
    # Chinese: overlapping 2/3-char n-gram sliding window (see above)
    # English: extract words >= 3 chars
    chinese_tokens = _extract_chinese_tokens(clean)
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

    # CJK 重叠 n-gram 的词元密度约为字符数的 2 倍，远高于英文分词。沿用英文的
    # 小额度会让长句只有开头一小段进入索引（实测「进行的重大的改造」整段被截断，
    # 导致「重大改造」类提问零命中）。故按 CJK 字符量自适应放宽，上限 20 —— 下游
    # ``_query_recent_outcomes`` 每个词元展开 3 个 LIKE，20 即 60 个子句，是
    # 召回覆盖与 OR 爆炸之间的平衡点。英文路径不受影响。
    cjk_len = sum(1 for ch in clean if '\u4e00' <= ch <= '\u9fff')
    effective_max = max_keywords
    if cjk_len:
        effective_max = min(20, max(max_keywords, cjk_len))

    return sorted_tokens[:effective_max]


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
            "FROM v_outcomes "
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
            f"FROM v_outcomes "
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
        f"FROM v_outcomes "
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
    except Exception as e:
        logger.debug("memory_recall: raw_events richness query failed: %s", e)

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
    except Exception as e:
        logger.debug("memory_recall: session count richness query failed: %s", e)

    # ── Handoffs (cross-session continuity) ──
    try:
        handoff_db = _get_handoff_db()
        if handoff_db:
            conn = sqlite3.connect(str(handoff_db))
            row = conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()
            score.handoff_count = row[0] if row else 0
            score.handoff_density = _sigmoid(score.handoff_count, _REF_HANDOFFS)
            conn.close()
    except Exception as e:
        logger.debug("memory_recall: handoff richness query failed: %s", e)

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


def _collect_relation_snippets(conn: sqlite3.Connection, limit: int = 3) -> List[Dict[str, Any]]:
    """查 relations 表，通过 outcomes 桥接取 strategy/anti_pattern 内容。

    桥接路径：recent outcomes → relations(source_id=outcome.id) → target strategies/anti_patterns
    仅取 target_type IN ('strategy','anti_pattern')——emotional_state 表不存在（1963 条幽灵）。
    """
    snippets = []
    try:
        # 取最近 N 条 outcomes 的 id
        recent_outcome_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM outcomes ORDER BY id DESC LIMIT 20"
            ).fetchall()
        ]
        if not recent_outcome_ids:
            return snippets

        placeholders = ",".join("?" * len(recent_outcome_ids))
        # 查 relations：outcome → strategy/anti_pattern
        rels = conn.execute(
            f"""SELECT r.target_type, r.target_id, r.rel_type, r.weight,
                      r.source_id as outcome_id
               FROM relations r
               WHERE r.source_type = 'outcome'
                 AND r.source_id IN ({placeholders})
                 AND r.target_type IN ('strategy', 'anti_pattern')
               ORDER BY r.source_id DESC
               LIMIT ?""",
            recent_outcome_ids + [limit],
        ).fetchall()

        for rel in rels:
            if rel["target_type"] == "strategy":
                row = conn.execute(
                    "SELECT strategy, success_rate_when_used, times_used FROM strategies WHERE id = ?",
                    (rel["target_id"],),
                ).fetchone()
                if row and row["strategy"]:
                    snippets.append({
                        "type": "strategy",
                        "content": row["strategy"],
                        "success_rate": row["success_rate_when_used"],
                        "times_used": row["times_used"],
                        "rel": rel["rel_type"],
                    })
            elif rel["target_type"] == "anti_pattern":
                row = conn.execute(
                    "SELECT pattern, correct, domain, frequency FROM anti_patterns WHERE id = ?",
                    (rel["target_id"],),
                ).fetchone()
                if row and row["pattern"]:
                    snippets.append({
                        "type": "anti_pattern",
                        "content": row["pattern"],
                        "correct": row["correct"],
                        "domain": row["domain"],
                        "frequency": row["frequency"],
                        "rel": rel["rel_type"],
                    })
    except Exception as e:
        logger.debug("_collect_relation_snippets failed: %s", e)

    return snippets


def _collect_recall_sections(user_message: str) -> Dict[str, Any]:
    """Pure read of the recall data sources — NO self-assessment write.

    Extracted so the live L3 adapter (``recall_context_as_hits``) reuses the
    exact retrieval path as the per-turn prompt injection *without* the side
    effect of recording a self-assessment raw_event on every search (which
    would pollute the recall subsystem's own evaluation data). The only
    difference from ``recall_context`` is the absence of ``assess_and_record``
    and the richness/depth decoration (not needed by the search hit list).
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

    # Source 2b: relations from self-model DB (session_id bridge)
    # 桥接：当前无 session_id 上下文 → 用最近 outcomes → relations → strategies/anti_patterns
    # 仅取 target_type IN ('strategy','anti_pattern')（915/2878=32%，emotional_state 表不存在）
    if self_model_db:
        try:
            conn = sqlite3.connect(str(self_model_db))
            conn.row_factory = sqlite3.Row
            relation_snippets = _collect_relation_snippets(conn, limit=3)
            if relation_snippets:
                result["relation_snippets"] = relation_snippets
            conn.close()
        except Exception as e:
            logger.debug("memory_recall: relations query failed: %s", e)

    # Source 2c: session handoffs (L3 episodic — 质量地板)
    # handoff 摘要是「会话发生过什么」的情节记录，天然是 episodic
    # 质量地板：长度≥80 字符 + 排除纯测试噪声（「通讯正常不」/「你好」开头）
    handoff_db = _get_handoff_db()
    if handoff_db:
        try:
            conn = sqlite3.connect(str(handoff_db))
            conn.row_factory = sqlite3.Row
            for row in conn.execute(
                "SELECT session_id, summary_text FROM session_handoffs "
                "ORDER BY id DESC LIMIT 5"
            ).fetchall():
                text = row["summary_text"] or ""
                # 质量地板
                if len(text) < 80:
                    continue
                if text.startswith("上次会话主题: 通讯正常") or text.startswith("上次会话主题: 你好"):
                    continue
                result.setdefault("handoff_snippets", []).append({
                    "session_id": row["session_id"],
                    "content": text,
                    "id": f"handoff:{row['session_id']}",
                })
            conn.close()
        except Exception as e:
            logger.debug("memory_recall: handoff query failed: %s", e)

    # Source 3: hybrid_retriever rich_search (if embedding API configured)
    try:
        from agent.hybrid_retriever import rich_search as _rich_search
        embedding_results = _rich_search(user_message, top_k=3)
        if embedding_results:
            result["embedding_matches"] = embedding_results
    except Exception as e:
        logger.debug("memory_recall: embedding search skipped: %s", e)

    result["keywords"] = keywords
    return result


def recall_context(user_message: str) -> Dict[str, Any]:
    """Recall relevant context for the current user message.

    Queries multiple data sources and returns a structured dict.
    Always returns a dict (even if empty) — the richness score is
    computed on every call regardless of keyword matches.

    This is the per-turn PROMPT-INJECTION path: it intentionally records a
    self-assessment raw_event (``assess_and_record``) so Vermes can observe
    its own retrieval quality. The live L3 SEARCH adapter
    (``recall_context_as_hits``) uses the side-effect-free
    ``_collect_recall_sections`` instead, so ``memory_search`` never pollutes
    the recall subsystem's evaluation data.
    """
    result = _collect_recall_sections(user_message)
    keywords = result.get("keywords", [])

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
    except Exception as e:
        logger.debug("memory_recall: recall depth assessment failed: %s", e)

    # ── Self-assessment: record recall quality as raw_event ──
    # This is the emergence trigger foundation — the system observes
    # its own retrieval quality on every turn. When bottleneck signals
    # accumulate, clustering groups them and emergent_insight surfaces
    # the pattern. No hardcoded triggers, no thresholds to flip switches.
    # The system discovers its own limitations from data.
    try:
        from agent.self_assessment import assess_and_record
        assess_and_record(result, keywords, session_id="", turn_number=0)
    except Exception as e:
        logger.debug("memory_recall: self_assessment recording failed: %s", e)

    return result


def recall_context_as_hits(user_message: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Flatten episodic recall into the unified L3 hit shape — side-effect-free.

    Used as the live adapter behind ``memory_fabric.set_l3_live_hook`` so the
    agent's episodic recall (self-model outcomes, fusion emotion, embedding
    matches) participates in the unified hierarchical recall pipeline. The
    fabric stays format-agnostic — this adapter is the only place that knows
    the recall subsystem's internal dict shape.

    Crucially this calls the PURE read path (``_collect_recall_sections``),
    NOT ``recall_context`` — so a ``memory_search`` query does NOT trigger the
    self-assessment ``assess_and_record`` raw_event write that the per-turn
    prompt-injection path performs. Otherwise every search would corrupt the
    recall subsystem's own evaluation data.

    fail-soft: any error yields an empty list (the agent must never be
    interrupted by a broken recall subsystem).
    """
    try:
        ctx = _collect_recall_sections(user_message) or {}
    except Exception:
        return []
    hits: List[Dict[str, Any]] = []
    section_sources = {
        "recent_outcomes": "recall:outcomes",
        "domain_stats": "recall:domain",
        "emotion": "recall:emotion",
        "embedding_matches": "recall:embedding",
        "handoff_snippets": "recall:handoff",
        "relation_snippets": "recall:relations",
    }
    for key, src in section_sources.items():
        val = ctx.get(key)
        if not val:
            continue
        items = val if isinstance(val, list) else [val]
        for i, item in enumerate(items[:limit]):
            if isinstance(item, dict):
                content = (
                    item.get("content")
                    or item.get("text")
                    or item.get("summary")
                    or item.get("excerpt")
                    or str(item)
                )
                pid = item.get("id") or f"{src}:{i}"
                try:
                    score = float(item.get("score") or item.get("similarity") or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
            else:
                content = str(item)
                pid = f"{src}:{i}"
                score = 0.0
            hits.append(
                {
                    "layer": "episodic",
                    "source": src,
                    "pointer": pid,
                    "content": content if isinstance(content, str) else str(content),
                    "score": score,
                }
            )
    return hits[:limit]


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


def refine_recall_per_turn(user_message: str) -> str:
    """Per-turn recall refinement for the CURRENT user message (H4.5).

    Unlike ``recall_context()`` (run once at turn 1 and injected into the
    frozen system prompt), this is meant to be injected into the *per-turn
    user message* so the stable prefix cache is never disturbed.

    Token/latency budget (risk R-mem2) is bounded by design:
      - ``fluent`` users already got full context at turn 1 → skip entirely.
      - Only a lightweight DB recall (recent outcomes + domain stats) is run;
        no embedding search, no self-assessment raw_event write.
      - Empty string when there is nothing relevant to add (fail-open).

    Returns a ``<recalled_context>`` block, or ``""`` if nothing to add.
    """
    try:
        richness = compute_richness()
    except Exception:
        return ""
    if getattr(richness, "tier", "") == "fluent":
        return ""  # 系统提示已全量注入，逐轮细化跳过以省 token

    keywords = _extract_keywords(user_message or "")
    result: Dict[str, Any] = {}

    self_model_db = _get_self_model_db()
    if self_model_db:
        try:
            conn = sqlite3.connect(str(self_model_db))
            conn.row_factory = sqlite3.Row
            recent = _query_recent_outcomes(conn, keywords)
            if recent:
                result["recent_outcomes"] = recent
            domain = _query_domain_stats(conn, keywords)
            if domain:
                result["domain_stats"] = domain
            conn.close()
        except Exception as e:
            logger.debug("memory_recall: refine_recall domain stats query failed: %s", e)

    if not result:
        return ""  # 无相关内容，不注入

    result["richness"] = richness
    result["keywords"] = keywords
    result["_recall_depth"] = "shallow"  # 逐轮细化用浅召回，省 token
    try:
        return format_recall_for_prompt(result)
    except Exception:
        return ""


def recall_hierarchical_per_turn(user_message: str) -> str:
    """Per-turn layered recall (audit B2): surface L1–L4 via
    ``memory_fabric.recall_hierarchical`` on *every* turn, so unified memory is
    proactively available regardless of whether the model decides to call the
    ``memory_search`` tool.

    Injected into the per-turn user message (NOT the system prompt) to keep the
    prompt-cache prefix intact. Fail-open (any error returns ``""``) and
    token-bounded (``_MAX_BLOCK_CHARS``).

    Fluent users already received full episodic (L3) context at turn 1 via the
    frozen system prompt, so we skip L3 for them to save tokens — but we still
    surface L1 (notes) / L2 (skills) / L4 (reference), which are never in the
    system prompt. Non-fluent users get the full L1–L4 stack.

    Ordering/de-dup inside each layer is handled by ``recall_hierarchical``
    (layer priority first, then FTS5 rank; de-duplicated by ``{source}#{id}``
    pointer + content fingerprint).
    """
    try:
        from agent.memory_fabric import (
            L1_NOTE,
            L2_PROCEDURAL,
            L4_REFERENCE,
            recall_hierarchical,
        )
    except Exception:
        return ""

    try:
        _rich = compute_richness()
        _fluent = getattr(_rich, "tier", "") == "fluent"
    except Exception as e:
        logger.debug("memory_recall: richness tier check failed: %s", e)
        _fluent = False

    # Fluent users skip L3 (already in the turn-1 system prompt).
    _layers = None if not _fluent else [L1_NOTE, L2_PROCEDURAL, L4_REFERENCE]

    try:
        hits = recall_hierarchical(
            user_message or "", limit=6, layers=_layers,
            prioritize_tags=["decision", "preference"],
        )
    except Exception:
        logger.warning("recall_hierarchical_per_turn failed (non-fatal)", exc_info=True)
        return ""

    if not hits:
        get_metrics().record_per_turn(hits_total=0)
        return ""

    _LABELS = {
        L1_NOTE: "note",
        L2_PROCEDURAL: "skill",
        "episodic": "recall",
        L4_REFERENCE: "reference",
    }
    parts = ["<memory_recall>"]
    for h in hits:
        _label = _LABELS.get(h.get("layer", ""), h.get("layer", ""))
        _content = (h.get("content") or "").strip()
        _tag = h.get("lifecycle_tag", "")
        if _content:
            # Route E P5: 注入 lifecycle_tag 标注，让 LLM 区分记忆类型
            if _tag and _tag != "reference":
                parts.append(f"[{_label}@{_tag}] {_content}")
            else:
                parts.append(f"[{_label}] {_content}")
    parts.append("</memory_recall>")
    block = "\n".join(parts)

    if len(block) > _MAX_BLOCK_CHARS:
        block = block[:_MAX_BLOCK_CHARS] + "\n</memory_recall>"

    _inner = block.replace("<memory_recall>", "").replace("</memory_recall>", "").strip()
    if not _inner:
        get_metrics().record_per_turn(hits_total=0)
        return ""
    get_metrics().record_per_turn(hits_total=len(hits))
    return block
