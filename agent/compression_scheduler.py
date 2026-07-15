"""Proactive compression scheduler — responds to LLM speed curve, not just context ceiling.

Core insight: response latency = f(input length, cache hit rate, compression overhead).
The baseline compressor only reacts to ``token > threshold`` — missing the cache
dimension and the fact that pre-threshold growth already slows generation.

This scheduler adds three dimensions:

1. **Cache-aware window** — Turn 2-5 (by default) never compresses, preserving
   provider prefix-cache hits.  Skipping compression here saves both the LLM
   compression call *and* preserves the provider-side cache, making these turns
   the fastest possible.

2. **Richness-aware compression depth** — Uses the existing emergence-system
   ``RichnessScore`` to decide *how aggressively* to compress.  High-richness
   users have dense data (old context retains more value → conservative cut).
   Low-richness users have sparse data (old context is noise → aggressive cut).

3. **Decision-point incremental cleanup** — After a tool batch finishes and
   the agent has consumed its results, the raw tool outputs from earlier batches
   are stripped immediately (zero LLM overhead) instead of waiting for token
   pressure.  This keeps the working-set small *between* compressions.

Architecture
------------

This module is a *scheduling* layer, not a replacement.  It decides *when*
to compress and *how deep*; the actual compression still goes through
``ContextCompressor.compress()``.  The existing threshold-based fallback
(token > 70% window) is the last line of defence and never removed.

Integration points
------------------
- ``agent/conversation_loop.py`` — replace the ``compression_attempts`` gating
  with ``CompressionScheduler`` decisions at the start of each turn.
- ``agent/memory_recall.py`` — ``compute_richness()`` already exists and is
  the sole data signal consumed by this scheduler.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants (tunable per provider via config)
# ---------------------------------------------------------------------------

# Turns 0-(n-1) are cache-warm — skip ALL compression.
# Different providers have different prefix-cache behaviour:
#   Anthropic: explicit prompt caching (set this high, 5-8)
#   OpenAI: some models have transparent prefix cache (3-5)
#   DeepSeek / OpenRouter: undocumented — assume none (0)
#   Local (ollama): none (0)
DEFAULT_CACHE_WINDOW_TURNS = 5

# Below this many tokens, we NEVER trigger any compression — the LLM
# compression call itself costs more than the tokens it saves.
ABSOLUTE_MIN_TOKENS = 10_000

# Token percentage of context window that triggers the hard fallback.
# When tokens > HARD_THRESHOLD_PCT * context_length, we compress regardless
# of cache window or richness — this is the safety net.
HARD_THRESHOLD_PCT = 0.70

# After a tool batch completes and a NEW batch starts (different tools),
# all tool results beyond the most recent N are candidates for cleanup.
# These are stripped without any LLM call — raw text removal only.
KEEP_RECENT_TOOL_RESULTS = 2

# Hard limit on recent tool results kept even before a new batch boundary.
MAX_TOOL_RESULTS_BEFORE_CLEANUP = 5


@dataclass
class CompressionDecision:
    """The scheduler's answer to "should I compress, and how deep?"."""

    should_compress: bool = False
    mode: str = "none"          # none | incremental | light | standard | deep | hard_fallback
    depth: int = 0              # number of turns to protect (higher = more conservative)
    reason: str = ""            # human-readable reason for logging

    # Incremental-cleanup specific: which tool-call indices to strip
    strip_tool_indices: List[int] = field(default_factory=list)


@dataclass
class TurnMetrics:
    """Metrics collected at turn boundaries for cache-hit estimation."""
    turn_number: int
    api_latency_ms: float            # time from API call to first token
    approx_tokens: int               # estimated input tokens this turn
    tool_calls_this_turn: int        # how many tool calls in this batch
    tool_names_this_turn: List[str]  # which tools were called


