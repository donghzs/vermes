"""A2A MCP transport —— stdio-MCP 外部 agent。

S2 接满（P0 修复版）：经 AgentRegistry 解析目标，复用 tools.registry 的
统一 dispatch 调用 MCP 工具（MCP 工具真实注册名为 ``mcp_{server}_{tool}``，
由 discover_mcp_tools 注册进 registry）。缺 server/工具 → 结构化 error，
不假装成功。

关键：不再依赖不存在的 ``mcp_tool.call_tool_by_name``（审计 P0），改走
registry.dispatch —— 这是 MCP 工具唯一真实执行入口。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from agent.a2a.transports import AgentTransport, register_a2a_transport
from agent.a2a.types import A2AEnvelope, MessageKind

logger = logging.getLogger(__name__)

_DISPATCHABLE = (MessageKind.MESSAGE.value, MessageKind.TASK.value, MessageKind.TOOL.value)


def _mcp_tool_name(server: str, tool: str) -> str:
    """构造 registry 里的 MCP 工具名 ``mcp_{safe_server}_{safe_tool}``。

    与 tools/mcp_tool.py 的 _convert_mcp_schema 同源：非法字符统一替换为 ``_``。
    """
    try:
        from tools.mcp_tool import sanitize_mcp_name_component
    except Exception:
        import re

        def sanitize_mcp_name_component(value: str) -> str:  # type: ignore[no-redef]
            return re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))

    return f"mcp_{sanitize_mcp_name_component(server)}_{sanitize_mcp_name_component(tool)}"


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


def _dispatch_mcp_tool(target: dict) -> dict:
    """经 registry.dispatch 真实执行 MCP 工具（同步）。"""
    server = target["server"]
    tool = target["tool"]
    arguments = target["arguments"] or {}
    tool_name = _mcp_tool_name(server, tool)

    try:
        from tools.registry import registry
    except Exception as exc:
        return {"ok": False, "error": f"registry unavailable: {exc}", **target}

    # 未注册（MCP server 未连接/工具未发现）→ 诚实报错
    if registry.get_entry(tool_name) is None:
        return {
            "ok": False,
            "error": (
                f"MCP tool not registered: {tool_name!r}; "
                f"ensure MCP server {server!r} is connected and tools discovered"
            ),
            "tool_name": tool_name,
            **target,
        }

    try:
        result = registry.dispatch(tool_name, arguments)
    except Exception as exc:
        return {"ok": False, "error": f"mcp dispatch failed: {exc}", "tool_name": tool_name, **target}

    # dispatch 失败时会返回 json.dumps({"error": ...})，识别并转为 ok=False
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and "error" in parsed:
            return {
                "ok": False,
                "error": parsed["error"],
                "tool_name": tool_name,
                **target,
            }

    return {"ok": True, "result": result, "tool_name": tool_name, **target}


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
        return _dispatch_mcp_tool(target)

    async def asend(self, envelope: A2AEnvelope) -> Any:
        """真异步：dispatch 内部可能是同步阻塞（stdio MCP），经 to_thread 避免卡事件循环。"""
        if envelope.kind not in _DISPATCHABLE:
            return self.send(envelope)
        target = _resolve_mcp_target(envelope)
        if not target:
            return self.send(envelope)  # 复用 error 结构
        return await asyncio.to_thread(_dispatch_mcp_tool, target)


register_a2a_transport("mcp", McpTransport)
