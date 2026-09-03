"""A2A AgentRegistry —— 注册 / 发现 / 路由表（① 第 4-5 步）。

持久化到 SessionDB 的 a2a_agents 表（见 vermes_state.py），提供：

  - register()      —— 注册一个 agent（upsert + 心跳）
  - unregister()    —— 下线
  - heartbeat()     —— 探活（@mention 路由到已销毁 agent 会静默丢消息，必须探活）
  - resolve()       —— @name / vermes://id → AgentHandle（离线返回 None）
  - list_agents()   —— 发现（供能力发现 / 调度按标签匹配）
  - match()         —— 按能力标签匹配派活

离线判定：last_heartbeat 超过 heartbeat_ttl 视为离线（默认 300s），
resolve 返回 None，调用方据此不投递（避免静默丢消息，§5 ① 第 5 步）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from agent.a2a.types import AgentHandle, normalize_handle

DEFAULT_HEARTBEAT_TTL = 300.0  # 秒


class AgentRegistry:
    """A2A agent 寻址 + 路由表（持久化 + 生命周期）。"""

    def __init__(self, db: Any = None, heartbeat_ttl: float = DEFAULT_HEARTBEAT_TTL):
        self._db = db
        self.heartbeat_ttl = heartbeat_ttl

    def _get_db(self):
        """惰性获取 SessionDB（避免 import 循环 + 支持测试注入）。"""
        if self._db is not None:
            return self._db
        from vermes_state import SessionDB
        self._db = SessionDB()
        return self._db

    # ── 生命周期 ───────────────────────────────────────────

    def register(self, handle: AgentHandle) -> None:
        """注册/更新一个 agent（upsert + 刷新心跳）。"""
        db = self._get_db()
        db.upsert_a2a_agent({
            "profile_id": handle.profile_id,
            "name": handle.name,
            "provider": handle.provider,
            "model": handle.model,
            "transport": handle.transport,
            "capabilities": handle.capabilities,
            "registered_at": handle.registered_at,
            "last_heartbeat": time.time(),
        })

    def unregister(self, profile_id: str) -> None:
        db = self._get_db()
        db.remove_a2a_agent(profile_id)

    def heartbeat(self, profile_id: str) -> bool:
        """刷新心跳；agent 不存在返回 False。"""
        db = self._get_db()
        if db.get_a2a_agent(profile_id) is None:
            return False
        db.heartbeat_a2a_agent(profile_id)
        return True

    def is_alive(self, profile_id: str) -> bool:
        db = self._get_db()
        agent = db.get_a2a_agent(profile_id)
        if agent is None:
            return False
        last = agent.get("last_heartbeat") or 0
        return (time.time() - last) <= self.heartbeat_ttl

    # ── 寻址 / 路由 ─────────────────────────────────────────

    def resolve(self, handle_str: str) -> Optional[AgentHandle]:
        """@name 或 vermes://id → AgentHandle；离线/未知返回 None。"""
        norm = normalize_handle(handle_str)
        if not norm:
            return None
        db = self._get_db()
        if norm.startswith("vermes://"):
            pid = norm[len("vermes://"):]
            agent = db.get_a2a_agent(pid)
        else:
            name = norm.lstrip("@")
            agent = db.get_a2a_agent_by_name(name)
        if agent is None:
            return None
        # 离线探活：已销毁 agent 不投递，避免静默丢消息
        last = agent.get("last_heartbeat") or 0
        if (time.time() - last) > self.heartbeat_ttl:
            return None
        return AgentHandle(
            profile_id=agent["profile_id"],
            name=agent.get("name", ""),
            provider=agent.get("provider", ""),
            model=agent.get("model", ""),
            transport=agent.get("transport", "local"),
            capabilities=tuple(agent.get("capabilities", ())),
            registered_at=agent.get("registered_at", 0.0),
        )

    def list_agents(self, alive_only: bool = True) -> List[AgentHandle]:
        db = self._get_db()
        out: List[AgentHandle] = []
        for agent in db.list_a2a_agents():
            h = AgentHandle(
                profile_id=agent["profile_id"],
                name=agent.get("name", ""),
                provider=agent.get("provider", ""),
                model=agent.get("model", ""),
                transport=agent.get("transport", "local"),
                capabilities=tuple(agent.get("capabilities", ())),
                registered_at=agent.get("registered_at", 0.0),
            )
            if alive_only and not self.is_alive(h.profile_id):
                continue
            out.append(h)
        return out

    def match(self, capability: str, alive_only: bool = True) -> List[AgentHandle]:
        """按能力标签匹配派活（「写代码」→@codex，「搜资料」→@扣子）。"""
        return [
            h for h in self.list_agents(alive_only=alive_only)
            if capability in h.capabilities
        ]
