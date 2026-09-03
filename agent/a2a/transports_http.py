"""A2A HTTP transport —— HTTP-API 外部 agent（豆包 / 百度搭子 / 大厂 SaaS）。

复用 outbound_webhook（gateway/platforms/webhook.py:14）+ 204 providers
注册表做鉴权与路由。初版透传 + async 发送骨架（httpx），联邦成熟后接
真实 HTTP 端点。
"""

from __future__ import annotations

from typing import Any

from agent.a2a.transports import AgentTransport, register_a2a_transport
from agent.a2a.types import A2AEnvelope


class HttpTransport(AgentTransport):
    name = "http"
    capabilities = ("http", "api")

    def send(self, envelope: A2AEnvelope) -> Any:
        return {
            "transport": self.name,
            "kind": envelope.kind,
            "to": envelope.to_handle,
            "payload": envelope.payload,
        }

    async def asend(self, envelope: A2AEnvelope) -> Any:
        """异步 HTTP POST 骨架（联邦成熟后接真实端点 + 鉴权）。"""
        return self.send(envelope)


register_a2a_transport("http", HttpTransport)
