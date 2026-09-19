"""A2A HTTP transport —— HTTP-API 外部 agent。

S2 接满：真实 httpx POST 到 agent 端点。
端点优先级：envelope.payload.endpoint > AgentRegistry handle 侧 agent_profiles.transport_ref。
缺端点/未注册 → 返回结构化 error，不假装发送成功。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.a2a.transports import AgentTransport, register_a2a_transport
from agent.a2a.types import A2AEnvelope, MessageKind

logger = logging.getLogger(__name__)

_DISPATCHABLE = (MessageKind.MESSAGE.value, MessageKind.TASK.value)
_DEFAULT_TIMEOUT = 30.0


def _resolve_endpoint(envelope: A2AEnvelope) -> Optional[str]:
    payload = envelope.payload or {}
    url = (payload.get("endpoint") or payload.get("url") or "").strip()
    if url:
        return url
    try:
        from agent.a2a.registry import AgentRegistry
        handle = AgentRegistry().resolve(envelope.to_handle)
        if handle is None:
            return None
        # a2a_agents 表无 endpoint 列 → 读 agent_profiles.transport_ref（http agent 的 URL）
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
        if row is None:
            return None
        ref = row["transport_ref"] if hasattr(row, "keys") else row[0]
        return (ref or "").strip() or None
    except Exception as exc:
        logger.debug("http transport endpoint resolve failed: %s", exc)
        return None


class HttpTransport(AgentTransport):
    name = "http"
    capabilities = ("http", "api")

    def send(self, envelope: A2AEnvelope) -> Any:
        """同步 HTTP POST（阻塞）。异步路径请用 asend。"""
        if envelope.kind not in _DISPATCHABLE:
            return {"transport": self.name, "kind": envelope.kind, "payload": envelope.payload}
        endpoint = _resolve_endpoint(envelope)
        if not endpoint:
            return {
                "transport": self.name,
                "error": (
                    f"no HTTP endpoint for {envelope.to_handle!r}; "
                    "set payload.endpoint or agent_profiles.transport_ref"
                ),
            }
        try:
            import httpx
        except ImportError as exc:
            return {"transport": self.name, "error": f"httpx unavailable: {exc}"}
        try:
            resp = httpx.post(
                endpoint,
                json=envelope.to_dict(),
                timeout=float((envelope.payload or {}).get("timeout_s") or _DEFAULT_TIMEOUT),
            )
            body: Any
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            return {
                "transport": self.name,
                "ok": resp.is_success,
                "status": resp.status_code,
                "endpoint": endpoint,
                "result": body,
            }
        except Exception as exc:
            return {"transport": self.name, "error": f"http post failed: {exc}", "endpoint": endpoint}

    async def asend(self, envelope: A2AEnvelope) -> Any:
        if envelope.kind not in _DISPATCHABLE:
            return self.send(envelope)
        endpoint = _resolve_endpoint(envelope)
        if not endpoint:
            return self.send(envelope)  # 复用 error 结构
        try:
            import httpx
        except ImportError as exc:
            return {"transport": self.name, "error": f"httpx unavailable: {exc}"}
        try:
            async with httpx.AsyncClient(timeout=float((envelope.payload or {}).get("timeout_s") or _DEFAULT_TIMEOUT)) as client:
                resp = await client.post(endpoint, json=envelope.to_dict())
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            return {
                "transport": self.name,
                "ok": resp.is_success,
                "status": resp.status_code,
                "endpoint": endpoint,
                "result": body,
            }
        except Exception as exc:
            return {"transport": self.name, "error": f"http post failed: {exc}", "endpoint": endpoint}


register_a2a_transport("http", HttpTransport)
