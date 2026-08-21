"""L2a 能力索引注册表（进程内单例）。

由 SoftwareAdapter.register() 在发现工具时顺带 build 并写入（§15.4）。
route_toolset() 消费此索引做倒排粗筛。fail-open：无索引时返回空列表，不阻断 L2。
"""

from __future__ import annotations

from typing import Optional

from .discovery import CapabilityIndex


class CapabilityRegistry:
    """进程内 CapabilityIndex 集合 + 倒排索引。"""

    def __init__(self) -> None:
        self._by_toolset: dict[str, CapabilityIndex] = {}

    def add(self, cap: CapabilityIndex) -> None:
        # 同 toolset 重复注册覆盖（discover 幂等）
        self._by_toolset[cap.toolset] = cap

    def get(self, toolset: str) -> Optional[CapabilityIndex]:
        return self._by_toolset.get(toolset)

    def all(self) -> list[CapabilityIndex]:
        return list(self._by_toolset.values())

    def clear(self) -> None:
        self._by_toolset.clear()


# 模块级单例（L2 register 时写入，route_toolset 默认消费）
CAPABILITY_REGISTRY = CapabilityRegistry()
