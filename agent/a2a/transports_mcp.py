"""A2A MCP transport —— stdio-MCP 外部 agent（OpenClaw / 扣子 / WorkBuddy·QClaw）。

复用 mcp_tool.py + hermes_tools_mcp_server.py 的 stdio-MCP 范式。
初版透传：adapter 注册进 registry 并声明能力，实际 MCP 通道调用由
调用方在联邦成熟后经 tools.mcp_tool 接入。
"""

from __future__ import annotations

from typing import Any

from agent.a2a.transports import AgentTransport, register_a2a_transport
from agent.a2a.types import A2AEnvelope


class McpTransport(AgentTransport):
    name = "mcp"
    capabilities = ("mcp", "tool")

    def send(self, envelope: A2AEnvelope) -> Any:
        return {
            "transport": self.name,
            "kind": envelope.kind,
            "to": envelope.to_handle,
            "payload": envelope.payload,
        }


register_a2a_transport("mcp", McpTransport)
