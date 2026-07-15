"""
Blueprint 共享状态（避免循环导入）

注意：仅放置被多个 Blueprint 共享的全局状态。
"""

import asyncio
import concurrent.futures
import atexit
from typing import Dict

# SSE 流式生成追踪（chat + stop_generation 共用）
# 所有蓝图统一从 blueprints.state 导入此变量
_active_streams: dict = {}

# ── 全局共享 ThreadPoolExecutor ─────────────────────────────────────────────
# 旧代码每次请求创建一个新的 ThreadPoolExecutor(max_workers=1) 但从不 shutdown,
# 导致线程泄漏 + CLOSE_WAIT 累积 + 长时间使用后 "Failed to fetch"。
# 改为全局共享池，请求复用而非每次新建。
_agent_executor: concurrent.futures.ThreadPoolExecutor | None = None


def get_agent_executor() -> concurrent.futures.ThreadPoolExecutor:
    """返回全局共享的 Agent 执行线程池（懒初始化）。"""
    global _agent_executor
    if _agent_executor is None or _agent_executor._shutdown:
        _agent_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="vermes-agent",
        )
    return _agent_executor


def _cleanup_executor() -> None:
    """进程退出时清理线程池。"""
    global _agent_executor
    if _agent_executor is not None and not _agent_executor._shutdown:
        _agent_executor.shutdown(wait=False, cancel_futures=True)


atexit.register(_cleanup_executor)
