"""tool_search stub — 消除 agent/tool_executor.py 的 ImportError 警告。

上游 Hermes 的 tool_search 模块提供动态工具搜索功能。
Vermes 桌面版不需要此功能（工具列表在启动时静态加载），
但 agent/tool_executor.py 会 import 它，缺失时打印 WARNING。
"""

from typing import Any, Dict, List, FrozenSet

# 工具调用名称常量
TOOL_CALL_NAME = "tool_search"


def scoped_deferrable_names(tool_defs: List[Dict[str, Any]]) -> FrozenSet[str]:
    """返回可延迟执行的工具名称集合。Vermes 不使用此功能。"""
    return frozenset()


def resolve_underlying_call(function_args: Dict[str, Any]) -> tuple:
    """解析底层工具调用。Vermes 不使用此功能。"""
    return None, {}, "tool_search not available in Vermes desktop"
