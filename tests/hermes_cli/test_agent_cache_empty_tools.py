"""Regression: _AgentCache must never cache/serve empty-tools agents.

Root cause of "Vermes 直接 LLM 回复不走 agent" (A):
a cold-start / broken agent whose .tools is empty gets cached and reused for
the whole session, so every request goes to the model with no tools → plain
LLM replies until a new session. The cache now refuses to store such agents
and self-heals if one is already present.
"""

from vermes_cli.blueprints.agent_cache import _AgentCache, _agent_has_tools


class _FakeAgent:
    def __init__(self, tools):
        self.tools = tools


def test_agent_has_tools_helper():
    assert _agent_has_tools(_FakeAgent([{"function": {"name": "x"}}])) is True
    assert _agent_has_tools(_FakeAgent([])) is False
    assert _agent_has_tools(_FakeAgent(None)) is False
    # defensive: object without .tools attr
    assert _agent_has_tools(object()) is False


def test_put_refuses_empty_tools_agent():
    cache = _AgentCache(maxsize=5)
    cache.put("p:m:s1", _FakeAgent([]))
    assert cache.get("p:m:s1") is None
    assert cache.metrics["rejected_empty"] == 1
    assert len(cache) == 0


def test_put_accepts_agent_with_tools():
    cache = _AgentCache(maxsize=5)
    good = _FakeAgent([{"function": {"name": "read"}}])
    cache.put("p:m:s1", good)
    assert cache.get("p:m:s1") is good
    assert cache.metrics["hits"] == 1
    assert cache.metrics["rejected_empty"] == 0


def test_get_self_heals_empty_tools_agent():
    """If an empty-tools agent is somehow already cached (e.g. tools cleared
    after caching, or a pre-fix legacy entry), get() evicts it → miss →
    caller rebuilds instead of reusing a broken agent for the whole session."""
    cache = _AgentCache(maxsize=5)
    good = _FakeAgent([{"function": {"name": "read"}}])
    cache.put("p:m:s1", good)
    # simulate tools being emptied after caching
    good.tools = []
    assert cache.get("p:m:s1") is None
    assert cache.metrics["self_healed"] == 1
    assert len(cache) == 0
    # subsequent get is a clean miss
    assert cache.get("p:m:s1") is None


def test_empty_agent_does_not_poison_session():
    """End-to-end shape of the fix: an empty agent for a session never blocks
    a later good agent for the same key."""
    cache = _AgentCache(maxsize=5)
    cache.put("p:m:s1", _FakeAgent([]))       # cold start, rejected
    assert cache.get("p:m:s1") is None         # miss → caller rebuilds
    good = _FakeAgent([{"function": {"name": "bash"}}])
    cache.put("p:m:s1", good)                   # rebuild succeeds
    assert cache.get("p:m:s1") is good          # session recovered
