"""Unified Session Continuity Facade (Route C-1).

Consolidates four independent cross-session injection paths into a single
entry point:

  1. session_handoff.load_handoff_for_new_session  → handoff_store
  2. evolution_injector.load_and_format_evolution  → evolution DB
  3. memory_recall.load_and_format_recall          → memory_fabric L3
  4. cross_session_continuity.get_continuity_prompt → cluster snapshots

Design principles:
  - Single entry: ``load_continuity_context(user_message)`` returns a
    ``ContinuityContext`` dataclass with all four blocks + metadata.
  - Fail-open: each source is independently try/excepted; failures in one
    do not block others. Errors are logged, not swallowed silently.
  - Backward compatible: the existing ``conversation_loop.py`` turn-1
    injection can be replaced by a single call to this facade without
    changing any downstream prompt assembly.
  - No prompt structure changes: the facade returns the same text blocks
    that were previously set as ``agent._handoff_context``, etc.

Route C-1 from Vermes Harness Baseline Audit (revised 2026-07-22).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ContinuityContext:
    """Aggregated cross-session continuity context for turn-1 injection."""

    handoff_block: str = ""
    evolution_block: str = ""
    recall_block: str = ""
    continuity_block: str = ""

    # Metadata for debugging / observability
    sources_loaded: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)
    total_events_since: int = 0
    capacity_status: dict = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """True if all four blocks are empty (cold start)."""
        return not any([
            self.handoff_block,
            self.evolution_block,
            self.recall_block,
            self.continuity_block,
        ])

    def to_prompt_sections(self) -> dict[str, str]:
        """Return non-empty blocks keyed by section name.

        Keys match the attribute names used by conversation_loop.py:
        ``handoff_context``, ``evolution_context``, ``recall_context``,
        ``continuity_context``.
        """
        sections: dict[str, str] = {}
        if self.handoff_block:
            sections["handoff_context"] = self.handoff_block
        if self.evolution_block:
            sections["evolution_context"] = self.evolution_block
        if self.recall_block:
            sections["recall_context"] = self.recall_block
        if self.continuity_block:
            sections["continuity_context"] = self.continuity_block
        return sections

    def summary(self) -> str:
        """One-line summary for logging."""
        parts = []
        if self.handoff_block:
            parts.append(f"handoff({len(self.handoff_block)}c)")
        if self.evolution_block:
            parts.append(f"evolution({len(self.evolution_block)}c)")
        if self.recall_block:
            parts.append(f"recall({len(self.recall_block)}c)")
        if self.continuity_block:
            parts.append(f"continuity({len(self.continuity_block)}c)")
        if self.sources_failed:
            parts.append(f"failed={','.join(self.sources_failed)}")
        return " | ".join(parts) if parts else "empty"


def load_continuity_context(
    user_message: str = "",
    *,
    db_path: str = "",
) -> ContinuityContext:
    """Load all cross-session continuity sources and return aggregated context.

    This is the single entry point for turn-1 continuity injection.
    Each source is independently fail-open; failures are logged.

    Args:
        user_message: The first user message of the new session.
        db_path: Optional DB path for cross_session_continuity. If empty,
            the function resolves it internally.

    Returns:
        ContinuityContext with all available blocks populated.
    """
    ctx = ContinuityContext()

    # 1. Session handoff (handoff_store)
    try:
        from agent.session_handoff import (
            load_handoff_for_new_session,
            format_handoff_for_prompt,
        )
        handoff = load_handoff_for_new_session(user_message)
        if handoff:
            ctx.handoff_block = format_handoff_for_prompt(handoff)
        ctx.sources_loaded.append("handoff")
    except Exception as e:
        logger.warning("Continuity facade: handoff source failed: %s", e)
        ctx.sources_failed.append("handoff")

    # 2. Evolution injection
    try:
        from agent.evolution_injector import load_and_format_evolution
        block = load_and_format_evolution(user_message)
        if block:
            ctx.evolution_block = block
        ctx.sources_loaded.append("evolution")
    except Exception as e:
        logger.warning("Continuity facade: evolution source failed: %s", e)
        ctx.sources_failed.append("evolution")

    # 3. Memory recall (L3)
    try:
        from agent.memory_recall import load_and_format_recall
        block = load_and_format_recall(user_message)
        if block:
            ctx.recall_block = block
        ctx.sources_loaded.append("recall")
    except Exception as e:
        logger.warning("Continuity facade: recall source failed: %s", e)
        ctx.sources_failed.append("recall")

    # 4. Cross-session continuity (cluster snapshots)
    try:
        from agent.cross_session_continuity import get_continuity_prompt
        resolved_db = db_path
        if not resolved_db:
            try:
                from agent.memory_recall import _get_self_model_db
                resolved_db = str(_get_self_model_db() or "")
            except Exception as e:
                logger.debug("Continuity facade: self_model_db resolve failed: %s", e)
        block = get_continuity_prompt(resolved_db)
        if block:
            ctx.continuity_block = block
        ctx.sources_loaded.append("continuity")
    except Exception as e:
        logger.warning("Continuity facade: continuity source failed: %s", e)
        ctx.sources_failed.append("continuity")

    # 5. Compression handoff recall (Route E P3)
    #    Recall compression summaries from memory_fabric for the current scope.
    try:
        from agent.memory_fabric import recall as _mf_recall
        # 用 tag_filter 查 volatile 标签的记忆，回退到纯 tag 查询
        _handoff_results = _mf_recall(
            "compression handoff summary context",
            limit=3,
            tag_filter=["volatile"],
        )
        _handoff_hits = [
            h for h in _handoff_results
            if h.get("source") == "compression"
            and h.get("type") == "compression_handoff"
        ]
        if _handoff_hits:
            _lines = ["📎 压缩上下文快照（来自上次压缩交割）："]
            for h in _handoff_hits[:2]:
                _content = (h.get("content") or "")[:500]
                _lines.append(f"  • [{h.get('scope', '?')}] {_content}")
            ctx.recall_block = (ctx.recall_block or "") + "\n" + "\n".join(_lines)
            ctx.sources_loaded.append("compression_handoff")
            logger.debug("Continuity facade: loaded %d compression handoff(s)",
                         len(_handoff_hits))
    except Exception as e:
        logger.warning("Continuity facade: compression handoff source failed: %s", e)
        ctx.sources_failed.append("compression_handoff")

    # 6. Reflection flags (Route E-Reflection R3)
    #    Inject open flags from memory_flags table so the agent is aware of
    #    potential memory issues (contradictions, outdated info, etc.).
    try:
        from agent.memory_reflection import get_open_flags, format_flags_for_context
        flags = get_open_flags(limit=10)
        if flags:
            block = format_flags_for_context(flags)
            if block:
                ctx.recall_block = (ctx.recall_block or "") + "\n" + block
                ctx.sources_loaded.append("reflection_flags")
                logger.debug("Continuity facade: loaded %d reflection flags", len(flags))
    except Exception as e:
        logger.warning("Continuity facade: reflection flags source failed: %s", e)
        ctx.sources_failed.append("reflection_flags")

    # 7. ScholarForge project handoff (Phase 3)
    #    Inject active paper project state so the agent can resume work.
    try:
        from hermes_cli.scholarforge.project_context import format_active_projects_prompt
        block = format_active_projects_prompt()
        if block:
            ctx.handoff_block = (ctx.handoff_block or "") + "\n" + block
            ctx.sources_loaded.append("project_handoff")
            logger.debug("Continuity facade: loaded project handoff")
    except Exception as e:
        logger.warning("Continuity facade: project handoff source failed: %s", e)
        ctx.sources_failed.append("project_handoff")

    if ctx.is_empty:
        logger.debug("Continuity facade: cold start (no blocks)")
    else:
        logger.info("Continuity facade loaded: %s", ctx.summary())

    # ── Route D metrics ──
    try:
        from agent.metrics import record_continuity_load
        record_continuity_load(
            sources_loaded=ctx.sources_loaded,
            sources_failed=ctx.sources_failed,
        )
    except Exception as e:
        logger.debug("Continuity facade: metrics collection failed: %s", e)

    # ── B 硬容量护栏：返回容量状态 ──
    try:
        from agent.memory_fabric import _check_capacity
        cap = _check_capacity()
        ctx.capacity_status = {
            "total_memories": cap["total_count"],
            "over_capacity": cap["over_capacity"],
            "limit_scale": cap["limit_scale"],
        }
    except Exception as e:
        logger.debug("Continuity facade: capacity check failed: %s", e)

    return ctx
