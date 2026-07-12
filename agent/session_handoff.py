"""Session handoff layer — cross-session task continuity.

Generates structured summaries at session end (deterministic extraction,
no LLM calls), loads them at new session start, and formats them for
system prompt injection.

Design decisions:
  - Deterministic extraction > LLM summarization (mirrors fatigue bridge)
  - No API calls, no latency, no cost
  - Best-effort: failures never block the main loop
  - Structured fields + free-text summary
  - Optional LLM enhancement via background_review (future)

Extraction strategy:
  1. User request: first user message (the primary task)
  2. Tools used: aggregate tool_call entries (name + success + count)
  3. Decisions: assistant messages containing decision keywords
  4. Pending tasks: TODO/下一步/待办 in last assistant message
  5. Open questions: ? in assistant messages
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from agent.handoff_store import store_handoff, get_global_latest_handoff

logger = logging.getLogger(__name__)

# ── Decision/pending extraction uses structural heuristics ──
# No hardcoded language-specific keywords. The fork agent (_DECISION_REVIEW_PROMPT)
# handles deep decision extraction. These lightweight extractors use generic
# sentence-structure patterns that work across languages.

# Structural decision indicators (cross-language): colon/conclusion markers,
# arrow patterns, code blocks, tool-result patterns
_DECISION_STRUCTURAL = [
    r'(?:使用|采用|选择|决定|决定用|方案是|策略是|结论[是为:]|综上).{3,80}[。\n]',
    r'(?:use|using|adopt|choose|decided|going with|will use|conclusion:).{3,120}[.\n]',
    r'(?:→|->|=>).{3,80}',  # Arrow pattern: "→用Postgres"
]

# Task/pending indicators: list items that end with action verbs or
# look like future plans (works across many languages via structural cues)
_PENDING_STRUCTURAL = [
    r'^\s*[-*•]\s*.{5,200}',  # Bullet-point items
    r'(?:next|下一步|接下来|还需要|待办|pending|todo|remaining).{3,120}',
]


def generate_and_store_handoff(
    messages: List[Dict[str, Any]],
    session_id: str = "",
) -> int:
    """Generate a handoff summary from messages and store it.

    Called at session end (shutdown_memory_provider / commit_memory_session).
    Returns the handoff row id, or -1 on failure.
    """
    if not messages:
        return -1

    try:
        user_request = _extract_user_request(messages)
        tools_used = _extract_tools_used(messages)
        decisions = _extract_decisions(messages)
        pending_tasks = _extract_pending_tasks(messages)
        open_questions = _extract_open_questions(messages)
        summary_text = _build_summary_text(
            user_request, tools_used, decisions, pending_tasks, open_questions
        )

        # Extract keywords for relevance matching
        try:
            from agent.memory_recall import _extract_keywords
            combined = user_request + " " + summary_text
            keywords = _extract_keywords(combined, max_keywords=8)
        except Exception:
            keywords = []

        handoff_id = store_handoff(
            session_id,
            user_request=user_request,
            tools_used=tools_used,
            decisions=decisions,
            pending_tasks=pending_tasks,
            open_questions=open_questions,
            summary_text=summary_text,
            keywords=keywords,
        )

        # ── 写入 embedding DB ────────────────────────────────────────
        if handoff_id >= 0:
            try:
                from agent.hybrid_retriever import store_embedding
                emb_content = f"Session: {session_id} | Task: {user_request} | Summary: {summary_text}"
                store_embedding(emb_content, target=f"handoff:{session_id}")
            except Exception as emb_err:
                logger.debug("store_embedding for handoff skipped: %s", emb_err)

        return handoff_id
    except Exception as e:
        logger.warning("Handoff generation failed: %s", e)
        return -1


def load_handoff_for_new_session(
    user_message: str = "",
) -> Optional[Dict[str, Any]]:
    """Load the most relevant handoff for a new session.

    Called at new session start (turn 1, before on_turn_start).
    Uses keyword matching to find the most relevant handoff when multiple
    parallel sessions exist.

    Returns the handoff dict or None if no handoff exists.
    """
    try:
        # Use relevance-based lookup instead of just latest
        from agent.handoff_store import get_relevant_handoff
        handoff = get_relevant_handoff(user_message, max_age_days=7)
        if not handoff:
            return None
        # Don't load if the handoff is too old
        import time
        age = time.time() - handoff.get("created_at", 0)
        if age > 7 * 86400:
            logger.debug("Skipping handoff: %d days old", int(age / 86400))
            return None
        logger.debug(
            "Loaded handoff from session %s (%.1f hours ago): %s",
            handoff.get("session_id", "?"),
            age / 3600,
            handoff.get("summary_text", "")[:80],
        )
        return handoff
    except Exception as e:
        logger.warning("Failed to load handoff: %s", e)
        return None


def format_handoff_for_prompt(handoff: Dict[str, Any]) -> str:
    """Format a handoff dict into a system prompt block."""
    lines = ["<previous_session_summary>"]

    summary = handoff.get("summary_text", "")
    if summary:
        lines.append(summary)

    tools = handoff.get("tools_used", [])
    if tools:
        lines.append("\n上次使用工具:")
        for t in tools[:5]:  # top 5
            status = "✓" if t.get("success") else "✗"
            lines.append(f"  {status} {t.get('name', '?')} (×{t.get('count', 1)})")

    pending = handoff.get("pending_tasks", [])
    if pending:
        lines.append("\n待办事项:")
        for p in pending[:5]:
            lines.append(f"  • {p.get('task', '')}")

    questions = handoff.get("open_questions", [])
    if questions:
        lines.append("\n开放问题:")
        for q in questions[:3]:
            lines.append(f"  ? {q}")

    lines.append("</previous_session_summary>")
    return "\n".join(lines)


# ── Deterministic extractors ─────────────────────────────────────────────


def _extract_user_request(messages: List[Dict[str, Any]]) -> str:
    """Extract the primary user request (first user message)."""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                # Strip skill injections and get the actual user text
                # (skill injections start with specific markers)
                if content.startswith("<"):
                    # Try to find the actual user text after XML tags
                    match = re.search(r"</[^>]+>\s*(.+)", content, re.DOTALL)
                    if match:
                        return match.group(1).strip()[:500]
                return content[:500]
    return ""


def _extract_tools_used(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate tool calls with success/failure counts."""
    tool_stats: Dict[str, Dict[str, Any]] = {}
    for msg in messages:
        tool_name = msg.get("tool_name")
        if not tool_name:
            continue
        if tool_name not in tool_stats:
            tool_stats[tool_name] = {
                "name": tool_name,
                "count": 0,
                "success": 0,
                "failed": 0,
            }
        tool_stats[tool_name]["count"] += 1
        # Heuristic: if the tool result content starts with "Error" or
        # the message has an error field, count as failed
        content = msg.get("content", "")
        if isinstance(content, str) and (
            content.lower().startswith("error")
            or "traceback" in content.lower()
        ):
            tool_stats[tool_name]["failed"] += 1
        else:
            tool_stats[tool_name]["success"] += 1

    # Return sorted by count descending
    return sorted(tool_stats.values(), key=lambda x: x["count"], reverse=True)


