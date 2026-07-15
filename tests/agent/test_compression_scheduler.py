"""Tests for the proactive compression scheduler.

Verifies that the scheduler correctly gates compression based on:
- Dynamic cache window (CacheObserver: observed latency, not hardcoded table)
- Absolute minimum token threshold (never compress <10K)
- Hard fallback (>70% context)
- Richness-aware depth
- Decision-point incremental cleanup
- Cooldown enforcement
- Cache-miss detection (from CacheObserver consecutive slow turns)
- Cache-hit detection (observer extends window when latency drops)
"""

import pytest
from unittest.mock import MagicMock, patch

from agent.compression_scheduler import (
    CacheObserver,
    CompressionDecision,
    CompressionScheduler,
    TurnMetrics,
    strip_stale_tool_results,
    resolve_cache_window,
    ABSOLUTE_MIN_TOKENS,
    HARD_THRESHOLD_PCT,
    KEEP_RECENT_TOOL_RESULTS,
    PROVIDER_CACHE_WINDOWS,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


class FakeRichness:
    def __init__(self, tier="cold_start", value=0.0):
        self.tier = tier
        self.value = value


def _make_scheduler(**kwargs) -> CompressionScheduler:
    return CompressionScheduler(**kwargs)


def _record_turns(s: CompressionScheduler, n: int, latency_ms: float = 1000):
    """Record n turns with uniform latency."""
    for _ in range(n):
        s.record_turn(TurnMetrics(
            turn_number=s._turn_count + 1,
            api_latency_ms=latency_ms,
            approx_tokens=0,
            tool_calls_this_turn=0,
            tool_names_this_turn=[],
        ))


# ── Absolute minimum / hard fallback ────────────────────────────────────────


def test_never_compress_below_absolute_min():
    """Below 10K tokens, compression overhead > savings → never compress."""
    s = _make_scheduler(cache_window_turns=0)
    richness = FakeRichness(tier="cold_start")
    decision = s.evaluate(richness, approx_tokens=5_000, context_length=200_000)
    assert not decision.should_compress
    assert decision.mode == "none"
    assert "compression overhead exceeds savings" in decision.reason


def test_just_above_min_with_no_cache_triggers_proactive():
    """With no cache window and tokens > 50% of hard limit → should compress."""
    s = _make_scheduler(cache_window_turns=0)
    _record_turns(s, 6)
    richness = FakeRichness(tier="building")
    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)  # 140K
    proactive = int(hard_limit * 0.50)  # 70K

    decision = s.evaluate(richness, approx_tokens=proactive + 1000, context_length=context_length)
    assert decision.should_compress
    assert decision.mode in ("light", "standard", "deep")


def test_hard_fallback_overrides_everything():
    """Above 70% context → hard fallback regardless of cache/richness."""
    s = _make_scheduler(cache_window_turns=5)
    _record_turns(s, 2, latency_ms=1000)
    richness = FakeRichness(tier="fluent")
    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)

    decision = s.evaluate(richness, approx_tokens=hard_limit + 1, context_length=context_length)
    assert decision.should_compress
    assert decision.mode == "hard_fallback"
    assert "hard fallback" in decision.reason


# ── CacheObserver tests ────────────────────────────────────────────────────


def test_observer_baseline_set_on_turn_1():
    """Turn 1 latency becomes the baseline (always cache miss)."""
    obs = CacheObserver(initial_window=5)
    obs.record_turn(1500.0)
    assert obs.has_baseline
    assert obs._baseline_latency_ms == 1500.0


def test_observer_detects_cache_hit():
    """Turn 2+ significantly faster than baseline → cache hit detected."""
    obs = CacheObserver(initial_window=2)
    obs.record_turn(1000.0)  # baseline
    obs.record_turn(500.0)   # 50% of baseline → cache hit
    assert obs._consecutive_fast_turns == 1


def test_observer_detects_cache_miss():
    """Turn 2+ as slow or slower than baseline → cache miss detected."""
    obs = CacheObserver(initial_window=5)
    obs.record_turn(1000.0)  # baseline
    obs.record_turn(1300.0)  # 130% of baseline → cache miss
    assert obs._consecutive_slow_turns == 1


