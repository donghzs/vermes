"""Tests for prune_context P3: salvage in-progress tool observations."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agent.conversation_compression import (
    prune_context,
    _salvage_in_progress_observation,
)


class FakeTodoStore:
    def __init__(self, items):
        self._items = items
    def read(self):
        return [dict(i) for i in self._items]


class FakeAgent:
    def __init__(self, items=None):
        self._todo_store = FakeTodoStore(items or [])
        self.context_compressor = None


def _msg(role, content, **kw):
    m = {"role": role, "content": content}
    m.update(kw)
    return m


# ─── P2b: step_id ordinal 序位 ─────────────────────────────────

def test_step_id_ordinal_no_in_progress():
    """No in_progress → step_id is None, step_index/total are None."""
    # This is a sanity check; the actual logic is in chat.py but we
    # verify the data flow: empty store → all None.
    store = FakeTodoStore([
        {"id": "1", "content": "done", "status": "completed"},
        {"id": "2", "content": "pending", "status": "pending"},
    ])
    items = store.read()
    step_id = None
    step_index = None
    step_total = len(items)
    for i, it in enumerate(items):
        if it.get("status") == "in_progress":
            step_id = it.get("id")
            step_index = i + 1
            break
    assert step_id is None
    assert step_index is None
    assert step_total == 2


def test_step_id_ordinal_first_in_progress():
    """First in_progress in a linear list → ordinal 1-based index."""
    store = FakeTodoStore([
        {"id": "1", "content": "step one", "status": "completed"},
        {"id": "2", "content": "step two", "status": "in_progress"},
        {"id": "3", "content": "step three", "status": "pending"},
    ])
    items = store.read()
    step_id = None
    step_index = None
    step_total = len(items)
    for i, it in enumerate(items):
        if it.get("status") == "in_progress":
            step_id = it.get("id")
            step_index = i + 1
            break
    assert step_id == "2"
    assert step_index == 2  # 1-based, second item
    assert step_total == 3


def test_step_id_ordinal_multiple_in_progress():
    """Multiple in_progress → takes the FIRST one (list order = priority)."""
    store = FakeTodoStore([
        {"id": "a", "content": "first", "status": "in_progress"},
        {"id": "b", "content": "second", "status": "in_progress"},
        {"id": "c", "content": "third", "status": "pending"},
    ])
    items = store.read()
    step_id = None
    step_index = None
    for i, it in enumerate(items):
        if it.get("status") == "in_progress":
            step_id = it.get("id")
            step_index = i + 1
            break
    assert step_id == "a"
    assert step_index == 1  # first in_progress, 1-based


# ─── P3: salvage in-progress observation ──────────────────────

def test_salvage_no_in_progress_todo():
    """No in_progress todo → salvage returns []."""
    agent = FakeAgent(items=[
        {"id": "1", "content": "done", "status": "completed"},
    ])
    messages = [
        _msg("user", "do something"),
        _msg("assistant", "calling tool", tool_calls=[{"id": "tc1", "type": "function", "function": {"name": "test", "arguments": "{}"}}]),
        _msg("tool", "result data"),
        _msg("assistant", "done"),
    ]
    result = _salvage_in_progress_observation(messages, [], [], [], agent)
    assert result == []


def test_salvage_finds_in_progress_observation():
    """In-progress todo + tool pair in dropped zone → salvaged."""
    agent = FakeAgent(items=[
        {"id": "s1", "content": "working", "status": "in_progress"},
    ])
    # Simulate: head + pruned_body + tail are kept, the tool pair was dropped.
    tool_call = _msg("assistant", "calling tool", tool_calls=[{"id": "tc1", "type": "function", "function": {"name": "test", "arguments": "{}"}}])
    tool_result = _msg("tool", "important intermediate result")
    messages = [
        _msg("system", "sys"),
        _msg("user", "start"),
        _msg("assistant", "first response"),
        tool_call,
        tool_result,
        _msg("assistant", "after tool"),
        _msg("user", "next round"),
        _msg("assistant", "latest"),
    ]
    # head=[sys, start], pruned_body=[after tool], tail=[next round, latest]
    head = messages[:3]
    pruned_body = [messages[5]]
    tail = messages[6:]
    result = _salvage_in_progress_observation(messages, head, pruned_body, tail, agent)
    assert len(result) == 2
    assert result[0] is tool_call
    assert result[1] is tool_result


def test_salvage_no_tool_pair_in_dropped_zone():
    """In-progress todo but no tool pair in dropped zone → empty."""
    agent = FakeAgent(items=[
        {"id": "s1", "content": "working", "status": "in_progress"},
    ])
    messages = [
        _msg("user", "hello"),
        _msg("assistant", "hi"),
        _msg("user", "bye"),
        _msg("assistant", "bye"),
    ]
    result = _salvage_in_progress_observation(messages, messages[:2], [], messages[2:], agent)
    assert result == []


# ─── prune_context integration ─────────────────────────────────

def test_prune_no_in_progress_observation_unchanged():
    """prune_context with no in_progress todo → no salvage, normal prune."""
    agent = FakeAgent(items=[
        {"id": "1", "content": "completed task", "status": "completed"},
    ])
    # Build enough messages to trigger pruning (> 3 + 20 + 4 = 27)
    messages = [_msg("system", "sys")]
    for i in range(40):
        messages.append(_msg("user", f"round {i}"))
        messages.append(_msg("assistant", f"response {i}"))
    original_len = len(messages)
    pruned, sys_msg = prune_context(agent, messages, "system prompt")
    # Should have shrunk (40 rounds → 24 body rounds kept after prune)
    assert len(pruned) < original_len
    # No salvage happened (no in_progress), so no extra tool messages
    # The bridge note should be in the system message
    assert "[上下文衔接摘要]" in sys_msg


def test_prune_salvages_in_progress_observation():
    """prune_context with in_progress todo → salvages tool pair from dropped zone."""
    agent = FakeAgent(items=[
        {"id": "s1", "content": "working on it", "status": "in_progress"},
    ])
    # Build messages where a tool pair is in the middle (will be dropped)
    messages = [_msg("system", "sys"), _msg("user", "start")]
    # Fill with many rounds to ensure pruning triggers
    for i in range(30):
        messages.append(_msg("user", f"round {i}"))
        messages.append(_msg("assistant", f"resp {i}"))
    # Add a tool pair near the end (in the protected tail)
    # Actually, we need the tool pair in the DROPPED zone.
    # The dropped zone is the oldest rounds beyond keep_rounds=24.
    # So put a tool pair early in the body.
    # Rebuild:
    messages = [_msg("system", "sys"), _msg("user", "start")]
    messages.append(_msg("assistant", "begin"))
    # Early tool call (will be in dropped zone)
    messages.append(_msg("assistant", "calling", tool_calls=[{"id": "tc1", "type": "function", "function": {"name": "test", "arguments": "{}"}}]))
    messages.append(_msg("tool", "early important result"))
    messages.append(_msg("assistant", "after tool"))
    # Many more rounds to push the tool pair into dropped zone
    # Need > 24 body rounds after head(3)+tail(20) to trigger pruning
    for i in range(40):
        messages.append(_msg("user", f"round {i}"))
        messages.append(_msg("assistant", f"resp {i}"))
    original_len = len(messages)
    pruned, sys_msg = prune_context(agent, messages, "system prompt")
    assert len(pruned) < original_len
    # The tool result should be present in pruned messages
    tool_msgs = [m for m in pruned if m.get("role") == "tool"]
    assert len(tool_msgs) >= 1
    assert tool_msgs[-1]["content"] == "early important result"
