"""A2A subprocess transport —— CLI 外部 agent（Codex app-server JSON-RPC）。

S2 接满：
  payload.spawn_bin / payload.spawn_args 或 handle.recipe=codex*
  → CodexAppServerClient initialize 握手 + thread/start + turn/start（能拿多少拿多少）
缺 spawn 命令且非 codex recipe → 结构化 error。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.a2a.transports import AgentTransport, register_a2a_transport
from agent.a2a.types import A2AEnvelope, MessageKind

logger = logging.getLogger(__name__)

_DISPATCHABLE = (MessageKind.MESSAGE.value, MessageKind.TASK.value)


def _resolve_spawn_plan(envelope: A2AEnvelope) -> Optional[dict]:
    payload = envelope.payload or {}
    spawn_bin = (payload.get("spawn_bin") or payload.get("codex_bin") or "").strip()
    spawn_args = payload.get("spawn_args") or []
    if spawn_bin:
        return {"bin": spawn_bin, "args": list(spawn_args), "text": _payload_text(envelope)}
    try:
        from agent.a2a.registry import AgentRegistry
        handle = AgentRegistry().resolve(envelope.to_handle)
        if handle is None:
            return None
        recipe = (handle.recipe or "").lower()
        if "codex" in recipe:
            return {"bin": "codex", "args": [], "text": _payload_text(envelope), "recipe": handle.recipe}
        return None
    except Exception as exc:
        logger.debug("subprocess spawn plan resolve failed: %s", exc)
        return None


def _payload_text(envelope: A2AEnvelope) -> str:
    payload = envelope.payload or {}
    return payload.get("goal") or payload.get("text") or payload.get("message") or ""


class SubprocessTransport(AgentTransport):
    name = "subprocess"
    capabilities = ("code", "cli", "subprocess")

    def send(self, envelope: A2AEnvelope) -> Any:
        if envelope.kind not in _DISPATCHABLE:
            return {"transport": self.name, "kind": envelope.kind, "payload": envelope.payload}
        plan = _resolve_spawn_plan(envelope)
        if not plan:
            return {
                "transport": self.name,
                "error": (
                    f"no spawn plan for {envelope.to_handle!r}; "
                    "set payload.spawn_bin or register recipe containing 'codex'"
                ),
            }
        text = plan.get("text") or ""
        if not text:
            return {"transport": self.name, "error": "empty message text in payload"}
        try:
            from agent.transports.codex_app_server import CodexAppServerClient
        except Exception as exc:
            return {"transport": self.name, "error": f"codex client unavailable: {exc}"}
        client = None
        try:
            client = CodexAppServerClient(codex_bin=plan["bin"], extra_args=plan.get("args") or [])
            init = client.initialize(client_name="Vermes-A2A")
            # 最小驱动：thread/start → turn/start → 尽力读已完成结果（协议层不阻断）
            thread = None
            turn = None
            try:
                thread = client.request("thread/start", {"input": {"text": text}}, timeout=60.0)
            except Exception as exc:
                return {
                    "transport": self.name,
                    "error": f"thread/start failed: {exc}",
                    "handshake": init,
                }
            try:
                thread_id = (thread or {}).get("threadId") or (thread or {}).get("thread_id") or ""
            except Exception:
                thread_id = ""
            try:
                turn = client.request(
                    "turn/start",
                    {"threadId": thread_id, "input": {"text": text}},
                    timeout=120.0,
                )
            except Exception as exc:
                return {
                    "transport": self.name,
                    "error": f"turn/start failed: {exc}",
                    "handshake": init,
                    "thread": thread,
                }
            result_text = ""
            try:
                result_text = ((turn or {}).get("result") or {}).get("text") or str(turn)
            except Exception:
                result_text = str(turn)
            return {
                "transport": self.name,
                "ok": True,
                "result": result_text,
                "handshake": bool(init),
                "thread_id": thread_id or None,
            }
        except Exception as exc:
            return {"transport": self.name, "error": f"subprocess dispatch failed: {exc}"}
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    async def asend(self, envelope: A2AEnvelope) -> Any:
        return self.send(envelope)


register_a2a_transport("subprocess", SubprocessTransport)
