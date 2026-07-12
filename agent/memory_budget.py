"""Memory budget — unified token budget for all memory injections.

Controls the total token cost of all memory blocks injected into the
system prompt volatile tier:
  1. Session handoff (~200 tokens)
  2. Evolution context (~160 tokens)
  3. Memory recall (~400 tokens)
  4. Active decisions (~200 tokens)

Total budget: ~1000 tokens (4000 chars)

When total exceeds budget, lower-priority blocks are truncated or dropped.
Priority order (highest first):
  1. recall_context (most relevant to current turn)
  2. handoff_context (essential for session continuity)
  3. evolution_context (learned experience)
  4. active_decisions (standing decisions)

Design:
  - Post-hoc trimming: each block generates independently, then budget
    manager trims if total exceeds budget
  - Graceful degradation: truncate from lowest priority, not drop entirely
  - Token estimate: chars / 4 (rough approximation)
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Total budget for all memory injections
_TOTAL_BUDGET_CHARS = 4000  # ~1000 tokens

# Per-block priority (lower number = higher priority)
# Block is identified by the attribute name on the agent object
_BLOCK_PRIORITIES: Dict[str, int] = {
    "_recall_context": 1,        # highest — most relevant to current turn
    "_handoff_context": 2,       # essential for session continuity
    "_evolution_context": 3,     # learned experience
    "_decisions_context": 4,     # standing decisions (injected by system_prompt)
}

# Per-block soft cap (chars) — blocks should not exceed this individually
_BLOCK_SOFT_CAPS: Dict[str, int] = {
    "_recall_context": 1600,
    "_handoff_context": 1200,
    "_evolution_context": 1000,
    "_decisions_context": 800,
}


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: chars / 4."""
    return len(text) // 4


def _trim_block(text: str, max_chars: int) -> str:
    """Trim a block to fit within max_chars.

    Preserves the opening/closing XML tags if present.
    """
    if len(text) <= max_chars:
        return text

    # Find XML tags
    import re
    tag_match = re.match(r'^(\s*<\w+>)(.*?)(<\/\w+>\s*)$', text, re.DOTALL)
    if tag_match:
        open_tag, content, close_tag = tag_match.groups()
        # Reserve space for tags + ellipsis
        available = max_chars - len(open_tag) - len(close_tag) - 20
        if available > 20:
            trimmed_content = content[:available].rsplit('\n', 1)[0].rstrip()
            return f"{open_tag}{trimmed_content}\n... (trimmed)\n{close_tag}"

    # Fallback: simple truncation
    return text[:max_chars - 20] + "\n... (trimmed)"


def apply_budget(blocks: Dict[str, str]) -> Dict[str, str]:
    """Apply token budget to memory blocks.

    Args:
        blocks: dict of {attr_name: block_text}

    Returns:
        dict of {attr_name: trimmed_block_text}
        May contain fewer keys if a block is dropped entirely.
    """
    if not blocks:
        return {}

    # Filter out empty blocks
    active_blocks = {k: v for k, v in blocks.items() if v and v.strip()}
    if not active_blocks:
        return {}

    total_chars = sum(len(v) for v in active_blocks.values())

    # If under budget, no trimming needed
    if total_chars <= _TOTAL_BUDGET_CHARS:
        return active_blocks

    # Sort by priority (highest priority = lowest number)
    sorted_blocks = sorted(
        active_blocks.items(),
        key=lambda x: _BLOCK_PRIORITIES.get(x[0], 99)
    )

    result: Dict[str, str] = {}
    remaining_budget = _TOTAL_BUDGET_CHARS

    for name, text in sorted_blocks:
        if remaining_budget <= 0:
            # Budget exhausted — drop this block
            logger.debug("Dropping block %s (budget exhausted)", name)
            continue

        soft_cap = _BLOCK_SOFT_CAPS.get(name, 800)

        # Allocate min(soft_cap, remaining_budget) to this block
        allocation = min(soft_cap, remaining_budget, len(text))

        if allocation < len(text):
            result[name] = _trim_block(text, allocation)
        else:
            result[name] = text

        remaining_budget -= len(result[name])

    return result


def get_memory_stats(blocks: Dict[str, str]) -> Dict[str, any]:
    """Get statistics about memory blocks for logging/debugging.

    Args:
        blocks: dict of {attr_name: block_text}

    Returns:
        dict with per-block and total stats
    """
    stats: Dict[str, any] = {
        "blocks": {},
        "total_chars": 0,
        "total_tokens_est": 0,
        "budget_chars": _TOTAL_BUDGET_CHARS,
        "budget_tokens_est": _TOTAL_BUDGET_CHARS // 4,
        "over_budget": False,
    }

    for name, text in blocks.items():
        if not text:
            continue
        block_tokens = _estimate_tokens(text)
        stats["blocks"][name] = {
            "chars": len(text),
            "tokens_est": block_tokens,
            "priority": _BLOCK_PRIORITIES.get(name, 99),
        }
        stats["total_chars"] += len(text)
        stats["total_tokens_est"] += block_tokens

    stats["over_budget"] = stats["total_chars"] > _TOTAL_BUDGET_CHARS

    return stats


def format_memory_summary(blocks: Dict[str, str]) -> str:
    """Format a brief summary of memory blocks for logging.

    Returns a one-line summary like:
    "Memory: recall=400ch handoff=200ch evolution=160ch decisions=0ch | total=760ch/4000"
    """
    parts: List[str] = []
    total = 0

    for name in ["_recall_context", "_handoff_context", "_evolution_context", "_decisions_context"]:
        text = blocks.get(name, "")
        chars = len(text) if text else 0
        total += chars
        # Short name for logging
        short = name.replace("_context", "").replace("_", "", 1)
        parts.append(f"{short}={chars}ch")

    return f"Memory: {' '.join(parts)} | total={total}ch/{_TOTAL_BUDGET_CHARS}"
