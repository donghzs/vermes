"""Periodic process memory usage logging for the gateway.

Ported from cline/cline#10343 (src/standalone/memory-monitor.ts).

The gateway is a long-lived process that accumulates memory as it caches
agent instances, session transcripts, tool schemas, memory providers, MCP
connections, etc.  A slow leak in any of those subsystems is invisible
in a single log line — you only see it by watching RSS climb over hours.

This module emits a single structured ``[MEMORY] ...`` line every N
minutes (default 5) so maintainers investigating a suspected leak can
grep ``agent.log`` / ``gateway.log`` for a time series of RSS + Python
GC stats.  The timer runs in a background thread and shuts down cleanly
with the gateway.

Design notes (parity with the Cline port):
  * Grep-friendly single-line format beginning ``[MEMORY]``.
  * Final snapshot logged on shutdown so "last RSS before exit" is
    always in the log.
  * Baseline snapshot logged immediately on start.
  * Daemon thread — never blocks process exit.
  * Uses ``resource`` (stdlib, Linux/macOS) first and falls back to
    ``psutil`` when ``resource`` isn't available (Windows).  Both are
    optional; when neither works we emit a single WARNING and disable
    the monitor rather than crashing the gateway.

Config: ``logging.memory_monitor`` in ``config.yaml`` — see
``vermes_cli/config.py`` for the defaults block.
"""

from __future__ import annotations

import gc
import logging
import os
import sys
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_BYTES_TO_MB = 1024 * 1024

_monitor_thread: Optional[threading.Thread] = None
_stop_event: Optional[threading.Event] = None
_start_time: Optional[float] = None
_interval_seconds: float = 300.0  # 5 minutes
_lock = threading.Lock()


def _get_rss_mb() -> Optional[int]:
    """Return current process resident set size in MB, or None if unavailable.

    Tries ``resource.getrusage`` first (Linux/macOS, no extra deps), then
    falls back to ``psutil`` which is an optional Vermes-agent dep.
    """
    # Linux / macOS — resource is stdlib.  On Linux ru_maxrss is in KB,
    # on macOS it is in bytes (yes, really).  We use it as a cheap
    # "current" RSS — ru_maxrss reports the high-water mark for the
    # process, which is what you actually want for leak detection.
    try:
        import resource

        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return int(maxrss / _BYTES_TO_MB)
        # Linux / other unices: KB
        return int(maxrss / 1024)
    except Exception:
        pass

    # Fallback: psutil (Windows, or unusual unix without resource).
    try:
        import psutil  # type: ignore

        rss = psutil.Process(os.getpid()).memory_info().rss
        return int(rss / _BYTES_TO_MB)
    except Exception:
        return None


def log_memory_usage(prefix: str = "") -> None:
    """Log current memory usage in a grep-friendly ``[MEMORY] ...`` line.

    Safe to call on-demand from any thread at important lifecycle
    moments (after shutdown, after context compression, etc.).

    Parameters
    ----------
    prefix
        Optional extra tag inserted after ``[MEMORY]`` — e.g.
        ``"baseline"``, ``"shutdown"``.
    """
    rss = _get_rss_mb()
    uptime = int(time.monotonic() - _start_time) if _start_time else 0
    # gc.get_stats() returns per-generation collection counts; the sum
    # is a cheap proxy for "how much garbage have we created".
    try:
        gc_counts = gc.get_count()  # (gen0, gen1, gen2)
    except Exception:
        gc_counts = (0, 0, 0)
    # Thread count is a handy correlate when diagnosing thread leaks.
    try:
        thread_count = threading.active_count()
    except Exception:
        thread_count = 0

    tag = f"{prefix} " if prefix else ""
    if rss is None:
        logger.info(
            "[MEMORY] %srss=unavailable gc=%s threads=%d uptime=%ds",
            tag,
            gc_counts,
            thread_count,
            uptime,
        )
    else:
        logger.info(
            "[MEMORY] %srss=%dMB gc=%s threads=%d uptime=%ds",
            tag,
            rss,
            gc_counts,
            thread_count,
            uptime,
        )


def _maybe_self_heal(
    rss_mb: Optional[int],
    soft_limit_mb: Optional[int],
    hard_limit_mb: Optional[int],
    state: dict,
) -> Optional[str]:
    """React to RSS crossing configured thresholds. Returns the action taken.

    Both limits are opt-in (``None`` disables). Behaviour:

    * ``rss >= hard_limit_mb`` → **WARNING** alert + a forced ``gc.collect()``
      (ignores cooldown — a hard breach is worth a collection every tick) and,
      if registered, an alert callback. Returns ``"hard"``.
    * ``rss >= soft_limit_mb`` → ``gc.collect()`` throttled by
      ``state['gc_min_interval']`` so we don't thrash the collector, logged at
      INFO with the amount freed. Returns ``"soft"``.
    * otherwise → ``None``.

    ``state`` is a mutable dict owned by the monitor loop; this function only
    reads/writes ``state['last_gc_ts']`` and ``state['gc_min_interval']``.
    Kept as a free function (not a closure) so it is unit-testable in isolation.
    """
    if rss_mb is None:
        return None

    now = time.monotonic()

    if hard_limit_mb is not None and rss_mb >= hard_limit_mb:
        before = rss_mb
        try:
            gc.collect()
        except Exception:
            pass
        state["last_gc_ts"] = now
        after = _get_rss_mb() or before
        logger.warning(
            "[MEMORY] hard-limit exceeded: rss=%dMB >= %dMB — forced gc.collect() "
            "(now %dMB, freed %dMB). Investigate a possible leak.",
            before, hard_limit_mb, after, max(0, before - after),
        )
        cb = state.get("alert_cb")
        if cb is not None:
            try:
                cb(before, hard_limit_mb, after)
            except Exception as exc:  # never let an alert hook crash the monitor
                logger.debug("Memory alert callback failed: %s", exc)
        return "hard"

    if soft_limit_mb is not None and rss_mb >= soft_limit_mb:
        last = state.get("last_gc_ts", 0.0)
        if now - last >= state.get("gc_min_interval", 60.0):
            before = rss_mb
            try:
                gc.collect()
            except Exception:
                pass
            state["last_gc_ts"] = now
            after = _get_rss_mb() or before
            logger.info(
                "[MEMORY] soft-limit reached: rss=%dMB >= %dMB — gc.collect() "
                "freed %dMB (now %dMB)",
                before, soft_limit_mb, max(0, before - after), after,
            )
            return "soft"

    return None


