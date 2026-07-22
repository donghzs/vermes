"""
Lightweight in-process metrics collector for Vermes agent runtime.

Zero external dependencies. Thread-safe. Fail-open by design.
Metrics are exposed via /api/v1/metrics in Prometheus text format.

Design principles:
- Only collect what we already compute (no extra API calls)
- Never block the turn loop (all operations are O(1) or best-effort)
- Thread-safe via a single Lock (contended path is write-only, reads are rare)
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


@dataclass
class _MetricsState:
    """Mutable metrics state. Protected by _LOCK."""

    # ── Counters (monotonically increasing) ──────────────────────────
    sessions_created_total: int = 0
    sessions_closed_total: int = 0
    turns_total: int = 0
    tool_calls_total: int = 0
    tool_call_errors_total: int = 0
    llm_calls_total: int = 0
    llm_call_errors_total: int = 0
    llm_tokens_prompt: int = 0
    llm_tokens_completion: int = 0
    compressions_total: int = 0
    fatigue_bridges_total: int = 0
    prune_calls_total: int = 0
    continuity_loads_total: int = 0
    continuity_source_failures_total: int = 0
    pipeline_stages_total: int = 0
    pipeline_stage_failures_total: int = 0

    # ── Gauges (can go up or down) ───────────────────────────────────
    active_sessions: int = 0

    # ── Histograms (per-key bucket lists) ────────────────────────────
    tool_call_duration_ms: Dict[str, List[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    llm_call_duration_ms: List[float] = field(default_factory=list)

    # ── Error categories ─────────────────────────────────────────────
    error_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # ── Metadata ─────────────────────────────────────────────────────
    start_time: float = field(default_factory=time.time)

    def reset(self) -> None:
        """Reset all metrics (for testing)."""
        self.sessions_created_total = 0
        self.sessions_closed_total = 0
        self.turns_total = 0
        self.tool_calls_total = 0
        self.tool_call_errors_total = 0
        self.llm_calls_total = 0
        self.llm_call_errors_total = 0
        self.llm_tokens_prompt = 0
        self.llm_tokens_completion = 0
        self.compressions_total = 0
        self.fatigue_bridges_total = 0
        self.prune_calls_total = 0
        self.continuity_loads_total = 0
        self.continuity_source_failures_total = 0
        self.pipeline_stages_total = 0
        self.pipeline_stage_failures_total = 0
        self.active_sessions = 0
        self.tool_call_duration_ms.clear()
        self.llm_call_duration_ms.clear()
        self.error_counts.clear()
        self.start_time = time.time()


_state = _MetricsState()


# ── Public API ────────────────────────────────────────────────────────


def get_state() -> _MetricsState:
    """Return the global metrics state (for testing)."""
    return _state


def record_session_created() -> None:
    with _LOCK:
        _state.sessions_created_total += 1
        _state.active_sessions += 1


def record_session_closed() -> None:
    with _LOCK:
        _state.sessions_closed_total += 1
        _state.active_sessions = max(0, _state.active_sessions - 1)


def record_turn_completed() -> None:
    with _LOCK:
        _state.turns_total += 1


def record_tool_call(tool_name: str, duration_ms: float, error: bool = False) -> None:
    with _LOCK:
        _state.tool_calls_total += 1
        if error:
            _state.tool_call_errors_total += 1
        # Cap per-tool history to prevent unbounded growth
        hist = _state.tool_call_duration_ms[tool_name]
        hist.append(duration_ms)
        if len(hist) > 100:
            hist.pop(0)


def record_llm_call(
    duration_ms: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    error: bool = False,
) -> None:
    with _LOCK:
        _state.llm_calls_total += 1
        if error:
            _state.llm_call_errors_total += 1
        _state.llm_tokens_prompt += prompt_tokens
        _state.llm_tokens_completion += completion_tokens
        _state.llm_call_duration_ms.append(duration_ms)
        if len(_state.llm_call_duration_ms) > 200:
            _state.llm_call_duration_ms.pop(0)


def record_compression() -> None:
    with _LOCK:
        _state.compressions_total += 1


def record_fatigue_bridge() -> None:
    with _LOCK:
        _state.fatigue_bridges_total += 1


def record_prune() -> None:
    with _LOCK:
        _state.prune_calls_total += 1


def record_continuity_load(sources_loaded: list, sources_failed: list) -> None:
    with _LOCK:
        _state.continuity_loads_total += 1
        _state.continuity_source_failures_total += len(sources_failed)


def record_pipeline_stage(stage_name: str, error: bool = False) -> None:
    with _LOCK:
        _state.pipeline_stages_total += 1
        if error:
            _state.pipeline_stage_failures_total += 1


def record_error(category: str) -> None:
    with _LOCK:
        _state.error_counts[category] += 1


def render_prometheus() -> str:
    """Render metrics in Prometheus exposition text format."""
    with _LOCK:
        s = _state
        uptime = time.time() - s.start_time
        lines: list[str] = []

        def _counter(name: str, help_text: str, value: int) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        def _gauge(name: str, help_text: str, value: float) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        _gauge("vermes_uptime_seconds", "Agent uptime in seconds", uptime)
        _gauge("vermes_active_sessions", "Currently active sessions", s.active_sessions)
        _counter("vermes_sessions_created_total", "Total sessions created", s.sessions_created_total)
        _counter("vermes_sessions_closed_total", "Total sessions closed", s.sessions_closed_total)
        _counter("vermes_turns_total", "Total turns completed", s.turns_total)
        _counter("vermes_tool_calls_total", "Total tool calls", s.tool_calls_total)
        _counter("vermes_tool_call_errors_total", "Total tool call errors", s.tool_call_errors_total)
        _counter("vermes_llm_calls_total", "Total LLM API calls", s.llm_calls_total)
        _counter("vermes_llm_call_errors_total", "Total LLM API call errors", s.llm_call_errors_total)
        _counter("vermes_llm_tokens_prompt_total", "Total prompt tokens consumed", s.llm_tokens_prompt)
        _counter("vermes_llm_tokens_completion_total", "Total completion tokens consumed", s.llm_tokens_completion)
        _counter("vermes_compressions_total", "Total context compressions", s.compressions_total)
        _counter("vermes_fatigue_bridges_total", "Total fatigue bridges", s.fatigue_bridges_total)
        _counter("vermes_prune_calls_total", "Total prune calls", s.prune_calls_total)
        _counter("vermes_continuity_loads_total", "Total continuity context loads", s.continuity_loads_total)
        _counter("vermes_continuity_source_failures_total", "Total continuity source failures", s.continuity_source_failures_total)
        _counter("vermes_pipeline_stages_total", "Total pipeline stages executed", s.pipeline_stages_total)
        _counter("vermes_pipeline_stage_failures_total", "Total pipeline stage failures", s.pipeline_stage_failures_total)

        # Per-tool call counts
        for tool_name, durations in sorted(s.tool_call_duration_ms.items()):
            count = len(durations)
            avg_ms = sum(durations) / count if count else 0
            lines.append(f'# HELP vermes_tool_call_count_{tool_name} Total calls for tool {tool_name}')
            lines.append(f'# TYPE vermes_tool_call_count_{tool_name} counter')
            lines.append(f'vermes_tool_call_count_{tool_name} {count}')
            lines.append(f'# HELP vermes_tool_call_avg_ms_{tool_name} Avg duration ms for tool {tool_name}')
            lines.append(f'# TYPE vermes_tool_call_avg_ms_{tool_name} gauge')
            lines.append(f'vermes_tool_call_avg_ms_{tool_name} {avg_ms:.1f}')

        # Error categories
        for cat, cnt in sorted(s.error_counts.items()):
            lines.append(f'# HELP vermes_error_{cat} Errors in category {cat}')
            lines.append(f'# TYPE vermes_error_{cat} counter')
            lines.append(f'vermes_error_{cat} {cnt}')

        # LLM call latency stats
        if s.llm_call_duration_ms:
            llm_durations = s.llm_call_duration_ms
            llm_count = len(llm_durations)
            llm_avg = sum(llm_durations) / llm_count
            lines.append(f'# HELP vermes_llm_call_avg_ms Average LLM call duration in ms')
            lines.append(f'# TYPE vermes_llm_call_avg_ms gauge')
            lines.append(f'vermes_llm_call_avg_ms {llm_avg:.1f}')

        return "\n".join(lines) + "\n"
