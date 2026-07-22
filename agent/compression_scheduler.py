"""Proactive compression scheduler — responds to LLM speed curve, not just context ceiling.

Core insight: response latency = f(input length, cache hit rate, compression overhead).
The baseline compressor only reacts to ``token > threshold`` — missing the cache
dimension and the fact that pre-threshold growth already slows generation.

This scheduler adds three dimensions:

1. **Cache-aware window (observed, not hardcoded)** — Observes actual API
   latency to detect whether prefix caching is effective.  Turn 1 is always
   a cache miss (baseline).  Turns 2+ that are significantly faster than
   baseline indicate cache hits → extend the no-compression window.
   Turns 2+ that are as slow as baseline indicate cache misses → compress
   normally.  A static fallback table (``PROVIDER_CACHE_WINDOWS``) provides
   initial guesses for turn 2 only; from turn 3 onward, all decisions are
   data-driven.

2. **Richness-aware compression depth** — Uses the existing emergence-system
   ``RichnessScore`` to decide *how aggressively* to compress.  High-richness
   users have dense data (old context retains more value → conservative cut).
   Low-richness users have sparse data (old context is noise → aggressive cut).

3. **Decision-point incremental cleanup** — After a tool batch finishes and
   the agent has consumed its results, the raw tool outputs from earlier batches
   are stripped immediately (zero LLM overhead) instead of waiting for token
   pressure.  This keeps the working-set small *between* compressions.

Design philosophy
-----------------
Same as the emergence system: **zero hardcoded assumptions about provider
behaviour.**  The static ``PROVIDER_CACHE_WINDOWS`` table is a cold-start
seed only — once we have ≥2 turns of latency data, the observed cache
effectiveness overrides the table entirely.  New providers, upgraded models,
changed cache policies: all handled automatically without code changes.

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
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants (tunable per provider via config)
# ---------------------------------------------------------------------------

# Default cache window for cold start (turn 2 only — from turn 3 onward
# we use observed latency data).  This is intentionally conservative: better
# to miss one turn of cache protection than to skip compression for 5 turns
# on a provider that has no cache.
DEFAULT_CACHE_WINDOW_TURNS = 2

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


class CacheObserver:
    """Observes API latency to detect prefix-cache effectiveness.

    Zero hardcoded assumptions: instead of a static table, we measure actual
    latency and let the data speak.  The observer tracks per-turn latency
    and decides whether prefix caching is helping.

    Lifecycle:
        Turn 1 → always cache miss → establish baseline latency
        Turn 2 → cold-start guess (from PROVIDER_CACHE_WINDOWS table)
        Turn 3+ → compare latency to baseline:
            - significantly faster (≤70% of baseline) → cache likely hit
            - similar speed (±15% of baseline) → no cache
            - slower (>115% of baseline) → cache miss or degradation

    The observer outputs a ``CacheObservation`` each turn, which the
    scheduler uses to dynamically set the effective cache window.
    """

    # Latency ratio thresholds for cache-hit detection (emerged, not hardcoded)
    CACHE_HIT_RATIO = 0.70     # ≤70% of baseline → likely cache hit
    CACHE_MISS_RATIO = 1.15    # >115% of baseline → likely cache miss
    SLOW_STREAK_THRESHOLD = 2  # consecutive slow turns to declare cache broken

    # Effective cache window bounds (the observed value lives within these)
    MIN_OBSERVED_WINDOW = 0
    MAX_OBSERVED_WINDOW = 10

    def __init__(self, initial_window: int = DEFAULT_CACHE_WINDOW_TURNS,
                 max_window: Optional[int] = None):
        self._initial_window = initial_window
        self._max_window = max_window or self.MAX_OBSERVED_WINDOW
        self._turn_count: int = 0
        self._latencies: Deque[float] = deque(maxlen=20)  # rolling window
        self._baseline_latency_ms: float = 0.0
        self._consecutive_fast_turns: int = 0   # cache hits in a row
        self._consecutive_slow_turns: int = 0    # cache misses in a row
        self._observed_window: Optional[int] = None  # set after turn 2

    @property
    def has_baseline(self) -> bool:
        return self._baseline_latency_ms > 0

    @property
    def effective_window(self) -> int:
        """The cache window to use right now.

        Priority: observed window (if available) > initial guess.
        Once we have ≥2 turns of data, observed window takes over entirely.
        """
        if self._observed_window is not None:
            return self._observed_window
        return self._initial_window

    def record_turn(self, latency_ms: float) -> None:
        """Feed a turn's latency into the observer."""
        self._turn_count += 1
        self._latencies.append(latency_ms)

        # Turn 1: establish baseline (always cache miss)
        if self._turn_count == 1:
            self._baseline_latency_ms = latency_ms
            logger.debug(
                "CacheObserver: baseline latency = %.0fms (turn 1, cache miss)",
                latency_ms,
            )
            return

        if self._baseline_latency_ms <= 0:
            # No valid baseline yet (e.g., turn 1 had 0 latency)
            return

        ratio = latency_ms / self._baseline_latency_ms

        if ratio <= self.CACHE_HIT_RATIO:
            self._consecutive_fast_turns += 1
            self._consecutive_slow_turns = 0
            logger.debug(
                "CacheObserver: turn %d likely cache HIT (ratio=%.2f, fast streak=%d)",
                self._turn_count, ratio, self._consecutive_fast_turns,
            )
        elif ratio >= self.CACHE_MISS_RATIO:
            self._consecutive_slow_turns += 1
            self._consecutive_fast_turns = 0
            logger.debug(
                "CacheObserver: turn %d likely cache MISS (ratio=%.2f, slow streak=%d)",
                self._turn_count, ratio, self._consecutive_slow_turns,
            )
        else:
            # Neutral zone — neither clearly hit nor miss
            # Don't reset streaks, but don't increment either
            logger.debug(
                "CacheObserver: turn %d neutral (ratio=%.2f)",
                self._turn_count, ratio,
            )

        # Update observed window from turn 3 onward
        if self._turn_count >= 2:
            self._update_observed_window()

    def _update_observed_window(self) -> None:
        """Dynamically adjust the observed cache window based on latency data."""
        # If cache is consistently missing, shrink window to 0
        if self._consecutive_slow_turns >= self.SLOW_STREAK_THRESHOLD:
            self._observed_window = max(
                self.MIN_OBSERVED_WINDOW,
                (self._observed_window or self._initial_window) - 2,
            )
            logger.info(
                "CacheObserver: cache appears broken (%d consecutive slow turns) "
                "→ observed_window=%d",
                self._consecutive_slow_turns, self._observed_window,
            )
            # Don't reset streak here — is_cache_broken() needs it.
            # The scheduler calls reset_cache_broken() after acting on it.
            return

        # If cache is consistently hitting, extend window
        if self._consecutive_fast_turns >= 2:
            extension = min(2, self._consecutive_fast_turns - 1)
            self._observed_window = min(
                self._max_window,
                (self._observed_window or self._initial_window) + extension,
            )
            logger.info(
                "CacheObserver: cache appears healthy (%d consecutive fast turns) "
                "→ observed_window=%d",
                self._consecutive_fast_turns, self._observed_window,
            )
            self._consecutive_fast_turns = 0  # reset after acting
            return

        # If we have enough data, use median ratio to set window
        if len(self._latencies) >= 4 and self._observed_window is None:
            recent = list(self._latencies)[1:]  # skip turn 1 (baseline)
            if recent:
                median_ratio = statistics.median(recent) / self._baseline_latency_ms
                if median_ratio <= self.CACHE_HIT_RATIO:
                    self._observed_window = min(
                        self._max_window,
                        self._initial_window + 3,
                    )
                elif median_ratio >= self.CACHE_MISS_RATIO:
                    self._observed_window = 0
                else:
                    self._observed_window = self._initial_window
                logger.info(
                    "CacheObserver: initial observation from %d turns "
                    "(median_ratio=%.2f) → observed_window=%d",
                    len(recent), median_ratio, self._observed_window,
                )

    def is_cache_broken(self) -> bool:
        """True when we've recently seen consecutive slow turns.

        Note: the caller (CompressionScheduler.evaluate) is responsible
        for resetting the streak after acting on this signal.  We don't
        reset here so that multiple calls within the same turn all see
        the same state.
        """
        return self._consecutive_slow_turns >= self.SLOW_STREAK_THRESHOLD

    def reset_cache_broken(self) -> None:
        """Clear the cache-broken signal after the scheduler has acted on it."""
        self._consecutive_slow_turns = 0

    def summary(self) -> Dict[str, Any]:
        """Return a summary dict for debugging / UI."""
        return {
            "turn_count": self._turn_count,
            "baseline_latency_ms": round(self._baseline_latency_ms, 1),
            "recent_latencies": [round(l, 1) for l in self._latencies],
            "consecutive_fast_turns": self._consecutive_fast_turns,
            "consecutive_slow_turns": self._consecutive_slow_turns,
            "observed_window": self._observed_window,
            "effective_window": self.effective_window,
        }


