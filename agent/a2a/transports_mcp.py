"""A2A MCP transport —— stdio-MCP 外部 agent。

S2 接满：经 AgentRegistry 解析目标，调用 tools.mcp_tool 的调用约定
（payload.server + payload.tool + payload.arguments）。
缺 MCP server 注册/未声明 tool → 结构化 error，不假装成功。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.a2a.transports import AgentTransport, register_a2a_transport
from agent.a2a.types import A2AEnvelope, MessageKind

logger = logging.getLogger(__name__)

_DISPATCHABLE = (MessageKind.MESSAGE.value, MessageKind.TASK.value, MessageKind.TOOL.value)


def _resolve_mcp_target(envelope: A2AEnvelope) -> Optional[dict]:
    payload = envelope.payload or {}
    server = (payload.get("server") or payload.get("mcp_server") or "").strip()
    tool = (payload.get("tool") or payload.get("tool_name") or "").strip()
    if server and tool:
        return {"server": server, "tool": tool, "arguments": payload.get("arguments") or {}}
    try:
        from agent.a2a.registry import AgentRegistry
        handle = AgentRegistry().resolve(envelope.to_handle)
        if handle is None:
            return None
        # transport_ref = MCP server 名；tool 必须由 payload 显式声明
        from vermes_state import SessionDB
        db = SessionDB()
        try:
            row = db._conn.execute(
                "SELECT transport_ref FROM agent_profiles WHERE id=? OR name=?",
                (handle.profile_id, handle.name or handle.profile_id),
            ).fetchone()
        finally:
            try:
                db.close()
            except Exception:
                pass
        ref = ""
        if row is not None:
            ref = row["transport_ref"] if hasattr(row, "keys") else row[0]
        server = (ref or "").strip()
        if not server or not tool:
            return None
        return {"server": server, "tool": tool, "arguments": payload.get("arguments") or {}}
    except Exception as exc:
        logger.debug("mcp transport target resolve failed: %s", exc)
        return None


class McpTransport(AgentTransport):
    name = "mcp"
    capabilities = ("mcp", "tool")

    def send(self, envelope: A2AEnvelope) -> Any:
        if envelope.kind not in _DISPATCHABLE:
            return {"transport": self.name, "kind": envelope.kind, "payload": envelope.payload}
        target = _resolve_mcp_target(envelope)
        if not target:
            return {
                "transport": self.name,
                "error": (
                    f"mcp target incomplete for {envelope.to_handle!r}; "
                    "need payload.server+tool or registered agent transport_ref + tool"
                ),
            }
        try:
            # 复用 mcp_tool 的会话调用层（延迟 import，避免环）
            from tools.mcp_tool import MCPToolServer  # type: ignore
        except Exception:
            MCPToolServer = None  # noqa: N806
        # 优先：显式 call_tool 约定（httpx/stdio 由 MCP server 管理）
        try:
            from tools import mcp_tool as mcp_mod
            caller = getattr(mcp_mod, "call_tool_by_name", None) or getattr(mcp_mod, "call_mcp_tool", None)
            if callable(caller):
                result = caller(target["server"], target["tool"], target["arguments"])
                return {"transport": self.name, "ok": True, "result": result, **target}
        except Exception as exc:
            return {"transport": self.name, "error": f"mcp call failed: {exc}", **target}
        # 降级：未暴露统一调用入口时明确报错（不假装成功）
        return {
            "transport": self.name,
            "error": (
                "mcp_tool has no call_tool_by_name/call_mcp_tool entry; "
                f"target={target}"
            ),
            **target,
        }

    async def asend(self, envelope: A2AEnvelope) -> Any:
        return self.send(envelope)


register_a2a_transport("mcp", McpTransport)
