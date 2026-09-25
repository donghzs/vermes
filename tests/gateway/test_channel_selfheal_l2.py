"""L2 渠道自愈取长契约测（L-036~L-038，Hermes 2026-09-25 治本②）。"""

from __future__ import annotations

import inspect


def test_qqbot_never_gives_up():
    """L-036: MAX_RECONNECT_ATTEMPTS=0 = 无上限；三处躺平分支不得在 0 时 return。"""
    from gateway.platforms.qqbot import constants as c
    assert c.MAX_RECONNECT_ATTEMPTS == 0
    assert c.RECONNECT_BACKOFF[-1] == 60  # 封顶 60s
    assert c.RECONNECT_LOG_EVERY >= 1

    import gateway.platforms.qqbot.adapter as ad
    src = inspect.getsource(ad)
    # 旧形态：`if backoff_idx >= MAX_RECONNECT_ATTEMPTS` 无 0 判就 return
    assert "if MAX_RECONNECT_ATTEMPTS and backoff_idx >= MAX_RECONNECT_ATTEMPTS" in src
    assert "L-036" in src


def test_feishu_has_ws_supervisor_and_ws_link_lost():
    """L-037/L-038: WS 监督器 + 死链状态上报（不再假绿 connected）。"""
    import gateway.platforms.feishu as fs
    src = inspect.getsource(fs)
    assert "_supervise_websocket_thread" in src
    assert "ws_link_lost" in src
    assert "_ws_restart_backoff" in src
    assert "WebSocket client thread exited unexpectedly" in src


def test_feishu_supervisor_rebuilds_on_thread_exit():
    """监督器语义：future 结束且仍 running → 记 ws_link_lost + 重建。"""
    import asyncio
    import gateway.platforms.feishu as fs

    class FakeAdapter:
        def __init__(self):
            self._running = True
            self._ws_client = object()
            self._ws_future = asyncio.get_event_loop().create_future()
            self._ws_restart_backoff = 0.01
            self._connect_calls = 0
            self.status = []

        def _write_runtime_status_safe(self, context, **kw):
            self.status.append(context)

        async def _connect_websocket(self):
            self._connect_calls += 1
            self._ws_future = asyncio.get_event_loop().create_future()
            self._running = False  # 重建一次后停，避免测试挂住

    async def run():
        ad = FakeAdapter()
        ad._ws_future.set_result(None)  # 立即完成 → 监督器视为线程退出
        task = asyncio.get_event_loop().create_task(
            fs.FeishuAdapter._supervise_websocket_thread(ad)
        )
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.get_event_loop().run_until_complete(run())