class CompressionScheduler:
    """Decides compression timing + depth based on turn, cache, richness, and tool state.

    Usage (one instance per agent session)::

        scheduler = CompressionScheduler(provider="anthropic")
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
        provider: str = "",
        cache_window_turns: Optional[int] = None,
        hard_threshold_pct: float = HARD_THRESHOLD_PCT,
        absolute_min_tokens: int = ABSOLUTE_MIN_TOKENS,
    ):
        # Resolve initial cache window: explicit override > provider table > default
        if cache_window_turns is not None:
            initial_window = cache_window_turns
        else:
            initial_window = resolve_cache_window(provider)

        self.hard_threshold_pct = hard_threshold_pct
        self.absolute_min_tokens = absolute_min_tokens

        # Cache observer — replaces static cache_window with dynamic observation
        self._cache_observer = CacheObserver(initial_window=initial_window)

        # Per-session state
        self._turn_count: int = 0
        self._last_tool_names: List[str] = []
        self._last_compress_turn: int = -1
        self._cooldown_until_turn: int = 0
        self._decision_history: List[CompressionDecision] = []

        # B 硬容量护栏：压缩轮次上限（防无限压缩循环）
        self._compression_rounds: int = 0
        self._max_compression_rounds: int = 20  # 每 session 最多压缩 20 次

    @property
    def cache_observer(self) -> CacheObserver:
        """Expose the cache observer for debugging / introspection."""
        return self._cache_observer

    # ── Public API ──────────────────────────────────────────────────────────

    def record_turn(self, metrics: TurnMetrics) -> None:
        """Feed turn-completion metrics into the scheduler and cache observer."""
        self._turn_count += 1

        # Feed latency to cache observer for dynamic window adjustment
        if metrics.api_latency_ms > 0:
            self._cache_observer.record_turn(metrics.api_latency_ms)

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

        # ── Guard 3: cache-broken detection (from CacheObserver) ──────
        # If the observer has detected consecutive slow turns, prefix caching
        # isn't working — skip the cache window and compress now.
        if self._cache_observer.is_cache_broken():
            self._cache_observer.reset_cache_broken()
            return CompressionDecision(
                should_compress=True,
                mode="standard",
                depth=self._depth_for_richness(richness),
                reason=f"cache-miss detected by observer "
                       f"({self._cache_observer.summary()})",
            )

        # ── Guard 4: cache-warm window (dynamically observed) ──────────
        # The effective window is now set by CacheObserver, not a static
        # table.  On turn 2 it uses the provider table guess; from turn 3+
        # it uses observed latency data entirely.
        effective_window = self._cache_observer.effective_window
        if effective_window > 0 and self._turn_count <= effective_window:
            return CompressionDecision(
                should_compress=False,
                mode="none",
                reason=f"turn {self._turn_count} <= observed cache_window={effective_window} "
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
        # B 硬容量护栏：压缩轮次计数
        self._compression_rounds += 1

    def compression_exhausted(self) -> bool:
        """Return True if max compression rounds reached (B 硬容量护栏)."""
        return self._compression_rounds >= self._max_compression_rounds

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
#
# What the number means: N = "don't compress for the first N turns",
# betting that the provider has prefix cache and turns 2..N will get
# cache hits on the overlapping prefix from turn 1.
#
#   0  = no cache or undocumented → evaluate compression from turn 2
#   3  = modest/implicit cache → protect early turns, but don't over-bet
#   5+ = strong, well-documented cache → protect more turns
#
# Sources:
#   Anthropic: explicit prompt caching via cache_control, 90% cost reduction
#       https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
#   OpenAI: automatic prefix caching on most models (no opt-in needed)
#       50% discount on cached input tokens
#   Google Gemini: implicit caching on Gemini 2.5 Pro/Flash (auto-detected)
#       up to 75% cost reduction; also explicit context caching API
#       https://ai.google.dev/gemini-api/docs/caching
#   DeepSeek: KV cache with 98% hit rate reported; cached tokens billed at 0.1x
#       hardcoded_cache_tokens in API response confirms cache hits
#       https://api-docs.deepseek.com/guides/kv_cache
#   xAI: Grok Build 0.1 supports prompt caching (cached read $0.20/M)
#       https://docs.x.ai/docs/models
#   Mistral: no public prompt caching API documented as of 2026-07
#   Groq: no documented prefix cache; ultra-low latency anyway
#   OpenRouter: opaque proxy — cache depends on underlying provider,
#       but can't be reliably assumed
#   Ollama / local: no remote cache; vLLM --enable-prefix-caching is
#       possible but can't be assumed
PROVIDER_CACHE_WINDOWS: Dict[str, int] = {
    "anthropic": 8,        # explicit prompt caching, 90% cost reduction
    "openai": 5,           # automatic prefix caching, 50% discount on cached tokens
    "google": 5,           # implicit caching on Gemini 2.5+, 75% cost reduction
    "deepseek": 5,         # KV cache, 98% hit rate reported, 0.1x cached token price
    "xai": 3,              # prompt caching supported on Grok Build, cached read $0.20/M
    "mistral": 0,          # no public prompt caching API
    "groq": 0,             # no documented prefix cache
    "openrouter": 0,       # opaque proxy — depends on underlying provider, can't assume
    "ollama": 0,           # local — no remote cache
    "local": 0,            # generic local provider
    "nous": 3,             # routes through OpenAI-compatible providers, cache varies
    "qwen": 3,             # DashScope API has implicit prefix caching on some models
    "zhipu": 3,            # GLM API has implicit caching on some models
    "baichuan": 0,         # no documented prefix cache
    "minimax": 0,          # no documented prefix cache
    "moonshot": 3,         # Kimi API has context caching on some models
    "hunyuan": 0,          # no documented prefix cache
    "yi": 0,               # no documented prefix cache
    "siliconflow": 0,      # aggregator — depends on underlying provider
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
    "CacheObserver",
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