def _monitor_loop(
    stop_event: threading.Event,
    interval: float,
    soft_limit_mb: Optional[int] = None,
    hard_limit_mb: Optional[int] = None,
    gc_min_interval: float = 60.0,
) -> None:
    """Background thread body — log every ``interval`` seconds until stopped.

    When ``soft_limit_mb`` / ``hard_limit_mb`` are set, each tick also runs
    :func:`_maybe_self_heal` after the usual ``[MEMORY]`` log line.
    """
    state = {"last_gc_ts": 0.0, "gc_min_interval": gc_min_interval}
    while not stop_event.wait(interval):
        try:
            log_memory_usage()
            if soft_limit_mb is not None or hard_limit_mb is not None:
                _maybe_self_heal(
                    _get_rss_mb(), soft_limit_mb, hard_limit_mb, state
                )
        except Exception as e:
            # Never let the monitor crash the gateway; just log and carry on.
            logger.debug("Memory monitor iteration failed: %s", e)


def start_memory_monitoring(
    interval_seconds: float = 300.0,
    *,
    soft_limit_mb: Optional[int] = None,
    hard_limit_mb: Optional[int] = None,
    gc_min_interval_seconds: float = 60.0,
) -> bool:
    """Start periodic memory usage logging in a daemon thread.

    Logs immediately to capture a baseline, then every ``interval_seconds``.
    Safe to call multiple times — subsequent calls are no-ops while the
    first monitor is still running.

    Parameters
    ----------
    interval_seconds
        How often to log.  Default 300s (5 minutes), matching the
        upstream cline/cline implementation.
    soft_limit_mb
        When set, every tick where ``rss >= soft_limit_mb`` triggers a
        throttled ``gc.collect()`` (self-heal). ``None`` disables — the
        default, so existing callers keep the pure-logging behaviour.
    hard_limit_mb
        When set, every tick where ``rss >= hard_limit_mb`` logs a WARNING
        alert and forces a ``gc.collect()`` regardless of cooldown.
    gc_min_interval_seconds
        Minimum spacing between soft-limit collections so the collector is
        not thrashed on a process that hovers around the soft limit.

    Returns
    -------
    bool
        True if a fresh monitor thread was started, False if one was
        already running or if memory introspection isn't available.
    """
    global _monitor_thread, _stop_event, _start_time, _interval_seconds

    with _lock:
        if _monitor_thread is not None and _monitor_thread.is_alive():
            return False

        # Sanity-check that we can read RSS at all.  If neither resource
        # nor psutil works, no point spinning a thread that can only log
        # "rss=unavailable" forever — warn once and bail.
        if _get_rss_mb() is None:
            logger.warning(
                "[MEMORY] Memory monitoring unavailable: neither resource.getrusage "
                "nor psutil could read process RSS — skipping periodic logging.",
            )
            return False

        _start_time = time.monotonic()
        _interval_seconds = float(interval_seconds)
        _stop_event = threading.Event()

        # Baseline snapshot before the loop starts.
        log_memory_usage(prefix="baseline")

        _monitor_thread = threading.Thread(
            target=_monitor_loop,
            args=(_stop_event, _interval_seconds),
            kwargs={
                "soft_limit_mb": soft_limit_mb,
                "hard_limit_mb": hard_limit_mb,
                "gc_min_interval": gc_min_interval_seconds,
            },
            name="gateway-memory-monitor",
            daemon=True,
        )
        _monitor_thread.start()

        if soft_limit_mb is not None or hard_limit_mb is not None:
            logger.info(
                "[MEMORY] Periodic memory monitoring started (interval: %ds, "
                "soft_limit=%s hard_limit=%s)",
                int(_interval_seconds),
                f"{soft_limit_mb}MB" if soft_limit_mb is not None else "off",
                f"{hard_limit_mb}MB" if hard_limit_mb is not None else "off",
            )
        else:
            logger.info(
                "[MEMORY] Periodic memory monitoring started (interval: %ds)",
                int(_interval_seconds),
            )
        return True


def stop_memory_monitoring(timeout: float = 2.0) -> None:
    """Stop the monitor thread and log a final snapshot.

    Safe to call even if ``start_memory_monitoring()`` was never called.
    """
    global _monitor_thread, _stop_event

    with _lock:
        if _stop_event is None or _monitor_thread is None:
            return

        # Final snapshot before teardown so "last RSS" is always in the log.
        try:
            log_memory_usage(prefix="shutdown")
        except Exception:
            pass

        _stop_event.set()
        thread = _monitor_thread
        _monitor_thread = None
        _stop_event = None

    # Join outside the lock so a stuck log call can't deadlock shutdown.
    try:
        thread.join(timeout=timeout)
    except Exception:
        pass

    logger.info("[MEMORY] Periodic memory monitoring stopped")


def is_running() -> bool:
    """True if the background monitor thread is alive."""
    with _lock:
        return _monitor_thread is not None and _monitor_thread.is_alive()