def test_observer_extends_window_on_consistent_hits():
    """Multiple consecutive fast turns → observer extends cache window."""
    obs = CacheObserver(initial_window=3)
    obs.record_turn(1000.0)  # baseline
    obs.record_turn(600.0)   # hit
    obs.record_turn(500.0)   # hit (streak=2 → extend)
    assert obs._observed_window is not None
    assert obs._observed_window > 3  # extended


def test_observer_shrinks_window_on_consistent_misses():
    """Multiple consecutive slow turns → observer shrinks cache window."""
    obs = CacheObserver(initial_window=5)
    obs.record_turn(1000.0)  # baseline
    obs.record_turn(1500.0)  # miss
    obs.record_turn(1600.0)  # miss (streak=2 → shrink)
    assert obs._observed_window is not None
    assert obs._observed_window < 5  # shrunk


def test_observer_effective_window_uses_observed_after_data():
    """After ≥2 turns of data, effective_window uses observed, not initial."""
    obs = CacheObserver(initial_window=5)
    obs.record_turn(1000.0)  # baseline
    obs.record_turn(1200.0)  # miss
    obs.record_turn(1300.0)  # miss (streak=2 → shrink to 3)
    # observed_window should now be set and < initial
    assert obs.effective_window < 5


def test_observer_effective_window_uses_initial_before_data():
    """Before any observation, effective_window = initial guess."""
    obs = CacheObserver(initial_window=4)
    assert obs.effective_window == 4


def test_observer_is_cache_broken():
    """is_cache_broken() True after SLOW_STREAK_THRESHOLD consecutive misses."""
    obs = CacheObserver(initial_window=5)
    obs.record_turn(1000.0)  # baseline
    obs.record_turn(1500.0)  # miss
    assert not obs.is_cache_broken()  # only 1 miss
    obs.record_turn(1600.0)  # miss (streak=2)
    assert obs.is_cache_broken()  # threshold reached


def test_observer_neutral_zone_no_streak_change():
    """Latency within ±15-30% of baseline → no streak change."""
    obs = CacheObserver(initial_window=5)
    obs.record_turn(1000.0)  # baseline
    obs.record_turn(1050.0)  # 105% → neutral (between 70% and 115%)
    assert obs._consecutive_fast_turns == 0
    assert obs._consecutive_slow_turns == 0


def test_observer_summary():
    """summary() returns a dict with expected keys."""
    obs = CacheObserver(initial_window=3)
    obs.record_turn(1000.0)
    obs.record_turn(600.0)
    s = obs.summary()
    assert "turn_count" in s
    assert "baseline_latency_ms" in s
    assert "observed_window" in s
    assert "effective_window" in s
    assert s["turn_count"] == 2


# ── Cache window integration with scheduler ────────────────────────────────


def test_cache_window_blocks_compression():
    """Turn 3 within effective cache window → no compression."""
    s = _make_scheduler(cache_window_turns=5)
    # Record turns with fast latency (cache hits) so observer keeps window at 5
    _record_turns(s, 3, latency_ms=1000)
    # Turns 2 and 3 are fast → observer may extend, but at minimum keeps 5
    richness = FakeRichness(tier="cold_start")
    decision = s.evaluate(richness, approx_tokens=60_000, context_length=200_000)
    assert not decision.should_compress
    assert "preserving prefix cache" in decision.reason


def test_cache_window_blocked_but_hard_fallback_still_fires():
    """Cache window blocks normal compression but NOT hard fallback."""
    s = _make_scheduler(cache_window_turns=5)
    _record_turns(s, 2, latency_ms=1000)
    richness = FakeRichness(tier="cold_start")
    hard_limit = int(200_000 * HARD_THRESHOLD_PCT)
    decision = s.evaluate(richness, approx_tokens=hard_limit + 100, context_length=200_000)
    assert decision.should_compress
    assert decision.mode == "hard_fallback"


def test_no_cache_provider_compresses_normally():
    """cache_window=0 → no cache assumption → falls through to proactive evaluation."""
    s = _make_scheduler(cache_window_turns=0)
    _record_turns(s, 6)
    richness = FakeRichness(tier="building")
    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)
    proactive = int(hard_limit * 0.50)
    decision = s.evaluate(richness, approx_tokens=proactive + 10_000, context_length=context_length)
    assert decision.should_compress


