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
        }

    def get(self, key: str):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self.metrics['hits'] += 1
                return self._cache[key]
            self.metrics['misses'] += 1
        return None

    def put(self, key: str, agent):
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
