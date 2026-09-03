"""A2A subprocess transport —— CLI 外部 agent（Codex / Claude Code）。

复用 codex_app_server.py 的 spawn 子进程 + JSON-RPC 范式。
初版落地为「工厂 + 能力声明」，实际 JSON-RPC 驱动在 codex_app_server.py
（已存在，见 §5 ① 范式已现成）；此处 adapter 负责把 A2AEnvelope 桥接过去。

外部 agent 接满后，send 才真正 spawn 子进程；现在先保证注册进 registry
并声明 capability，使能力发现与路由表可先行就位（透传语义，无复杂调度）。
"""

from __future__ import annotations

from typing import Any

from agent.a2a.transports import AgentTransport, register_a2a_transport
from agent.a2a.types import A2AEnvelope


class SubprocessTransport(AgentTransport):
    name = "subprocess"
    capabilities = ("code", "cli", "subprocess")

    def send(self, envelope: A2AEnvelope) -> Any:
        """CLI 外部 agent 桥接（Codex app-server JSON-RPC）。

        初版透传：返回结构化 payload + 目标 handle，实际 spawn 由调用方
        在联邦成熟后接入 codex_app_server.CodexAppServerClient。
        """
        return {
            "transport": self.name,
            "kind": envelope.kind,
            "to": envelope.to_handle,
            "payload": envelope.payload,
        }


register_a2a_transport("subprocess", SubprocessTransport)
