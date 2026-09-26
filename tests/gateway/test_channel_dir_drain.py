"""治本③：channel-directory 刷新不堆叠 + 关停 drain 覆盖阻塞（WorkBuddy 2026-09-26）。"""

from __future__ import annotations

import inspect


def test_cron_ticker_skips_when_refresh_inflight():
    """上一次刷新未完成 → 跳过本轮，不堆叠 fut.result 阻塞。"""
    import gateway.run as gr
    src = inspect.getsource(gr._start_cron_ticker)
    assert "_channel_dir_inflight" in src
    assert "skipping this cycle" in src
    assert "CHANNEL_DIR_RESULT_TIMEOUT" in src


def test_shutdown_drain_covers_channel_dir_block():
    """join 必须覆盖 fut.result(30) + margin（上游 _HOUSEKEEPING 35s）。"""
    import gateway.run as gr
    src = inspect.getsource(gr.start_gateway)
    assert "_CRON_TICKER_DRAIN_TIMEOUT = 35.0" in src
    # 旧值 5s 不得回归
    assert "cron_thread.join(timeout=5)" not in src
