"""Tests for the proactive compression scheduler.

Verifies that the scheduler correctly gates compression based on:
- Cache-warm window (turns 1-5: no compression)
- Absolute minimum token threshold (never compress <10K)
- Hard fallback (>70% context)
- Richness-aware depth
- Decision-point incremental cleanup
- Cooldown enforcement
- Cache-miss pessimism detection
"""

import pytest
from unittest.mock import MagicMock, patch

from agent.compression_scheduler import (
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


# ── Turning off cache — verifies direct-hit compression ─────────────────────


def test_never_compress_below_absolute_min():
    """Below 10K tokens, compression overhead > savings → never compress."""
    s = _make_scheduler(cache_window_turns=0)  # no cache window
    richness = FakeRichness(tier="cold_start")
    decision = s.evaluate(richness, approx_tokens=5_000, context_length=200_000)
    assert not decision.should_compress
    assert decision.mode == "none"
    assert "compression overhead exceeds savings" in decision.reason


def test_just_above_min_with_no_cache_triggers_proactive():
    """With no cache window and tokens > 50% of hard limit → should compress."""
    s = _make_scheduler(cache_window_turns=0)
    # Simulate 6th turn to skip any remaining cache logic
    for _ in range(6):
        s.record_turn(TurnMetrics(turn_number=s._turn_count + 1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    richness = FakeRichness(tier="building")
    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)  # 140K
    proactive = int(hard_limit * 0.50)  # 70K

    # Just above proactive trigger
    decision = s.evaluate(richness, approx_tokens=proactive + 1000, context_length=context_length)
    assert decision.should_compress
    assert decision.mode in ("light", "standard", "deep")


def test_hard_fallback_overrides_everything():
    """Above 70% context → hard fallback regardless of cache/richness."""
    s = _make_scheduler(cache_window_turns=5)
    # Only 2 turns in — well within cache window
    s.record_turn(TurnMetrics(turn_number=1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    s.record_turn(TurnMetrics(turn_number=2, api_latency_ms=800, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    richness = FakeRichness(tier="fluent")
    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)  # 140K

    decision = s.evaluate(richness, approx_tokens=hard_limit + 1, context_length=context_length)
    assert decision.should_compress
    assert decision.mode == "hard_fallback"
    assert "hard fallback" in decision.reason


# ── Cache-window tests ──────────────────────────────────────────────────────


def test_cache_window_blocks_compression():
    """Turn 3 within cache_window=5 → no compression."""
    s = _make_scheduler(cache_window_turns=5)
    s.record_turn(TurnMetrics(turn_number=1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    s.record_turn(TurnMetrics(turn_number=2, api_latency_ms=800, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    s.record_turn(TurnMetrics(turn_number=3, api_latency_ms=750, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))

    richness = FakeRichness(tier="cold_start")
    context_length = 200_000
    # Even with 60K tokens (way above proactive trigger), cache window blocks it
    decision = s.evaluate(richness, approx_tokens=60_000, context_length=context_length)
    assert not decision.should_compress
    assert "preserving prefix cache" in decision.reason


def test_cache_window_blocked_but_hard_fallback_still_fires():
    """Cache window blocks normal compression but NOT hard fallback."""
    s = _make_scheduler(cache_window_turns=5)
    s.record_turn(TurnMetrics(turn_number=1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    s.record_turn(TurnMetrics(turn_number=2, api_latency_ms=800, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))

    richness = FakeRichness(tier="cold_start")
    hard_limit = int(200_000 * HARD_THRESHOLD_PCT)  # 140K
    decision = s.evaluate(richness, approx_tokens=hard_limit + 100, context_length=200_000)
    assert decision.should_compress
    assert decision.mode == "hard_fallback"


def test_no_cache_provider_compresses_normally():
    """cache_window=0 → no cache assumption → falls through to proactive evaluation."""
    s = _make_scheduler(cache_window_turns=0)
    # 6 turns in without cache
    for _ in range(6):
        s.record_turn(TurnMetrics(turn_number=s._turn_count + 1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))

    richness = FakeRichness(tier="building")
    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)
    proactive = int(hard_limit * 0.50)

    # Above proactive trigger
    decision = s.evaluate(richness, approx_tokens=proactive + 10_000, context_length=context_length)
    assert decision.should_compress


# ── Richness-aware depth ────────────────────────────────────────────────────


def test_richness_fluent_gives_deeper_protection():
    """High-richness users get conservative compression (more turns protected)."""
    s = _make_scheduler(cache_window_turns=0)
    for _ in range(6):
        s.record_turn(TurnMetrics(turn_number=s._turn_count + 1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))

    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)
    proactive = int(hard_limit * 0.50)

    fluent = s.evaluate(FakeRichness(tier="fluent"), approx_tokens=proactive + 5000, context_length=context_length)
    cold = s.evaluate(FakeRichness(tier="cold_start"), approx_tokens=proactive + 5000, context_length=context_length)

    assert fluent.depth > cold.depth  # fluent → more protection


def test_richness_cold_start_sets_shallow_depth():
    s = _make_scheduler(cache_window_turns=0)
    for _ in range(6):
        s.record_turn(TurnMetrics(turn_number=s._turn_count + 1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))

    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)
    proactive = int(hard_limit * 0.50)

    decision = s.evaluate(FakeRichness(tier="cold_start"), approx_tokens=proactive + 5000, context_length=context_length)
    assert decision.depth <= 3


# ── Cooldown tests ──────────────────────────────────────────────────────────


def test_cooldown_blocks_back_to_back_compression():
    """After compression, cooldown prevents immediate re-compression."""
    s = _make_scheduler(cache_window_turns=0)
    for _ in range(6):
        s.record_turn(TurnMetrics(turn_number=s._turn_count + 1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))

    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)
    proactive = int(hard_limit * 0.50)

    # First compression
    decision1 = s.evaluate(FakeRichness(), approx_tokens=proactive + 10_000, context_length=context_length)
    assert decision1.should_compress
    s.record_compression()

    # Record one more turn
    s.record_turn(TurnMetrics(turn_number=s._turn_count + 1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))

    # Still within cooldown → should not compress
    decision2 = s.evaluate(FakeRichness(), approx_tokens=proactive + 20_000, context_length=context_length)
    assert not decision2.should_compress
    assert "cooldown" in decision2.reason


# ── Cache-miss pessimism ────────────────────────────────────────────────────


def test_cache_miss_triggers_compression():
    """2+ consecutive slow turns → assume cache miss → force compression."""
    s = _make_scheduler(cache_window_turns=5)
    s.record_turn(TurnMetrics(turn_number=1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    s.record_turn(TurnMetrics(turn_number=2, api_latency_ms=2000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))  # slow (2x baseline)
    s.record_turn(TurnMetrics(turn_number=3, api_latency_ms=2100, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))  # slow again

    # Even within cache window, two slow turns trigger cache-miss pessimism
    decision = s.evaluate(FakeRichness(), approx_tokens=40_000, context_length=200_000)
    assert decision.should_compress
    assert "cache-miss" in decision.reason


def test_cache_not_missed_when_latency_recovers():
    """One slow turn followed by normal → no cache-miss trigger."""
    s = _make_scheduler(cache_window_turns=5)
    s.record_turn(TurnMetrics(turn_number=1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))
    s.record_turn(TurnMetrics(turn_number=2, api_latency_ms=2000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))  # slow
    s.record_turn(TurnMetrics(turn_number=3, api_latency_ms=900, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))   # normal → counter reset

    # Should NOT trigger cache-miss (counter was reset)
    decision = s.evaluate(FakeRichness(), approx_tokens=40_000, context_length=200_000)
    # We're in cache window, so compression is blocked — and cache-miss should NOT fire
    assert not decision.should_compress


# ── Decision-point incremental cleanup ──────────────────────────────────────


def test_decision_point_cleanup_triggered():
    """Tools changed + below 50% hard limit → incremental cleanup (no LLM cost)."""
    s = _make_scheduler(cache_window_turns=0)
    for _ in range(6):
        s.record_turn(TurnMetrics(turn_number=s._turn_count + 1, api_latency_ms=1000, approx_tokens=0, tool_calls_this_turn=0, tool_names_this_turn=[]))

    context_length = 200_000
    hard_limit = int(context_length * HARD_THRESHOLD_PCT)

    # Below 50% hard limit + tools_changed=True
    decision = s.evaluate(
        FakeRichness(),
        approx_tokens=hard_limit // 3,  # ~33% of hard limit
        context_length=context_length,
        tools_changed=True,
    )
    assert decision.mode == "incremental"
    assert not decision.should_compress  # zero LLM cost!
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
    assert stripped == 3  # 5 total - 2 kept = 3 stripped
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
    # non-tool messages preserved
    assert cleaned[0]["role"] == "system"
    assert cleaned[1]["role"] == "user"
    assert cleaned[2]["role"] == "assistant"
    # only most recent tool result kept
    tool_kept = [m for m in cleaned if m.get("role") == "tool"]
    assert len(tool_kept) == 1
    assert tool_kept[0]["content"] == "result3"


# ── resolve_cache_window ────────────────────────────────────────────────────


def test_resolve_cache_window_known_providers():
    assert resolve_cache_window("anthropic") == 8
    assert resolve_cache_window("openai") == 5
    assert resolve_cache_window("deepseek") == 5  # KV cache, 98% hit rate
    assert resolve_cache_window("mistral") == 0   # no public cache API
    assert resolve_cache_window("groq") == 0       # no documented cache
    assert resolve_cache_window("ollama") == 0
    assert resolve_cache_window("moonshot") == 3   # Kimi context caching
    assert resolve_cache_window("xai") == 3         # Grok Build prompt caching


def test_resolve_cache_window_substring_match():
    assert resolve_cache_window("anthropic/claude-opus-4.5") == 8
    assert resolve_cache_window("openrouter/anthropic/claude") == 8  # anthropic substring match


def test_resolve_cache_window_unknown():
    assert resolve_cache_window("some_unknown_provider") == 0


def test_resolve_cache_window_override():
    assert resolve_cache_window("anthropic", config_override=0) == 0
    assert resolve_cache_window("deepseek", config_override=5) == 5
