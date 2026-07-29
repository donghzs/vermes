"""tool_search stub — 消除 agent/tool_executor.py 和 model_tools.py 的 ImportError。

上游 Vermes 的 tool_search 模块提供动态工具搜索功能。
Vermes 桌面版不需要此功能（工具列表在启动时静态加载），
但 agent/tool_executor.py 和 model_tools.py 会 import 它。
"""

import json
from typing import Any, Dict, List, FrozenSet, Optional
from dataclasses import dataclass, field

# 工具调用名称常量
TOOL_CALL_NAME = "tool_search"
TOOL_SEARCH_NAME = "tool_search"
TOOL_DESCRIBE_NAME = "tool_describe"

# Bridge tools（连接外部服务的工具）
_BRIDGE_TOOLS = frozenset({
    'web_search', 'web_extract', 'browser_navigate', 'browser_click',
    'browser_type', 'browser_snapshot', 'browser_vision',
    'vision_analyze', 'image_gen', 'video_generate',
    'spotify', 'send_message',
})


@dataclass
class ToolSearchConfig:
    """Tool search configuration."""
    enabled: str = "off"
    threshold_tokens: int = 8000


@dataclass
class AssemblyResult:
    """Result of tool search assembly."""
    activated: bool = False
    deferred_count: int = 0
    deferred_tokens: int = 0
    threshold_tokens: int = 0
    tool_defs: list = field(default_factory=list)


def scoped_deferrable_names(tool_defs: List[Dict[str, Any]]) -> FrozenSet[str]:
    """返回可延迟执行的工具名称集合。Vermes 不使用此功能。"""
    return frozenset()


def resolve_underlying_call(function_args: Dict[str, Any]) -> tuple:
    """解析底层工具调用。Vermes 不使用此功能。"""
    return None, {}, "tool_search not available in Vermes desktop"


def is_bridge_tool(tool_name: str) -> bool:
    """Check if a tool is a bridge tool (connects to external services)."""
    return tool_name in _BRIDGE_TOOLS


def load_config() -> ToolSearchConfig:
    """Load tool search configuration. Returns disabled config."""
    return ToolSearchConfig(enabled="off")


def assemble_tool_defs(tool_defs: list, context_length: int = 0, config: Optional[ToolSearchConfig] = None) -> AssemblyResult:
    """Assemble tool definitions. Vermes returns tools as-is (no deferral)."""
    return AssemblyResult(
        activated=False,
        deferred_count=0,
        deferred_tokens=0,
        threshold_tokens=0,
        tool_defs=tool_defs,
    )


def dispatch_tool_search(function_args: Dict[str, Any], current_tool_defs: Optional[list] = None) -> str:
    """Dispatch tool_search call. Vermes returns empty result."""
    return json.dumps({"results": [], "message": "tool_search not available in Vermes desktop"}, ensure_ascii=False)


def dispatch_tool_describe(function_args: Dict[str, Any], current_tool_defs: Optional[list] = None) -> str:
    """Dispatch tool_describe call. Vermes returns empty result."""
    return json.dumps({"description": "", "message": "tool_describe not available in Vermes desktop"}, ensure_ascii=False)
