"""⑦ hermes peer —— bot 间 DM（A2A 点对点特例）。

设计（路线图 C3）：
  - 复用 ① AgentRegistry 寻址 + 探活，信封 A2AEnvelope 走 transport dispatch；
  - 群聊场景：A agent @B agent → 建 peer 通道私聊一轮，结果回群；
  - 执行面可插拔：bot_room 原生/acp/cli 用 chat_runner；外部联邦走 get_a2a_transport。

纪律：本模块只做协议层路由与信封，不直接碰 SessionDB 时间线——
落库/广播由调用方（chat.py bot room）负责。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from agent.a2a.registry import AgentRegistry
from agent.a2a.types import (
    A2AEnvelope,
    AgentHandle,
    MessageKind,
    normalize_handle,
    parse_mention_handles,
)

# chat_runner: (profile_dict, prompt) -> str  （同步）
# async_chat_runner: (profile_dict, prompt) -> str （协程）
ChatRunner = Callable[[Dict[str, Any], str], Union[str, Awaitable[str]]]


def profile_to_handle(profile: Dict[str, Any]) -> AgentHandle:
    """bot_room agent_profiles 行 → AgentHandle（⑦ 建 peer 通道的寻址侧）。"""
    if not isinstance(profile, dict):
        raise ValueError("profile must be dict")
    pid = (profile.get("id") or profile.get("profile_id") or "").strip()
    if not pid:
        raise ValueError("profile missing id")
    caps = profile.get("capabilities") or ()
    if isinstance(caps, str):
        caps = tuple(x for x in caps.replace(",", " ").split() if x)
    return AgentHandle(
        profile_id=pid,
        name=(profile.get("name") or pid).strip(),
        provider=(profile.get("provider") or "").strip(),
        model=(profile.get("model") or "").strip(),
        transport=(profile.get("transport") or "local").strip() or "local",
        capabilities=tuple(caps),
        registered_at=float(profile.get("registered_at") or time.time()),
        recipe=(profile.get("recipe") or "").strip(),
    )


def ensure_peer_registered(
    profile: Dict[str, Any],
    registry: Optional[AgentRegistry] = None,
) -> AgentHandle:
    """把 bot_room 成员注册进 AgentRegistry（幂等 upsert + 刷新心跳）。"""
    reg = registry or AgentRegistry()
    handle = profile_to_handle(profile)
    reg.register(handle)
    return handle


def build_peer_envelope(
    from_handle: Union[str, AgentHandle],
    to_handle: Union[str, AgentHandle],
    text: str,
    *,
    room_id: str = "",
    kind: str = MessageKind.MESSAGE.value,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> A2AEnvelope:
    """构造 peer 私聊信封。payload.text 为私聊正文。"""
    fh = from_handle.uri() if isinstance(from_handle, AgentHandle) else normalize_handle(from_handle)
    th = to_handle.uri() if isinstance(to_handle, AgentHandle) else normalize_handle(to_handle)
    payload: Dict[str, Any] = {"text": text or "", "peer": True}
    if extra_payload:
        payload.update(extra_payload)
    return A2AEnvelope(
        from_handle=fh,
        to_handle=th,
        kind=kind,
        payload=payload,
        room_id=room_id or "",
        message_id=f"peer-{uuid.uuid4().hex[:12]}",
        created_at=time.time(),
    )


def extract_peer_targets(text: str, profiles: List[Dict[str, Any]], self_id: str = "") -> List[str]:
    """从文本提取可 peer 的成员 id（排除自指；匹配 name/id）。

    协议层自包含匹配，不 import vermes_cli（避免 agent→vermes_cli 反向依赖）。
    """
    if not text:
        return []
    tokens = parse_mention_handles(text)
    if not tokens:
        return []
    by_lower: Dict[str, str] = {}
    for p in profiles or []:
        if not isinstance(p, dict):
            continue
        pid = p.get("id") or p.get("profile_id")
        name = p.get("name") or ""
        if not pid:
            continue
        by_lower[str(pid).lower()] = str(pid)
        if name:
            by_lower[str(name).lower()] = str(pid)
    matched: List[str] = []
    seen = set()
    for tok in tokens:
        pid = by_lower.get(tok.lower())
        if not pid or pid == self_id or pid in seen:
            continue
        seen.add(pid)
        matched.append(pid)
    return matched


@dataclass
class PeerResult:
    ok: bool
    from_handle: str = ""
    to_handle: str = ""
    room_id: str = ""
    envelope: Optional[Dict[str, Any]] = None
    result: str = ""
    error: str = ""
    transport: str = ""
    turns: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "from": self.from_handle,
            "to": self.to_handle,
            "from_handle": self.from_handle,
            "to_handle": self.to_handle,
            "room_id": self.room_id,
            "envelope": self.envelope,
            "result": self.result,
            "error": self.error,
            "transport": self.transport,
            "turns": self.turns,
        }


def _normalize_result(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        # LocalTransport 返回 {"transport","result": ...}
        inner = raw.get("result")
        if isinstance(inner, str):
            return inner
        if isinstance(inner, dict) and isinstance(inner.get("content"), str):
            return inner["content"]
        if raw.get("error"):
            return ""
        return str(inner if inner is not None else raw)
    return str(raw)


def _resolve_or_register(
    profile: Dict[str, Any],
    registry: AgentRegistry,
) -> tuple[Optional[AgentHandle], AgentHandle]:
    """解析 peer；未注册才 upsert（不刷新已离线 agent 的心跳）。

    纪律：已注册但心跳过期 → resolve=None → 调用方报 offline，
    禁止「每次 peer 都 register 把离线刷成在线」。
    """
    handle = profile_to_handle(profile)
    resolved = registry.resolve(handle.uri())
    if resolved is None and handle.name:
        resolved = registry.resolve(f"@{handle.name}")
    if resolved is None:
        raw = None
        try:
            raw = registry._get_db().get_a2a_agent(handle.profile_id)
        except Exception:
            raw = None
        if raw is None:
            # 从未注册 → 允许首次建 peer 通道
            registry.register(handle)
            resolved = registry.resolve(handle.uri())
        # else: 已注册但离线 → 不 re-register，返回 None 让调用方显式失败
    return resolved, handle


async def peer_exchange(
    from_profile: Dict[str, Any],
    to_profile: Dict[str, Any],
    text: str,
    *,
    room_id: str = "",
    registry: Optional[AgentRegistry] = None,
    chat_runner: Optional[ChatRunner] = None,
    kind: str = MessageKind.MESSAGE.value,
    peer_prompt_builder: Optional[Callable[[Dict[str, Any], Dict[str, Any], str, str], str]] = None,
) -> PeerResult:
    """一轮 peer 私聊：A → B。

    路由顺序：
      1. resolve 探活（未注册才写入 registry；离线显式失败）；
      2. 构造 A2AEnvelope（协议面必过）；
      3. chat_runner 优先（bot_room 原生/acp/cli）；否则 transport.send。
    """
    reg = registry or AgentRegistry()
    try:
        resolved_from, from_h = _resolve_or_register(from_profile, reg)
        resolved_to, to_h = _resolve_or_register(to_profile, reg)
    except ValueError as exc:
        return PeerResult(ok=False, error=f"invalid profile: {exc}")

    if from_h.profile_id == to_h.profile_id:
        return PeerResult(ok=False, error="peer_dm rejected: self-target",
                          from_handle=from_h.uri(), to_handle=to_h.uri(), room_id=room_id)

    if resolved_to is None:
        return PeerResult(
            ok=False,
            error=f"peer offline or unregistered: {to_h.display()}",
            from_handle=from_h.uri(),
            to_handle=to_h.uri(),
            room_id=room_id,
        )
    if resolved_from is None:
        return PeerResult(
            ok=False,
            error=f"peer offline or unregistered: {from_h.display()}",
            from_handle=from_h.uri(),
            to_handle=to_h.uri(),
            room_id=room_id,
        )

    envelope = build_peer_envelope(from_h, resolved_to, text, room_id=room_id, kind=kind)
    prompt = text or ""
    if peer_prompt_builder is not None:
        try:
            prompt = peer_prompt_builder(from_profile, to_profile, text, room_id)
        except Exception:
            prompt = text or ""

    transport_name = resolved_to.transport or "local"
    reply = ""
    error = ""

    if chat_runner is not None:
        try:
            raw = chat_runner(to_profile, prompt)
            if hasattr(raw, "__await__") or isinstance(raw, Awaitable):
                raw = await raw  # type: ignore[misc]
            reply = _normalize_result(raw)
            if not reply:
                error = "peer chat_runner returned empty"
        except Exception as exc:
            error = f"peer chat_runner failed: {exc}"
    else:
        from agent.a2a.transports import get_a2a_transport
        transport = get_a2a_transport(transport_name)
        if transport is None:
            error = f"a2a transport not found: {transport_name}"
        else:
            try:
                raw = await transport.asend(envelope)
                reply = _normalize_result(raw)
                if not reply:
                    error = "peer transport returned empty"
            except Exception as exc:
                error = f"peer transport failed: {exc}"

    turn = {
        "role": "user",
        "from": from_h.uri(),
        "to": resolved_to.uri(),
        "text": text,
        "envelope_id": envelope.message_id,
    }
    turns = [turn]
    if reply:
        turns.append({
            "role": "assistant",
            "from": resolved_to.uri(),
            "to": from_h.uri(),
            "text": reply,
        })
        # 刷新双方心跳（私聊成功 = 双方在线）
        try:
            reg.heartbeat(from_h.profile_id)
            reg.heartbeat(resolved_to.profile_id)
        except Exception:
            pass
        return PeerResult(
            ok=True,
            from_handle=from_h.uri(),
            to_handle=resolved_to.uri(),
            room_id=room_id,
            envelope=envelope.to_dict(),
            result=reply,
            transport=transport_name if chat_runner is None else "chat_runner",
            turns=turns,
        )

    return PeerResult(
        ok=False,
        from_handle=from_h.uri(),
        to_handle=resolved_to.uri(),
        room_id=room_id,
        envelope=envelope.to_dict(),
        error=error or "peer exchange failed",
        transport=transport_name if chat_runner is None else "chat_runner",
        turns=turns,
    )


def peer_dm(
    from_profile: Dict[str, Any],
    to_profile: Dict[str, Any],
    text: str,
    *,
    room_id: str = "",
    registry: Optional[AgentRegistry] = None,
    chat_runner: Optional[ChatRunner] = None,
    peer_prompt_builder: Optional[Callable[[Dict[str, Any], Dict[str, Any], str, str], str]] = None,
) -> PeerResult:
    """同步包装（测试/CLI 用）。"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    coro = peer_exchange(
        from_profile, to_profile, text,
        room_id=room_id, registry=registry,
        chat_runner=chat_runner, peer_prompt_builder=peer_prompt_builder,
    )
    if loop and loop.is_running():
        # 已在事件循环内：调用方应 await peer_exchange；此处退化为新建 loop 不安全
        raise RuntimeError("peer_dm called inside running loop; use await peer_exchange")
    return asyncio.new_event_loop().run_until_complete(coro)
