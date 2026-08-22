"""Turn-metrics event emission.

A2 (§7.5 stage 2): extract the inline telemetry埋点 from
``conversation_loop._record_turn_metrics`` into a single, independently
testable emitter. This is the "event化" boundary for turn metrics:

* The *producer* (conversation_loop) calls ``emit_turn_metrics_event``.
* The *consumers* (compression scheduler + metrics module) are fixed inside
  this emitter, so when ``conversation_loop`` is later split into a
  turn/step/stream Service the whole event contract travels with this file.

Behavior is best-effort: a failure in any subscriber must NEVER block turn
completion. This mirrors the original ``try/except: pass`` wrapper around
the inline calls.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from agent.compression_scheduler import TurnMetrics

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agent.conversation_loop import VermesAgent
    from agent.compression_scheduler import CompressionScheduler


def emit_turn_metrics_event(
    agent: "VermesAgent",
    messages: list[dict],
    _scheduler: "CompressionScheduler | None",
    api_start_time: float | None,
    approx_tokens: int,
) -> None:
    """Emit end-of-turn metrics to all subscribers (best-effort).

    Subscribers (fixed here so the event contract is portable):
    1. ``CompressionScheduler.record_turn`` — feeds the compaction scheduler.
    2. ``agent.metrics.record_turn_completed`` / ``record_tool_call`` — Route D
       metrics counters exposed via ``/api/v1/metrics``.
    """
    try:
        _api_latency = (time.time() - api_start_time) * 1000 if api_start_time else 0
        _tool_names_this_turn = []
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in (m.get("tool_calls") or []):
                    _name = tc.get("function", {}).get("name")
                    if _name:
                        _tool_names_this_turn.append(_name)
        _scheduler.record_turn(TurnMetrics(
            turn_number=agent._user_turn_count,
            api_latency_ms=_api_latency,
            approx_tokens=approx_tokens,
            tool_calls_this_turn=len(_tool_names_this_turn),
            tool_names_this_turn=_tool_names_this_turn,
        ))
        agent._current_turn_tool_names = _tool_names_this_turn
        agent._last_turn_tool_names = _tool_names_this_turn
        # ── Route D metrics ──
        from agent.metrics import record_turn_completed, record_tool_call
        record_turn_completed()
        for _tn in _tool_names_this_turn:
            record_tool_call(_tn, _api_latency / max(len(_tool_names_this_turn), 1))
    except Exception:
        pass  # scheduler metrics are best-effort - never block turn completion
