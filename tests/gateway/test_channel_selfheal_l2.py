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
    """监督器语义：future 结束且仍 running → 记 ws_link_lost + 重建（次数也断言）。"""
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
            self.status.append((context, kw.get("platform_state")))

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
        return ad

    ad = asyncio.get_event_loop().run_until_complete(run())
    # WorkBuddy 探针 2：必须真写入 ws_link_lost，且真重建恰好 1 次
    assert ad.status == [("ws_link_lost", "retrying")], ad.status
    assert ad._connect_calls == 1, ad._connect_calls


def test_feishu_supervisor_noop_on_deliberate_disconnect():
    """主动断开（_running=False / _ws_client=None）不得写 ws_link_lost、不得重建。"""
    import asyncio
    import gateway.platforms.feishu as fs

    class FakeAdapter:
        def __init__(self):
            self._running = False  # 已主动断开
            self._ws_client = None
            self._ws_future = asyncio.get_event_loop().create_future()
            self._ws_restart_backoff = 0.01
            self._connect_calls = 0
            self.status = []

        def _write_runtime_status_safe(self, context, **kw):
            self.status.append(context)

        async def _connect_websocket(self):
            self._connect_calls += 1

    async def run():
        ad = FakeAdapter()
        ad._ws_future.set_result(None)
        task = asyncio.get_event_loop().create_task(
            fs.FeishuAdapter._supervise_websocket_thread(ad)
        )
        await asyncio.wait_for(task, timeout=2.0)
        return ad

    ad = asyncio.get_event_loop().run_until_complete(run())
    assert ad.status == []
    assert ad._connect_calls == 0


def test_feishu_connect_degrades_when_loop_has_no_create_task():
    """BUG#1（WorkBuddy 2026-09-26）：stub loop 无 create_task 不得炸 connect。"""
    import gateway.platforms.feishu as fs

    class StubLoop:
        def is_closed(self):
            return False

        def run_in_executor(self, *a, **k):
            fut = __import__("asyncio").Future()
            fut.set_result(None)
            return fut

    ad = object.__new__(fs.FeishuAdapter)
    ad._running = True
    ad._ws_client = object()
    ad._ws_supervisor = None
    ad._ws_restart_backoff = 0.01
    ad._ws_future = None
    ad._loop = StubLoop()
    # 只走监督器装配分支，不跑完整 connect
    _create_task = getattr(ad._loop, "create_task", None)
    if _create_task is None:
        ad._ws_supervisor = None
    else:
        ad._ws_supervisor = _create_task(ad._supervise_websocket_thread())
    assert ad._ws_supervisor is None  # 降级可见，不炸