class CompressionScheduler:
    """Decides compression timing + depth based on turn, cache, richness, and tool state.

    Usage (one instance per agent session)::

        scheduler = CompressionScheduler(cache_window_turns=5)
        ...
        for turn in conversation:
            metrics = TurnMetrics(turn_number=n, ...)
            decision = scheduler.evaluate(richness, metrics, approx_tokens, context_length)
            if decision.should_compress:
                ...
    """

    def __init__(
        self,
        *,
        cache_window_turns: int = DEFAULT_CACHE_WINDOW_TURNS,
        hard_threshold_pct: float = HARD_THRESHOLD_PCT,
        absolute_min_tokens: int = ABSOLUTE_MIN_TOKENS,
    ):
        self.cache_window = cache_window_turns
        self.hard_threshold_pct = hard_threshold_pct
        self.absolute_min_tokens = absolute_min_tokens

        # Per-session state
        self._turn_count: int = 0
        self._last_tool_names: List[str] = []
        self._last_compress_turn: int = -1          # turn when last compression ran
        self._cooldown_until_turn: int = 0           # skip compression until this turn
        self._consecutive_slow_turns: int = 0        # consecutive turns with latency > baseline
        self._baseline_latency_ms: float = 0.0       # median turn 1 latency (cache MISS)
        self._decision_history: List[CompressionDecision] = []

        # Provider-specific: when True, we assume prefix caching exists.
        # Set via config: agent.compression.cache_window > 0 means "assume cache".
        self._assume_cache: bool = cache_window_turns > 0

    # ── Public API ──────────────────────────────────────────────────────────

    def record_turn(self, metrics: TurnMetrics) -> None:
        """Feed turn-completion metrics into the scheduler."""
        self._turn_count += 1
        if self._turn_count == 1 and metrics.api_latency_ms > 0:
            self._baseline_latency_ms = metrics.api_latency_ms

        # Track slow-turn streak for cache-miss detection
        if self._baseline_latency_ms > 0 and self._turn_count > 1:
            if metrics.api_latency_ms > self._baseline_latency_ms * 1.5:
                self._consecutive_slow_turns += 1
            else:
                self._consecutive_slow_turns = 0

        # Track tool name changes for decision-point detection
        self._last_tool_names = list(metrics.tool_names_this_turn)

    def evaluate(
        self,
        richness: Any,               # RichnessScore from memory_recall
        approx_tokens: int,
        context_length: int,
        *,
        tools_changed: bool = False,
        force: bool = False,
    ) -> CompressionDecision:
        """Evaluate whether and how to compress at this moment.

        Args:
            richness: ``RichnessScore`` from ``compute_richness()``.
            approx_tokens: Current estimated token count.
            context_length: Model context window size.
            tools_changed: True when this turn's tool batch differs from
                the previous turn's (old results consumed → cleanup candidate).
            force: Bypass all gating and return a standard compression decision.
                Used by ``/compress`` slash command.

        Returns a ``CompressionDecision`` — callers route to compression or
        incremental cleanup based on ``.mode``.
        """
        if force:
            return CompressionDecision(
                should_compress=True,
                mode="standard",
                depth=self._depth_for_richness(richness),
                reason="forced by user or system command",
            )

        # ── Guard 0: below absolute minimum ─────────────────────────────
        if approx_tokens < self.absolute_min_tokens:
            return CompressionDecision(
                should_compress=False,
                mode="none",
                reason=f"tokens={approx_tokens:,} < min={self.absolute_min_tokens:,} — "
                       f"compression overhead exceeds savings",
            )

        # ── Guard 1: hard fallback (safety net) ─────────────────────────
        hard_limit = int(context_length * self.hard_threshold_pct)
        if approx_tokens > hard_limit:
            return CompressionDecision(
                should_compress=True,
                mode="hard_fallback",
                depth=3,  # minimal protection — we're desperate
                reason=f"tokens={approx_tokens:,} > {self.hard_threshold_pct*100:.0f}% "
                       f"of context={context_length:,} — hard fallback",
            )

        # ── Guard 2: cooldown ───────────────────────────────────────────
        if self._turn_count < self._cooldown_until_turn:
            return CompressionDecision(
                should_compress=False,
                mode="none",
                reason=f"cooldown until turn {self._cooldown_until_turn} "
                       f"(last compression at turn {self._last_compress_turn})",
            )

        # ── Guard 3: cache-miss pessimism (check BEFORE cache window) ──
        # Cache-miss detection MUST precede the cache-window gate:
        # if we're past turn 1 and latency is consistently high, prefix
        # caching isn't working — skip the cache window and compress.
        if self._baseline_latency_ms > 0 and self._turn_count > 1:
            if self._consecutive_slow_turns >= 2:
                self._consecutive_slow_turns = 0
                return CompressionDecision(
                    should_compress=True,
                    mode="standard",
                    depth=self._depth_for_richness(richness),
                    reason=f"cache-miss detected ({self._consecutive_slow_turns+2} "
                           f"consecutive slow turns > baseline={self._baseline_latency_ms:.0f}ms) "
                           f"— compressing to shrink working set",
                )

        # ── Guard 4: cache-warm window ──────────────────────────────────
        if self._assume_cache and self._turn_count <= self.cache_window:
            return CompressionDecision(
                should_compress=False,
                mode="none",
                reason=f"turn {self._turn_count} <= cache_window={self.cache_window} "
                       f"— preserving prefix cache",
            )

        # ── Decision-point incremental cleanup ──────────────────────────
        # Tools changed + below 50% of threshold → just strip old results
        if tools_changed and approx_tokens < hard_limit * 0.5:
            return CompressionDecision(
                should_compress=False,  # NOT a compression — zero LLM cost
                mode="incremental",
                depth=0,
                reason=f"decision-point cleanup: tools changed, "
                       f"tokens={approx_tokens:,} < {hard_limit * 0.5:.0f}",
            )

        # ── Proactive: tokens creeping toward 50% of threshold ──────────
        # At 50-70% of hard limit, consider a light/standard compression
        # BEFORE the model starts slowing down.
        proactive_trigger = int(hard_limit * 0.50)
        if approx_tokens > proactive_trigger:
            depth = self._depth_for_richness(richness)
            # Pick mode based on how close we are to the hard limit
            if approx_tokens > hard_limit * 0.85:
                mode = "deep"
            elif approx_tokens > hard_limit * 0.70:
                mode = "standard"
            else:
                mode = "light"
            return CompressionDecision(
                should_compress=True,
                mode=mode,
                depth=depth,
                reason=f"proactive compression at tokens={approx_tokens:,} "
                       f"(>{proactive_trigger*100//hard_limit}% of hard limit={hard_limit:,}, "
                       f"richness={richness.tier})",
            )

        # ── No action needed ────────────────────────────────────────────
        return CompressionDecision(
            should_compress=False,
            mode="none",
            reason=f"tokens={approx_tokens:,} under proactive trigger "
                   f"({proactive_trigger:,}), turn={self._turn_count}",
        )

    def record_compression(self) -> None:
        """Notify the scheduler that compression just ran.

        Sets a cooldown to prevent back-to-back compressions (which
        thrash the provider and degrade user experience).
        """
        self._last_compress_turn = self._turn_count
        # Cooldown: at least 2 turns before next compression evaluation
        self._cooldown_until_turn = self._turn_count + 3

    # ── Internal helpers ────────────────────────────────────────────────────

    def _depth_for_richness(self, richness: Any) -> int:
        """Map richness tier to 'how many recent turns to protect from compression'.

        Higher depth = more conservative = leaves more context intact.
        """
        tier = getattr(richness, "tier", "cold_start")
        if tier in ("fluent", "learning"):
            return 8   # high data density — old context still valuable
        elif tier == "building":
            return 5   # moderate — standard protection
        else:  # cold_start
            return 3   # sparse data — aggressive cut is safe

    def _find_stale_tool_indices(self) -> List[int]:
        """Find tool-result message indices that are candidates for cleanup.

        Only applicable when integrated into the message list by the caller
        — this returns indices the caller can use to strip messages.
        """
        # This is a stub — the actual cleanup is done by the caller
        # (conversation_loop.py) because only it has access to messages.
        # We return an empty list here; the caller detects decision-point
        # via mode == "incremental" and strips tool results itself.
        return []


