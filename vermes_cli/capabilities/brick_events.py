"""
Brick change broadcaster — 轻量事件总线 + SSE 端点。

后端在技能安装/卸载、模块安装/卸载、工具注册/deregister、MCP 变更时
调用 publish()，前端通过 SSE 订阅实时刷新各 Agent 管理面板。

设计：
- 内存级 asyncio.Queue 每连接一个队列，publish 时广播到所有订阅者
- 同步线程安全调用（reload_module_tools 在子线程跑，用 call_soon_threadsafe）
- 事件丢失不补发（快照刷新兜底），fail-open 不阻断任何安装/注册流程
"""
import asyncio
import logging
import time
from typing import Set

from fastapi import Request
from fastapi.responses import StreamingResponse

_log = logging.getLogger(__name__)

# 事件类型常量
EVENT_SKILL_INSTALLED = "skill.installed"
EVENT_SKILL_UNINSTALLED = "skill.uninstalled"
EVENT_MODULE_INSTALLED = "module.installed"
EVENT_MODULE_UNINSTALLED = "module.uninstalled"
EVENT_TOOL_REGISTERED = "tool.registered"
EVENT_TOOL_DEREGISTERED = "tool.deregistered"
EVENT_MCP_CHANGED = "mcp.changed"
EVENT_BRICK_CHANGED = "brick.changed"  # 通用兜底

# 每个订阅者的队列
_subscribers: Set[asyncio.Queue] = set()
# 主事件循环引用（首次 publish 或 register 时绑定）
_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    """绑定主事件循环，供子线程 call_soon_threadsafe 使用。"""
    global _loop
    _loop = loop


def publish(event_type: str, payload: dict | None = None) -> None:
    """广播事件到所有 SSE 订阅者。

    线程安全：从子线程调用时用 call_soon_threadsafe。
    fail-open：任何异常静默吞掉，不阻断安装/注册流程。
    """
    try:
        data = {
            "type": event_type,
            "payload": payload or {},
            "ts": time.time(),
        }
        if _loop and _loop.is_running():
            _loop.call_soon_threadsafe(_do_broadcast, data)
        else:
            # 事件循环未就绪（启动早期），直接同步广播
            _do_broadcast(data)
    except Exception as exc:
        _log.debug("brick event publish failed (fail-open): %s", exc)


def _do_broadcast(data: dict) -> None:
    """实际广播（须在事件循环线程内调用）。"""
    dead: list[asyncio.Queue] = []
    for q in _subscribers:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers.discard(q)


def _register_subscriber() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _subscribers.add(q)
    return q


def _unregister_subscriber(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


async def brick_events_stream(request: Request):
    """SSE 端点：GET /api/bricks/events

    前端用 EventSource 订阅，收到事件后刷新对应组件数据。
    """
    # 绑定当前事件循环
    set_loop(asyncio.get_running_loop())

    q = _register_subscriber()

    async def stream():
        try:
            # 首条事件：快照指令（前端收到后立即拉一次全量）
            yield f"data: {__import__('json').dumps({'type': 'snapshot', 'payload': {}, 'ts': time.time()})}\n\n"
            while not request.is_disconnected():
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30.0)
                    import json
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 心跳保活
                    yield f": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _unregister_subscriber(q)

    return StreamingResponse(stream(), media_type="text/event-stream")


def register_to(app):
    """模块级注册（在 mount_spa 之前，避免 catch-all 拦截）。"""
    app.add_api_route(
        "/api/bricks/events",
        brick_events_stream,
        methods=["GET"],
        name="brick_events_sse",
    )