def _extract_decisions(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract decisions from assistant messages using structural patterns.

    Uses regex patterns that capture decision-like sentence structures
    (colon/conclusion markers, arrow symbols, tool-result patterns).
    Works across languages — no hardcoded zh/en keyword lists.
    """
    decisions = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        for pattern in _DECISION_STRUCTURAL:
            for m in re.finditer(pattern, content, re.IGNORECASE):
                sentence = m.group().strip()
                if sentence and len(sentence) < 300:
                    decisions.append({"decision": sentence})
                    break
            if decisions and decisions[-1].get("decision") == sentence:
                break  # one decision per message
    return decisions[:10]


def _extract_pending_tasks(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract pending tasks from assistant messages using structural cues.

    Uses bullet-point patterns and future-plan markers that work
    across languages, not zh/en-only keyword lists.
    """
    tasks = []
    assistant_msgs = [
        m for m in messages if m.get("role") == "assistant"
    ][-5:]
    for msg in assistant_msgs:
        content = msg.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        for pattern in _PENDING_STRUCTURAL:
            for m in re.finditer(pattern, content, re.IGNORECASE):
                task_text = m.group().strip().lstrip("-*• ").strip()
                if task_text and len(task_text) < 200:
                    tasks.append({"task": task_text})
    # Deduplicate
    seen = set()
    unique_tasks = []
    for t in tasks:
        if t["task"] not in seen:
            seen.add(t["task"])
            unique_tasks.append(t)
    return unique_tasks[:10]


def _extract_open_questions(messages: List[Dict[str, Any]]) -> List[str]:
    """Extract open questions from assistant messages."""
    questions = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        # Find sentences ending with ? or？ that look like questions
        # Use a broader pattern: any text ending with a question mark
        for match in re.finditer(r'[^\n。]{5,}[？?]', content):
            q = match.group().strip()
            if q and len(q) < 200 and q not in questions:
                questions.append(q)
    return questions[:5]


def _build_summary_text(
    user_request: str,
    tools_used: List[Dict[str, Any]],
    decisions: List[Dict[str, Any]],
    pending_tasks: List[Dict[str, Any]],
    open_questions: List[str],
) -> str:
    """Build a free-text summary from extracted fields."""
    parts = []
    if user_request:
        parts.append(f"上次会话主题: {user_request[:200]}")
    if decisions:
        parts.append(f"关键决策: {decisions[0]['decision'][:150]}")
    if pending_tasks:
        parts.append(f"未完成: {pending_tasks[0]['task'][:150]}")
    if not parts:
        # Fallback: use tool count as a signal of what was done
        if tools_used:
            top_tool = tools_used[0]
            parts.append(
                f"上次主要使用 {top_tool['name']} ({top_tool['count']}次)"
            )
        else:
            parts.append("(无显著内容)")
    return " | ".join(parts)
