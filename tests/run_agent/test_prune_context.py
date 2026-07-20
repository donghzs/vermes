"""Regression test for ``prune_context`` (the fatigue bridge).

This function previously did not exist — ``run_agent._prune_context``
forwarded to ``agent.conversation_compression.prune_context``, which raised
``ImportError`` on long, memory-rich sessions (memory bridge ready +
user turns > 25). That crashed the agent loop mid long-task.

These tests prove the fix: no crash, the message window shrinks, tool_call
↔ observation pairs stay intact, the bridge note is appended to the system
prompt when memory exists, and the fail-open path returns inputs unchanged.
"""
import types

from agent.conversation_compression import prune_context


class _FakeCompressor:
    def __init__(self, first=3, last=20):
        self.protect_first_n = first
        self.protect_last_n = last


class _FakeStore:
    def __init__(self, entries):
        self.memory_entries = entries


class _FakeAgent:
    def __init__(self, entries=None, first=3, last=20):
        self.context_compressor = _FakeCompressor(first, last)
        self._memory_store = _FakeStore(entries or [])
        self.session_id = "sess-test"


def _make_messages(n_body_rounds, protect_first=3, protect_last=20):
    """Build a message list: head, many user→assistant(tool_call)+tool rounds,
    tail. Each round carries a tool_call + its observation to verify integrity.
    """
    msgs = []
    for i in range(protect_first):
        msgs.append({"role": "user", "content": f"head-{i}"})
    for r in range(n_body_rounds):
        msgs.append({"role": "user", "content": f"task question {r}"})
        msgs.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"call-{r}", "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }],
        })
        msgs.append({"role": "tool", "tool_call_id": f"call-{r}",
                     "content": f"result {r}"})
    for i in range(protect_last):
        msgs.append({"role": "user", "content": f"tail-{i}"})
    return msgs


def test_prune_shrinks_and_keeps_pairs():
    msgs = _make_messages(40)
    agent = _FakeAgent(entries=[types.SimpleNamespace(fts_content="记住：项目用 pytest")])
    out, sys_prompt = prune_context(agent, msgs, "SYS")
    assert isinstance(out, list) and isinstance(sys_prompt, str)
    assert len(out) < len(msgs)
    # every tool observation must still have its originating call
    tool_ids = {m["tool_call_id"] for m in out if m.get("role") == "tool"}
    call_ids = {
        c["id"]
        for m in out if m.get("role") == "assistant"
        for c in (m.get("tool_calls") or [])
    }
    assert tool_ids <= call_ids, "a tool observation lost its call"
    # bridge note appended from memory
    assert "上下文衔接摘要" in sys_prompt
    assert "记住：项目用 pytest" in sys_prompt


def test_prune_fail_open_on_short_messages():
    agent = _FakeAgent(entries=[])
    agent.context_compressor = None
    short = [{"role": "user", "content": "hi"}]
    out, sp = prune_context(agent, short, "SYS")
    assert out is short  # unchanged (no-op path)


def test_prune_no_memory_still_drops_and_notes():
    msgs = _make_messages(40)
    agent = _FakeAgent(entries=[])  # empty memory → no bridge text
    out, sys_prompt = prune_context(agent, msgs, "SYS")
    assert len(out) < len(msgs)
    assert "精简" in sys_prompt          # dropped-rounds note still present
    assert "关键记忆衔接" not in sys_prompt  # but no memory bridge text
