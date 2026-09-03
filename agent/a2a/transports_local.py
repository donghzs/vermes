"""A2A local transport —— 本地进程内（delegate_tool 委派原语）。

本地 agent 间消息不经网络：send 走 tools.delegate_tool.delegate_task 的
subagent 委派原语，把信封 kind/payload 映射为一次委派调用。

这是「同源多实例」场景的默认通路；「异构联邦」则走 subprocess/mcp/http。
"""

from __future__ import annotations

from typing import Any, Optional

from agent.a2a.transports import AgentTransport, register_a2a_transport
from agent.a2a.types import A2AEnvelope, MessageKind


class LocalTransport(AgentTransport):
    name = "local"
    capabilities = ("local",)

    def send(self, envelope: A2AEnvelope) -> Any:
        """把信封投递为一次本地委派。

        初版只落地「透传」：把 task/message 信封转成 delegate_task 的 goal；
        result/skill/tool/knowledge 信封本地不做复杂路由，返回结构化的
        payload 供调用方（registry 路由层）决策。
        """
        if envelope.kind in (MessageKind.TASK.value, MessageKind.MESSAGE.value):
            payload = envelope.payload or {}
            goal = payload.get("goal") or payload.get("text") or payload.get("message") or ""
            if goal:
                try:
                    from tools.delegate_tool import delegate_task
                    result = delegate_task(
                        goal=goal,
                        context=payload.get("context"),
                        toolsets=payload.get("toolsets"),
                        role=payload.get("role"),
                        background=bool(payload.get("background", False)),
                    )
                    return {"transport": self.name, "result": result}
                except Exception as exc:  # 委派失败不阻断协议层
                    return {"transport": self.name, "error": str(exc)}
        # 其余 kind：本地透传，返回 payload（无调度）
        return {"transport": self.name, "kind": envelope.kind, "payload": envelope.payload}


register_a2a_transport("local", LocalTransport)
