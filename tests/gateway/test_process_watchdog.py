"""L3/L4 watchdog contract tests (Hermes 2026-09-25 治本①)."""

from __future__ import annotations

import time

import pytest

from gateway.watchdog import GatewayWatchdog, _self_cpu_seconds


def test_cpu_fn_returns_float():
    v = _self_cpu_seconds()
    assert isinstance(v, float)
    assert v >= 0.0


def test_watchdog_fires_on_control_strikes():
    """Control unresponsive × strikes → on_trigger called (and would exit)."""
    fired = []
    wd = GatewayWatchdog(
        interval=0.01,
        strikes=3,
        probe=lambda: False,
        cpu_fn=lambda: 0.0,
        on_trigger=fired.append,
        enabled=True,
    )
    # 禁用真实 os._exit：只测判据
    wd._trigger = lambda reason: fired.append(reason)  # type: ignore
    wd.start()
    time.sleep(0.12)
    wd.stop()
    assert fired, "control strikes should have fired"
    assert any("control" in str(r) for r in fired)


def test_watchdog_fires_on_cpu_strikes():
    fired = []
    # CPU 每采样 +10s，interval=0.01 → frac 爆表
    counter = {"t": 0.0}

    def cpu():
        counter["t"] += 10.0
        return counter["t"]

    wd = GatewayWatchdog(
        interval=0.01,
        cpu_limit=0.90,
        strikes=3,
        probe=lambda: True,
        cpu_fn=cpu,
        on_trigger=fired.append,
        enabled=True,
    )
    wd._trigger = lambda reason: fired.append(reason)  # type: ignore
    wd.start()
    time.sleep(0.15)
    wd.stop()
    assert any("CPU" in str(r) for r in fired)


def test_watchdog_does_not_fire_when_healthy():
    fired = []
    t0 = time.time()

    def cpu():
        return time.time() - t0  # 每秒 ~1 CPU 秒？不 —— interval 内增量 / interval
        # 用单调时间差会得到 frac≈1。改为返回常量，增量 0。

    wd = GatewayWatchdog(
        interval=0.01,
        strikes=2,
        probe=lambda: True,
        cpu_fn=lambda: 1.0,  # 常量 → delta 0
        on_trigger=fired.append,
        enabled=True,
    )
    wd._trigger = lambda reason: fired.append(reason)  # type: ignore
    wd.start()
    time.sleep(0.08)
    wd.stop()
    assert fired == []


def test_watchdog_disabled_is_noop():
    wd = GatewayWatchdog(enabled=False, on_trigger=lambda r: None)
    wd.start()
    assert wd._thread is None
    wd.stop()


def test_channel_directory_exception_is_warning_not_debug():
    """L-035 空异常升级：run.py 必须用 warning + repr，不再 debug+%s 吞空串。"""
    import inspect
    import gateway.run as gr
    src = inspect.getsource(gr._start_cron_ticker)
    assert "Channel directory refresh error: %r" in src
    assert 'logger.debug("Channel directory refresh error: %s"' not in src
    assert "Channel directory refresh wait failed/timeout: %r" in src
