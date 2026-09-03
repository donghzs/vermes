"""A2A 传输抽象 + transport adapter registry（① 第 6 步）。

直接镜像 Vermes 已有的 `agent/transports` 可插拔范式
（ProviderTransport ABC + _REGISTRY + register_transport + _discover_transports），
只是语义对象从「provider 响应归一化」换成「agent 间消息收发」。

传输类别（对应 §5 ① 第 6 步）：
  - local       —— 本地进程内（delegate_tool subagent 委派原语）
  - subprocess  —— CLI 外部 agent（Codex / Claude Code，JSON-RPC/stdio）
  - mcp         —— stdio-MCP 外部 agent（OpenClaw / 扣子 / WorkBuddy·QClaw）
  - http        —— HTTP-API 外部 agent（豆包 / 百度搭子 / 大厂 SaaS）
  - ws          —— WS/SSE（预留，实时双向）

初版落地 local + subprocess + mcp + http 四个 adapter 工厂；
ws 只声明接口，外部 agent 接满后再补实现（规格明示）。

每个 adapter 声明 capability 标签，随注册写入 AgentRegistry 供调度匹配。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from agent.a2a.types import A2AEnvelope

# transport 名 -> adapter 工厂类
_A2A_REGISTRY: Dict[str, type] = {}
_discovered: bool = False


class AgentTransport(ABC):
    """A2A 传输适配器抽象基类。

    一个 adapter 负责一条外部 agent 数据通路：send(envelope) 发出，
    recv() 收一条（若该通路支持同步拉取，否则返回 None / 抛 NotImplemented）。
    """

    # 该 transport 的标识（与 registry key 一致）
    name: str = ""

    # 能力标签（code/search/writing/vision/...），供调度按标签派活
    capabilities: tuple = ()

    @abstractmethod
    def send(self, envelope: A2AEnvelope) -> Any:
        """把信封发到目标 agent，返回底层实现的结果（或 None）。"""
        ...

    def recv(self) -> Optional[A2AEnvelope]:
        """同步拉取一条入站信封（支持时）。默认不支持。"""
        return None

    async def asend(self, envelope: A2AEnvelope) -> Any:
        """异步发送（HTTP/WS 等适配器覆写）。默认同步 send 包装。"""
        return self.send(envelope)


def register_a2a_transport(name: str, transport_cls: type) -> None:
    """注册一个 A2A transport adapter 工厂类。"""
    _A2A_REGISTRY[name] = transport_cls


def get_a2a_transport(name: str) -> Optional[AgentTransport]:
    """获取 transport 实例；未注册返回 None（渐进迁移，调用方判空回退）。"""
    global _discovered
    if not _discovered:
        _discover_transports()
    cls = _A2A_REGISTRY.get(name)
    if cls is None:
        _discover_transports()  # 部分导入场景，miss 时再发现一次
        cls = _A2A_REGISTRY.get(name)
    if cls is None:
        return None
    return cls()


def list_a2a_transports() -> List[str]:
    """列出所有已注册 transport 名。"""
    global _discovered
    if not _discovered:
        _discover_transports()
    return sorted(_A2A_REGISTRY.keys())


def _discover_transports() -> None:
    """导入所有 transport 模块以触发自动注册（mirror provider 层 idiom）。"""
    global _discovered
    _discovered = True
    for _mod in ("local", "subprocess", "mcp", "http"):
        try:
            __import__(f"agent.a2a.transports_{_mod}")
        except ImportError:
            pass
