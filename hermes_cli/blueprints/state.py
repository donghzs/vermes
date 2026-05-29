"""
Blueprint 共享状态（避免循环导入）

注意：仅放置被多个 Blueprint 共享的全局状态。
"""

import asyncio
from typing import Dict

# SSE 流式生成追踪（chat + stop_generation 共用）
_active_streams: Dict[str, asyncio.Event] = {}
