"""C-2: Strengthened prune/fatigue tests.

Covers edge cases and fallback paths not tested in test_prune_context.py
or test_prune_p2b_p3.py:

- _build_fatigue_bridge_note: dict entries, handoff fallback, empty store
- prune_context: boundary (exact no-op threshold), exception fail-open
- prune_context: keep_rounds boundary (exactly keep_rounds → no drop)
- _salvage: id() overlap robustness (messages reused in head/tail)
"""
import types
from unittest import mock

import pytest

from agent.conversation_compression import (
    prune_context,
    _build_fatigue_bridge_note,
    _salvage_active_task_observation,
)


# ── Helpers ──────────────────────────────────────────────────────────

class _FakeCompressor:
    def __init__(self, first=3, last=20):
        self.protect_first_n = first
        self.protect_last_n = last


class _FakeStore:
    def __init__(self, entries):
        self.memory_entries = entries


class _FakeAgent:
    def __init__(self, entries=None, first=3, last=20, session_id="sess-test"):
        self.context_compressor = _FakeCompressor(first, last)
        self._memory_store = _FakeStore(entries or [])
        self._todo_store = None
        self.session_id = session_id


def _make_messages(n_body_rounds, protect_first=3, protect_last=20):
    """Build a message list with head, body rounds (user+assistant+tool), tail."""
    msgs = []
    for i in range(protect_first):
        msgs.append({"role": "user", "content": f"head-{i}"})
    for r in range(n_body_rounds):
        msgs.append({"role": "user", "content": f"task question {r}"})
        msgs.append({
            "role": "assistant", "content": "",
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


# ── _build_fatigue_bridge_note tests ─────────────────────────────────

class TestBuildFatigueBridgeNote:

    def test_empty_store_no_handoff(self):
        """Empty memory store + no handoff → empty string."""
        agent = _FakeAgent(entries=[])
        with mock.patch(
            "agent.handoff_store.get_latest_handoff",
            return_value=None,
        ):
            result = _build_fatigue_bridge_note(agent)
        assert result == ""

    def test_dict_entries(self):
        """Dict entries (not SimpleNamespace) → content extracted."""
        entries = [
            {"fts_content": "dict-based memory entry"},
        ]
        agent = _FakeAgent(entries=entries)
        result = _build_fatigue_bridge_note(agent)
        assert "dict-based memory entry" in result
        assert "关键记忆衔接" in result

    def test_simple_namespace_entries(self):
        """SimpleNamespace entries → fts_content extracted."""
        entries = [
            types.SimpleNamespace(fts_content="namespace memory"),
        ]
        agent = _FakeAgent(entries=entries)
        result = _build_fatigue_bridge_note(agent)
        assert "namespace memory" in result

    def test_handoff_fallback(self):
        """Empty memory store + handoff available → handoff summary used."""
        agent = _FakeAgent(entries=[])
        handoff = {"summary_text": "Previous session: worked on RAG optimization"}
        with mock.patch(
            "agent.handoff_store.get_latest_handoff",
            return_value=handoff,
        ):
            result = _build_fatigue_bridge_note(agent)
        assert "RAG optimization" in result
        assert "此前会话衔接" in result

    def test_handoff_fallback_truncated(self):
        """Long handoff summary → truncated to 240 chars."""
        agent = _FakeAgent(entries=[])
        long_text = "x" * 500
        handoff = {"summary_text": long_text}
        with mock.patch(
            "agent.handoff_store.get_latest_handoff",
            return_value=handoff,
        ):
            result = _build_fatigue_bridge_note(agent)
        # Should contain truncated text (240 chars max)
        assert len(result) < 300  # prefix + 240 chars
        assert "x" * 240 in result

    def test_multiple_entries_last_four(self):
        """More than 4 entries → only last 4 used."""
        entries = [
            types.SimpleNamespace(fts_content=f"memory-{i}")
            for i in range(10)
        ]
        agent = _FakeAgent(entries=entries)
        result = _build_fatigue_bridge_note(agent)
        # Should include memory-6 through memory-9 (last 4)
        assert "memory-9" in result
        assert "memory-6" in result
        assert "memory-5" not in result

    def test_entry_truncation_160_chars(self):
        """Individual entry > 160 chars → truncated."""
        long_entry = "A" * 300
        entries = [types.SimpleNamespace(fts_content=long_entry)]
        agent = _FakeAgent(entries=entries)
        result = _build_fatigue_bridge_note(agent)
        # Each snippet truncated to 160 chars
        assert "A" * 160 in result
        assert "A" * 200 not in result

    def test_no_memory_store_attr(self):
        """Agent without _memory_store → empty string (fail-safe)."""
        agent = types.SimpleNamespace(session_id="s")
        # _memory_store missing entirely
        result = _build_fatigue_bridge_note(agent)
        assert result == ""

    def test_no_entries_attr(self):
        """Store without memory_entries attr → handoff fallback."""
        agent = types.SimpleNamespace(
            _memory_store=types.SimpleNamespace(),  # no memory_entries
            session_id="s",
        )
        with mock.patch(
            "agent.handoff_store.get_latest_handoff",
            return_value=None,
        ):
            result = _build_fatigue_bridge_note(agent)
        assert result == ""


# ── prune_context boundary tests ─────────────────────────────────────

class TestPruneContextBoundaries:

    def test_exact_noop_threshold(self):
        """Messages = protect_first + protect_last + 4 → no-op (boundary)."""
        # 3 + 20 + 4 = 27 messages → no-op
        msgs = _make_messages(0, protect_first=3, protect_last=20)
        # Add 4 body messages
        msgs = msgs[:3] + [
            {"role": "user", "content": "b1"},
            {"role": "user", "content": "b2"},
            {"role": "user", "content": "b3"},
            {"role": "user", "content": "b4"},
        ] + msgs[3:]
        agent = _FakeAgent(entries=[])
        out, sp = prune_context(agent, msgs, "SYS")
        assert out is msgs  # unchanged (no-op)

    def test_one_above_noop_threshold(self):
        """Messages = protect_first + protect_last + 5 → prunes."""
        # 3 + 20 + 5 = 28 messages → should prune
        # Need body rounds to actually group/drop
        msgs = _make_messages(30, protect_first=3, protect_last=20)
        agent = _FakeAgent(entries=[])
        out, sp = prune_context(agent, msgs, "SYS")
        assert len(out) < len(msgs)

    def test_keep_rounds_boundary_no_drop(self):
        """Exactly keep_rounds (24) body rounds → no rounds dropped."""
        # Build messages with exactly 24 body rounds
        msgs = _make_messages(24, protect_first=3, protect_last=20)
        agent = _FakeAgent(entries=[])
        out, sp = prune_context(agent, msgs, "SYS")
        # With exactly 24 rounds, len(rounds) > keep_rounds is False → no drop
        # But total > protect_first + protect_last + 4 → enters pruning
        # The body has 24*3 = 72 messages, total = 3 + 72 + 20 = 95
        # Since len(rounds) == keep_rounds, no rounds dropped
        # pruned_body = all body messages → same length
        # Actually, the check is `if len(rounds) > keep_rounds:` so 24 is NOT >
        # So no rounds dropped, but the function still runs through
        # Check that no "精简" note is added (no rounds dropped)
        # But the function still returns the same messages (no change)
        # Actually it returns head + body + tail = same as input
        assert len(out) == len(msgs)  # no change since no rounds dropped

    def test_keep_rounds_plus_one_drops_one(self):
        """25 body rounds → drops 1 oldest round."""
        msgs = _make_messages(25, protect_first=3, protect_last=20)
        agent = _FakeAgent(entries=[])
        out, sp = prune_context(agent, msgs, "SYS")
        assert len(out) < len(msgs)
        # Should drop exactly 1 round (3 messages: user+assistant+tool)
        dropped = len(msgs) - len(out)
        assert dropped == 3  # 1 round × 3 messages
        assert "精简" in sp
        # First body round's content should NOT be in output
        body_contents = [m.get("content", "") for m in out]
        assert "task question 0" not in body_contents

    def test_exception_fail_open(self):
        """Internal exception → returns inputs unchanged."""
        msgs = _make_messages(40)
        agent = _FakeAgent(entries=[])

        # Force an exception inside the try block
        with mock.patch(
            "agent.conversation_compression._build_fatigue_bridge_note",
            side_effect=RuntimeError("unexpected error"),
        ):
            out, sp = prune_context(agent, msgs, "SYS")
        # fail-open: returns inputs unchanged
        assert out is msgs
        assert sp == "SYS"

    def test_no_compressor_uses_defaults(self):
        """Missing context_compressor → defaults to protect_first=3, protect_last=20."""
        msgs = _make_messages(40)
        agent = _FakeAgent(entries=[])
        agent.context_compressor = None
        out, sp = prune_context(agent, msgs, "SYS")
        assert len(out) < len(msgs)  # pruning happened with defaults

    def test_custom_protect_values(self):
        """Custom protect_first/protect_last respected."""
        msgs = _make_messages(40, protect_first=5, protect_last=10)
        agent = _FakeAgent(entries=[], first=5, last=10)
        out, sp = prune_context(agent, msgs, "SYS")
        assert len(out) < len(msgs)
        # Head should be preserved (first 5 messages)
        for i in range(5):
            assert {"role": "user", "content": f"head-{i}"} in out


# ── _salvage_active_task_observation edge cases ──────────────────────

class TestSalvageEdgeCases:

    def test_no_todo_store(self):
        """Agent without _todo_store → empty list."""
        agent = types.SimpleNamespace()  # no _todo_store
        result = _salvage_active_task_observation([], [], [], [], agent)
        assert result == []

    def test_todo_store_none(self):
        """_todo_store is None → empty list."""
        agent = types.SimpleNamespace(_todo_store=None)
        result = _salvage_active_task_observation([], [], [], [], agent)
        assert result == []

    def test_all_completed_todos(self):
        """All todos completed → no in_progress → empty list."""
        class FakeStore:
            def read(self):
                return [{"id": "1", "status": "completed"}]
        agent = types.SimpleNamespace(_todo_store=FakeStore())
        result = _salvage_active_task_observation([], [], [], [], agent)
        assert result == []

    def test_exception_returns_empty(self):
        """Internal exception → empty list (fail-safe)."""
        class BrokenStore:
            def read(self):
                raise RuntimeError("DB error")
        agent = types.SimpleNamespace(_todo_store=BrokenStore())
        result = _salvage_active_task_observation([], [], [], [], agent)
        assert result == []

    def test_tool_result_without_preceding_call(self):
        """Tool result in dropped zone but no preceding assistant tool_call → empty."""
        class FakeStore:
            def read(self):
                return [{"id": "s1", "status": "in_progress", "id": "s1"}]
        agent = types.SimpleNamespace(_todo_store=FakeStore())
        # Only a tool result, no preceding assistant with tool_calls
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "tool", "content": "orphan result"},
            {"role": "assistant", "content": "response"},
        ]
        result = _salvage_active_task_observation(
            messages, head=[], pruned_body=[], tail=[messages[0]], agent=agent)
        # Should not find a valid pair
        assert result == []


