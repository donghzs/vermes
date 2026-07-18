"""Tests for gateway.memory_monitor — periodic process memory logging.

Ported from cline/cline#10343.  The module logs a structured
``[MEMORY] rss=...MB ...`` line periodically so long-running gateway
leaks show up as a time series in agent.log / gateway.log.
"""

from __future__ import annotations

import logging
import time

import pytest

from gateway import memory_monitor as mm


@pytest.fixture(autouse=True)
def _ensure_monitor_stopped():
    """Every test starts from a clean state and leaves one behind."""
    mm.stop_memory_monitoring(timeout=1.0)
    yield
    mm.stop_memory_monitoring(timeout=1.0)


def test_log_memory_usage_emits_memory_line(caplog):
    caplog.set_level(logging.INFO, logger="gateway.memory_monitor")
    mm.log_memory_usage()
    memory_lines = [r for r in caplog.records if "[MEMORY]" in r.getMessage()]
    assert memory_lines, "expected at least one [MEMORY] log record"


def test_log_memory_usage_has_grep_friendly_format(caplog):
    caplog.set_level(logging.INFO, logger="gateway.memory_monitor")
    mm.log_memory_usage()
    msg = caplog.records[-1].getMessage()
    # Grep-friendly contract: line starts with [MEMORY] and carries RSS
    # (or 'unavailable'), GC counts, thread count, uptime.
    assert msg.startswith("[MEMORY]"), msg
    assert "rss=" in msg
    assert "gc=" in msg
    assert "threads=" in msg
    assert "uptime=" in msg


def test_log_memory_usage_with_prefix(caplog):
    caplog.set_level(logging.INFO, logger="gateway.memory_monitor")
    mm.log_memory_usage(prefix="baseline")
    msg = caplog.records[-1].getMessage()
    assert "[MEMORY] baseline " in msg


def test_start_logs_baseline_and_returns_true(caplog):
    caplog.set_level(logging.INFO, logger="gateway.memory_monitor")
    # Large interval so the background timer never fires during the test —
    # we're only checking the synchronous baseline behavior here.
    started = mm.start_memory_monitoring(interval_seconds=3600.0)
    assert started is True
    assert mm.is_running() is True

    messages = [r.getMessage() for r in caplog.records]
    assert any("[MEMORY] baseline " in m for m in messages), messages
    assert any("Periodic memory monitoring started" in m for m in messages), messages


def test_double_start_is_noop():
    assert mm.start_memory_monitoring(interval_seconds=3600.0) is True
    assert mm.start_memory_monitoring(interval_seconds=3600.0) is False
    assert mm.is_running() is True


def test_stop_logs_shutdown_snapshot(caplog):
    mm.start_memory_monitoring(interval_seconds=3600.0)
    caplog.clear()
    caplog.set_level(logging.INFO, logger="gateway.memory_monitor")
    mm.stop_memory_monitoring(timeout=1.0)
    assert mm.is_running() is False

    messages = [r.getMessage() for r in caplog.records]
    assert any("[MEMORY] shutdown " in m for m in messages), messages
    assert any("Periodic memory monitoring stopped" in m for m in messages), messages


def test_stop_without_start_is_noop():
    # Must not raise, must not log shutdown snapshot.
    mm.stop_memory_monitoring(timeout=0.5)
    assert mm.is_running() is False


def test_periodic_timer_fires(caplog):
    caplog.set_level(logging.INFO, logger="gateway.memory_monitor")
    # Short interval so we can observe multiple ticks inside the test budget.
    mm.start_memory_monitoring(interval_seconds=0.1)
    time.sleep(0.45)
    mm.stop_memory_monitoring(timeout=1.0)

    periodic = [
        r for r in caplog.records
        if r.getMessage().startswith("[MEMORY] rss=") or r.getMessage().startswith("[MEMORY] rss=unavailable")
    ]
    # baseline + at least 2 periodic + shutdown — but shutdown has the
    # "shutdown " prefix so it won't match the strict "[MEMORY] rss=" start.
    # We expect >= 3 bare "[MEMORY] rss=..." lines.
    assert len(periodic) >= 3, [r.getMessage() for r in caplog.records]


def test_thread_is_daemon():
    mm.start_memory_monitoring(interval_seconds=3600.0)
    assert mm._monitor_thread is not None
    assert mm._monitor_thread.daemon is True, (
        "memory monitor thread must be daemon so it can never block process exit"
    )


