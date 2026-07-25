"""Blueprint: Agent Cache — LRU agent instance cache with session persistence.

Provides:
- _AgentCache: thread-safe LRU cache for AIAgent instances
- stop_agent_session(): interrupt agent for a session
- clean_agent_for_session(): explicit session-delete cleanup
"""

import logging
import threading
from collections import OrderedDict

_log = logging.getLogger(__name__)


def _agent_has_tools(agent) -> bool:
    """agent 是否携带非空工具集。防御式：任何异常都返回 False，绝不抛。"""
    try:
        return bool(getattr(agent, "tools", None))
    except Exception:
        return False


class _AgentCache:
    """LRU Agent 缓存，超限淘汰最久未用。线程安全。"""
    def __init__(self, maxsize: int = 20):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()
        # ── 性能监控指标 ──
        self.metrics = {
            'hits': 0,
            'misses': 0,
            'evictions': 0,
            'rejected_empty': 0,   # put 时因空工具被拒绝的次数
            'self_healed': 0,      # get 时命中空工具 agent 被自愈剔除的次数
        }

    def get(self, key: str):
        with self._lock:
            if key in self._cache:
                agent = self._cache[key]
                # 自愈：命中但工具为空（历史遗留缓存 / 工具被清空）→ 视作 miss，
                # 剔除后让调用方重建，避免整个会话卡在"无工具→直接 LLM 回复"状态。
                if not _agent_has_tools(agent):
                    del self._cache[key]
                    self.metrics['self_healed'] += 1
                    self.metrics['misses'] += 1
                    _log.warning(
                        f"[Agent] Self-heal: evicted empty-tools cached agent for {key} "
                        f"(will rebuild)"
                    )
                    return None
                self._cache.move_to_end(key)
                self.metrics['hits'] += 1
                return agent
            self.metrics['misses'] += 1
        return None

    def put(self, key: str, agent):
        # 空工具 agent 绝不缓存：否则整个 session 会持续复用一个无工具的 agent，
        # 表现为"直接 LLM 回复、不走 agent"。拒绝缓存 → 下次请求 get miss → 重建重试。
        if not _agent_has_tools(agent):
            with self._lock:
                self.metrics['rejected_empty'] += 1
            _log.warning(
                f"[Agent] Refusing to cache empty-tools agent for {key} "
                f"(len(tools)={len(getattr(agent, 'tools', None) or [])}); will rebuild next request"
            )
            return
        with self._lock:
            self._cache[key] = agent
            self._cache.move_to_end(key)
            self._evict()

    def pop_for_session(self, session_id: str):
        """Delete all cached entries matching this session_id."""
        with self._lock:
            keys = [k for k in self._cache if k.endswith(f":{session_id}")]
            for k in keys:
                del self._cache[k]
        if keys:
            _log.info(f"[Agent] Evicted {len(keys)} cached agent(s) for session {session_id}")

    def _evict(self):
        while len(self._cache) > self._maxsize:
            key, agent = self._cache.popitem(last=False)
            self.metrics['evictions'] += 1
            _log.info(f"[Agent] LRU evicted: {key}")

    def __len__(self):
        with self._lock:
            return len(self._cache)

    def get_metrics(self):
        """返回缓存性能指标。"""
        with self._lock:
            total = self.metrics['hits'] + self.metrics['misses']
            return {
                **self.metrics,
                'hit_rate': self.metrics['hits'] / total if total > 0 else 0.0,
                'current_size': len(self._cache),
                'max_size': self._maxsize,
            }


_agent_cache = _AgentCache(maxsize=20)


async def stop_agent_session(session_id: str) -> dict:
    """Interrupt agent generation for a given session."""
    for key, agent in list(_agent_cache._cache.items()):
        if session_id in key:
            try:
                agent.interrupt()
            except Exception as e:
                _log.warning(f"[Agent] Failed to interrupt {session_id}: {e}")
            return {"ok": True, "session_id": session_id}
    return {"ok": True, "session_id": session_id, "note": "no_active_agent"}


def clean_agent_for_session(session_id: str):
    """供 session.py 调用的 session 删除时清理接口。"""
    _agent_cache.pop_for_session(session_id)