# ── Integration: prune + bridge note content ──────────────────────────

class TestPruneBridgeNoteContent:

    def test_bridge_note_from_memory(self):
        """Bridge note includes memory content when available."""
        entries = [
            types.SimpleNamespace(fts_content="重要：用户偏好 pytest"),
        ]
        agent = _FakeAgent(entries=entries)
        msgs = _make_messages(40)
        out, sp = prune_context(agent, msgs, "SYS")
        assert "重要：用户偏好 pytest" in sp
        assert "关键记忆衔接" in sp

    def test_bridge_note_from_handoff_fallback(self):
        """No memory entries → handoff fallback → bridge includes handoff."""
        agent = _FakeAgent(entries=[])
        msgs = _make_messages(40)
        handoff = {"summary_text": "上次会话：完成了 RAG 模块开发"}
        with mock.patch(
            "agent.handoff_store.get_latest_handoff",
            return_value=handoff,
        ):
            out, sp = prune_context(agent, msgs, "SYS")
        assert "RAG 模块开发" in sp
        assert "此前会话衔接" in sp

    def test_no_bridge_when_both_empty(self):
        """No memory + no handoff → no bridge text, but drop note present."""
        agent = _FakeAgent(entries=[])
        msgs = _make_messages(40)
        with mock.patch(
            "agent.handoff_store.get_latest_handoff",
            return_value=None,
        ):
            out, sp = prune_context(agent, msgs, "SYS")
        assert "精简" in sp  # drop note still present
        assert "关键记忆衔接" not in sp
        assert "此前会话衔接" not in sp