def test_observer_shrinks_window_and_allows_compression():
    """When observer detects cache misses, it shrinks window → compression allowed."""
    s = _make_scheduler(cache_window_turns=5)
    # Turn 1: baseline
    s.record_turn(TurnMetrics(turn_number=1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    # Turns 2-3: slow (cache misses) → observer shrinks window to 3
    s.record_turn(TurnMetrics(turn_number=2, api_latency_ms=1500, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    s.record_turn(TurnMetrics(turn_number=3, api_latency_ms=1600, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    # Turn 4: still slow, but observer already shrank window → should compress
    s.record_turn(TurnMetrics(turn_number=4, api_latency_ms=1700, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    # At turn 4 with observed_window < 5, compression should be allowed
    richness = FakeRichness(tier="cold_start")
    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)
    proactive = int(hard_limit * 0.50)
    decision = s.evaluate(richness, approx_tokens=proactive + 5000, context_length=context_length)
    assert decision.should_compress


# ── Richness-aware depth ────────────────────────────────────────────────────


def test_richness_fluent_gives_deeper_protection():
    """High-richness users get conservative compression (more turns protected)."""
    s = _make_scheduler(cache_window_turns=0)
    _record_turns(s, 6)
    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)
    proactive = int(hard_limit * 0.50)

    fluent = s.evaluate(FakeRichness(tier="fluent"), approx_tokens=proactive + 5000, context_length=context_length)
    cold = s.evaluate(FakeRichness(tier="cold_start"), approx_tokens=proactive + 5000, context_length=context_length)
    assert fluent.depth > cold.depth


def test_richness_cold_start_sets_shallow_depth():
    s = _make_scheduler(cache_window_turns=0)
    _record_turns(s, 6)
    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)
    proactive = int(hard_limit * 0.50)
    decision = s.evaluate(FakeRichness(tier="cold_start"), approx_tokens=proactive + 5000, context_length=context_length)
    assert decision.depth <= 3


# ── Cooldown ────────────────────────────────────────────────────────────────


def test_cooldown_blocks_back_to_back_compression():
    """After compression, cooldown prevents immediate re-compression."""
    s = _make_scheduler(cache_window_turns=0)
    _record_turns(s, 6)
    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)
    proactive = int(hard_limit * 0.50)

    decision1 = s.evaluate(FakeRichness(), approx_tokens=proactive + 10_000, context_length=context_length)
    assert decision1.should_compress
    s.record_compression()

    _record_turns(s, 1)
    decision2 = s.evaluate(FakeRichness(), approx_tokens=proactive + 20_000, context_length=context_length)
    assert not decision2.should_compress
    assert "cooldown" in decision2.reason


# ── Cache-miss from observer ───────────────────────────────────────────────


def test_cache_miss_triggers_compression():
    """2+ consecutive slow turns → observer detects cache broken → compress."""
    s = _make_scheduler(cache_window_turns=5)
    s.record_turn(TurnMetrics(turn_number=1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    s.record_turn(TurnMetrics(turn_number=2, api_latency_ms=2000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))  # slow
    s.record_turn(TurnMetrics(turn_number=3, api_latency_ms=2100, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))  # slow again

    decision = s.evaluate(FakeRichness(), approx_tokens=40_000, context_length=200_000)
    assert decision.should_compress
    assert "cache-miss" in decision.reason


def test_cache_not_missed_when_latency_recovers():
    """One slow turn followed by normal → no cache-miss trigger."""
    s = _make_scheduler(cache_window_turns=5)
    s.record_turn(TurnMetrics(turn_number=1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    s.record_turn(TurnMetrics(turn_number=2, api_latency_ms=2000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))  # slow
    s.record_turn(TurnMetrics(turn_number=3, api_latency_ms=700, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))   # fast → counter reset

    decision = s.evaluate(FakeRichness(), approx_tokens=40_000, context_length=200_000)
    assert not decision.should_compress


# ── Decision-point incremental cleanup ──────────────────────────────────────


def test_decision_point_cleanup_triggered():
    """Tools changed + below 50% hard limit → incremental cleanup (no LLM cost)."""
    s = _make_scheduler(cache_window_turns=0)
    _record_turns(s, 6)
    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)

    decision = s.evaluate(
        FakeRichness(),
        approx_tokens=hard_limit // 3,
        context_length=context_length,
        tools_changed=True,
    )
    assert decision.mode == "incremental"
    assert not decision.should_compress
    assert "decision-point" in decision.reason


def test_force_bypasses_all_gating():
    """force=True → compresses regardless."""
    s = _make_scheduler(cache_window_turns=5)
    s.record_turn(TurnMetrics(turn_number=1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    decision = s.evaluate(FakeRichness(), approx_tokens=5_000, context_length=200_000, force=True)
    assert decision.should_compress
    assert decision.mode == "standard"


# ── strip_stale_tool_results ────────────────────────────────────────────────


def test_strip_stale_tool_results_leaves_recent():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "tool", "tool_call_id": "t1", "content": "old result 1"},
        {"role": "tool", "tool_call_id": "t2", "content": "old result 2"},
        {"role": "tool", "tool_call_id": "t3", "content": "old result 3"},
        {"role": "tool", "tool_call_id": "t4", "content": "recent result 1"},
        {"role": "tool", "tool_call_id": "t5", "content": "recent result 2"},
    ]
    cleaned, stripped = strip_stale_tool_results(messages, keep_recent=2)
    assert stripped == 3
    tool_contents = [m["content"] for m in cleaned if m.get("role") == "tool"]
    assert tool_contents == ["recent result 1", "recent result 2"]


def test_strip_stale_tool_results_noop_when_few():
    messages = [
        {"role": "tool", "tool_call_id": "t1", "content": "only result"},
    ]
    cleaned, stripped = strip_stale_tool_results(messages, keep_recent=2)
    assert stripped == 0
    assert len(cleaned) == 1


def test_strip_stale_tool_results_empty():
    cleaned, stripped = strip_stale_tool_results([])
    assert stripped == 0
    assert cleaned == []


def test_strip_stale_tool_results_preserves_non_tool():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi", "tool_calls": []},
        {"role": "tool", "tool_call_id": "t1", "content": "result1"},
        {"role": "tool", "tool_call_id": "t2", "content": "result2"},
        {"role": "tool", "tool_call_id": "t3", "content": "result3"},
    ]
    cleaned, stripped = strip_stale_tool_results(messages, keep_recent=1)
    assert stripped == 2
    assert cleaned[0]["role"] == "system"
    assert cleaned[1]["role"] == "user"
    assert cleaned[2]["role"] == "assistant"
    tool_kept = [m for m in cleaned if m.get("role") == "tool"]
    assert len(tool_kept) == 1
    assert tool_kept[0]["content"] == "result3"


# ── resolve_cache_window (now just cold-start fallback) ─────────────────────


def test_resolve_cache_window_known_providers():
    """Provider table is now a cold-start fallback, not a source of truth."""
    assert resolve_cache_window("anthropic") == 8
    assert resolve_cache_window("openai") == 5
    assert resolve_cache_window("deepseek") == 5
    assert resolve_cache_window("mistral") == 0
    assert resolve_cache_window("groq") == 0
    assert resolve_cache_window("ollama") == 0
    assert resolve_cache_window("moonshot") == 3
    assert resolve_cache_window("xai") == 3


def test_resolve_cache_window_substring_match():
    assert resolve_cache_window("anthropic/claude-opus-4.5") == 8
    assert resolve_cache_window("openrouter/anthropic/claude") == 8


def test_resolve_cache_window_unknown():
    assert resolve_cache_window("some_unknown_provider") == 0


def test_resolve_cache_window_override():
    assert resolve_cache_window("anthropic", config_override=0) == 0
    assert resolve_cache_window("deepseek", config_override=5) == 5


# ── Provider constructor ───────────────────────────────────────────────────


def test_scheduler_uses_provider_table_for_initial_window():
    """When constructed with provider='anthropic', initial window = 8."""
    s = CompressionScheduler(provider="anthropic")
    assert s.cache_observer._initial_window == 8


def test_scheduler_explicit_override_beats_provider():
    """Explicit cache_window_turns overrides provider table."""
    s = CompressionScheduler(provider="anthropic", cache_window_turns=2)
    assert s.cache_observer._initial_window == 2
