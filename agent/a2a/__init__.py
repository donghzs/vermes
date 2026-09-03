"""A2A v1.0 协议（① P0 地基）——异构多智能体互操作的「电线协议」。

模块：
  - types       —— AgentHandle / A2AEnvelope / MessageKind（三股流动语义）
  - transports  —— AgentTransport ABC + registry + 自动发现（镜像 provider 层）
  - registry    —— AgentRegistry（注册/发现/路由/生命周期，持久化 SessionDB）
  - transports_* —— local / subprocess / mcp / http 四个 adapter

用法：
    from agent.a2a import AgentHandle, A2AEnvelope, MessageKind, AgentRegistry
    from agent.a2a.transports import get_a2a_transport, list_a2a_transports

详见 docs/plans/2026-09-02-vermes-catchup-roadmap-final.md §5 ①。
"""

from agent.a2a.types import (
    AgentHandle,
    A2AEnvelope,
    MessageKind,
    normalize_handle,
    parse_mention_handles,
)
from agent.a2a.registry import AgentRegistry

__all__ = [
    "AgentHandle",
    "A2AEnvelope",
    "MessageKind",
    "normalize_handle",
    "parse_mention_handles",
    "AgentRegistry",
]