def test_unavailable_rss_warns_and_does_not_start(caplog, monkeypatch):
    # Force both backends to claim unavailable; start should bail.
    monkeypatch.setattr(mm, "_get_rss_mb", lambda: None)
    caplog.set_level(logging.WARNING, logger="gateway.memory_monitor")
    started = mm.start_memory_monitoring(interval_seconds=3600.0)
    assert started is False
    assert mm.is_running() is False
    assert any("Memory monitoring unavailable" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# P2.4 self-heal — _maybe_self_heal() unit tests                              #
# --------------------------------------------------------------------------- #
#
# These exercise the pure decision function directly (no thread), so they are
# fast and deterministic. gc.collect() is patched to a counter so we assert
# *whether* a collection was requested without depending on real GC timing.


def _fresh_state(gc_min_interval=60.0, **extra):
    state = {"last_gc_ts": 0.0, "gc_min_interval": gc_min_interval}
    state.update(extra)
    return state


def test_self_heal_none_rss_returns_none(monkeypatch):
    # When RSS can't be read, self-heal must be a no-op (never crash).
    calls = []
    monkeypatch.setattr(mm.gc, "collect", lambda *a, **k: calls.append(1))
    result = mm._maybe_self_heal(None, soft_limit_mb=100, hard_limit_mb=200,
                                 state=_fresh_state())
    assert result is None
    assert calls == []


def test_self_heal_below_all_limits_returns_none(monkeypatch):
    calls = []
    monkeypatch.setattr(mm.gc, "collect", lambda *a, **k: calls.append(1))
    result = mm._maybe_self_heal(50, soft_limit_mb=100, hard_limit_mb=200,
                                 state=_fresh_state())
    assert result is None
    assert calls == []


def test_self_heal_soft_limit_triggers_throttled_gc(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(mm.gc, "collect", lambda *a, **k: calls.append(1))
    caplog.set_level(logging.INFO, logger="gateway.memory_monitor")
    state = _fresh_state()

    result = mm._maybe_self_heal(150, soft_limit_mb=100, hard_limit_mb=None,
                                 state=state)
    assert result == "soft"
    assert len(calls) == 1
    assert state["last_gc_ts"] > 0.0
    assert any("soft-limit reached" in r.getMessage() for r in caplog.records)


def test_self_heal_soft_limit_is_throttled_by_cooldown(monkeypatch):
    calls = []
    monkeypatch.setattr(mm.gc, "collect", lambda *a, **k: calls.append(1))
    # last_gc_ts = now means we are inside the cooldown window.
    state = _fresh_state(gc_min_interval=3600.0, last_gc_ts=time.monotonic())

    result = mm._maybe_self_heal(150, soft_limit_mb=100, hard_limit_mb=None,
                                 state=state)
    # Within cooldown → no collection, and no "soft" action reported.
    assert result is None
    assert calls == []


def test_self_heal_hard_limit_warns_and_forces_gc(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(mm.gc, "collect", lambda *a, **k: calls.append(1))
    caplog.set_level(logging.WARNING, logger="gateway.memory_monitor")
    # Even inside the cooldown window, a hard breach forces a collection.
    state = _fresh_state(gc_min_interval=3600.0, last_gc_ts=time.monotonic())

    result = mm._maybe_self_heal(250, soft_limit_mb=100, hard_limit_mb=200,
                                 state=state)
    assert result == "hard"
    assert len(calls) == 1
    assert any(
        "hard-limit exceeded" in r.getMessage() and r.levelno == logging.WARNING
        for r in caplog.records
    )


def test_self_heal_hard_limit_invokes_alert_callback(monkeypatch):
    monkeypatch.setattr(mm.gc, "collect", lambda *a, **k: None)
    fired = []
    state = _fresh_state(alert_cb=lambda before, limit, after: fired.append(
        (before, limit, after)))

    result = mm._maybe_self_heal(300, soft_limit_mb=None, hard_limit_mb=200,
                                 state=state)
    assert result == "hard"
    assert len(fired) == 1
    before, limit, _after = fired[0]
    assert before == 300 and limit == 200


def test_self_heal_alert_callback_exception_is_swallowed(monkeypatch):
    monkeypatch.setattr(mm.gc, "collect", lambda *a, **k: None)

    def _boom(*a, **k):
        raise RuntimeError("callback blew up")

    state = _fresh_state(alert_cb=_boom)
    # Must not raise — an alert hook failure can never crash the monitor.
    result = mm._maybe_self_heal(300, soft_limit_mb=None, hard_limit_mb=200,
                                 state=state)
    assert result == "hard"


def test_self_heal_hard_takes_precedence_over_soft(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(mm.gc, "collect", lambda *a, **k: calls.append(1))
    caplog.set_level(logging.WARNING, logger="gateway.memory_monitor")
    # rss above both limits → hard branch wins.
    result = mm._maybe_self_heal(500, soft_limit_mb=100, hard_limit_mb=200,
                                 state=_fresh_state())
    assert result == "hard"


def test_start_forwards_limits_to_loop(monkeypatch):
    # start_memory_monitoring must thread the self-heal knobs down to the
    # loop verbatim. Capture the Thread kwargs to prove the plumbing.
    captured = {}

    class _FakeThread:
        def __init__(self, *args, **kwargs):
            captured["args"] = kwargs.get("args")
            captured["kwargs"] = kwargs.get("kwargs")
            self.daemon = kwargs.get("daemon", False)

        def start(self):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(mm.threading, "Thread", _FakeThread)
    mm.start_memory_monitoring(
        interval_seconds=123.0,
        soft_limit_mb=111,
        hard_limit_mb=222,
        gc_min_interval_seconds=33.0,
    )
    assert captured["kwargs"] == {
        "soft_limit_mb": 111,
        "hard_limit_mb": 222,
        "gc_min_interval": 33.0,
    }
