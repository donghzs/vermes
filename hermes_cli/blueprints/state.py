"""
Blueprint 共享状态（避免循环导入）

注意：仅放置被多个 Blueprint 共享的全局状态。
"""

import asyncio
from typing import Dict

# SSE 流式生成追踪（chat + stop_generation 共用）
# 所有蓝图统一从 blueprints.state 导入此变量
_active_streams: dict = {}