# ---------------------------------------------------------------------------
# Helper for stripping stale tool results from the message list
# ---------------------------------------------------------------------------

def strip_stale_tool_results(
    messages: List[Dict[str, Any]],
    keep_recent: int = KEEP_RECENT_TOOL_RESULTS,
    max_total: int = MAX_TOOL_RESULTS_BEFORE_CLEANUP,
) -> Tuple[List[Dict[str, Any]], int]:
    """Remove old tool-result messages, keeping only the N most recent.

    A "tool result" is a message with ``role="tool"``.  This is a
    purely structural cleanup — no LLM call, no summarisation.

    Returns:
        ``(cleaned_messages, stripped_count)`` tuple.
    """
    if not messages:
        return messages, 0

    tool_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "tool"
    ]

    if len(tool_indices) <= keep_recent:
        return messages, 0

    # Keep the most recent N tool results, strip the rest
    keep_indices = set(tool_indices[-keep_recent:])
    cleaned = [
        m for i, m in enumerate(messages)
        if i not in tool_indices or i in keep_indices
    ]

    stripped = len(messages) - len(cleaned)
    if stripped > 0:
        logger.info(
            "Incremental cleanup: stripped %d stale tool results, "
            "kept %d most recent (from %d total tool results in %d messages)",
            stripped, keep_recent, len(tool_indices), len(messages),
        )

    return cleaned, stripped


# ---------------------------------------------------------------------------
# Provider cache configuration
# ---------------------------------------------------------------------------

# Known provider prefix-cache characteristics (empirical, as of 2026-07).
# Values are recommended ``cache_window_turns`` — set 0 when cache is
# unreliable or non-existent.
PROVIDER_CACHE_WINDOWS: Dict[str, int] = {
    "anthropic": 8,        # explicit prompt caching, well-documented
    "openai": 4,           # transparent prefix cache on some models
    "google": 3,           # context caching, must be explicitly created
    "openrouter": 0,        # opaque proxy — assume no cache
    "groq": 0,             # fast but no documented prefix cache
    "deepseek": 0,         # no documented prefix cache
    "mistral": 0,          # no documented prefix cache
    "xai": 0,              # no documented prefix cache
    "ollama": 0,           # local — no cache
    "local": 0,            # generic local provider
}


def resolve_cache_window(provider: str, config_override: Optional[int] = None) -> int:
    """Resolve the cache window for a provider.

    Priority: config override > known provider table > default (0 = no cache).
    """
    if config_override is not None:
        return config_override

    provider_lower = (provider or "").lower()
    for key, window in PROVIDER_CACHE_WINDOWS.items():
        if key in provider_lower:
            return window
    return 0


__all__ = [
    "CompressionDecision",
    "CompressionScheduler",
    "TurnMetrics",
    "strip_stale_tool_results",
    "resolve_cache_window",
    "PROVIDER_CACHE_WINDOWS",
    "ABSOLUTE_MIN_TOKENS",
    "HARD_THRESHOLD_PCT",
    "KEEP_RECENT_TOOL_RESULTS",
    "MAX_TOOL_RESULTS_BEFORE_CLEANUP",
]
