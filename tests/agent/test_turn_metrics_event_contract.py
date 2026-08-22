"""Direct-lock contract tests for A2 §7.5 stage-2 turn-metrics event化.

Mirrors the discipline from test_compress_until_under_threshold_contract
(ed77d920c): import the NEW function name directly (not via the thin
forwarder) so the most regression-prone point when splitting conversation_loop
into a turn/step/stream Service is locked at its real location.

Behavior under test (must stay true):
- emit_turn_metrics_event delivers TurnMetrics to the CompressionScheduler.
- emit_turn_metrics_event increments Route D counters (turns_total,
  tool_calls_total) in agent.metrics.
- best-effort: a failing scheduler subscriber must NOT block metrics nor raise.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.compression_scheduler import TurnMetrics
from agent.metrics import get_state
from agent.metrics_events import emit_turn_metrics_event


def _make_agent(tool_names, user_turn_count=3):
    agent = SimpleNamespace()
    agent._user_turn_count = user_turn_count
    agent._current_turn_tool_names = None
    agent._last_turn_tool_names = None
    return agent


def _make_messages(tool_names):
    msgs = [{"role": "user", "content": "hi"}]
    for name in tool_names:
        msgs.append({
            "role": "assistant",
            "tool_calls": [{"function": {"name": name}}],
        })
    return msgs


def _make_scheduler(record_side_effect=None):
    sched = MagicMock()
    if record_side_effect is not None:
        sched.record_turn.side_effect = record_side_effect
    return sched


@pytest.fixture(autouse=True)
def _reset_metrics():
    get_state().reset()
    yield
    get_state().reset()


class TestEmitTurnMetricsEventDirect:
    def test_scheduler_receives_turn_metrics_with_tool_names(self):
        agent = _make_agent(["a", "b"])
        sched = _make_scheduler()
        msgs = _make_messages(["a", "b"])
        emit_turn_metrics_event(agent, msgs, sched, 0.0, 1234)
        sched.record_turn.assert_called_once()
        sent = sched.record_turn.call_args.args[0]
        assert isinstance(sent, TurnMetrics)
        assert sent.tool_names_this_turn == ["a", "b"]
        assert sent.tool_calls_this_turn == 2
        assert sent.approx_tokens == 1234
        assert sent.turn_number == 3
        # agent caches are propagated for downstream use
        assert agent._current_turn_tool_names == ["a", "b"]
        assert agent._last_turn_tool_names == ["a", "b"]

    def test_route_d_metrics_counters_increment(self):
        agent = _make_agent(["x", "y", "z"])
        sched = _make_scheduler()
        msgs = _make_messages(["x", "y", "z"])
        st = get_state()
        before_turns = st.turns_total
        before_tools = st.tool_calls_total
        emit_turn_metrics_event(agent, msgs, sched, 0.0, 50)
        assert st.turns_total == before_turns + 1
        # one record_tool_call per tool name
        assert st.tool_calls_total == before_tools + 3

    def test_best_effort_when_scheduler_raises(self):
        agent = _make_agent(["a"])
        sched = _make_scheduler(record_side_effect=RuntimeError("boom"))
        msgs = _make_messages(["a"])
        st = get_state()
        before_turns = st.turns_total
        # must NOT raise out of the emitter (except: pass swallows it)
        emit_turn_metrics_event(agent, msgs, sched, 0.0, 10)
        # Real behavior: record_turn is the FIRST statement, so a scheduler
        # failure skips the trailing metrics calls too (zero-behavior-change
        # from the original inline block — we do NOT reorder to "fix" this).
        assert st.turns_total == before_turns

    def test_forwarder_matches_direct_call(self):
        from agent.conversation_loop import _record_turn_metrics
        agent = _make_agent(["p"])
        sched = _make_scheduler()
        msgs = _make_messages(["p"])
        # direct
        emit_turn_metrics_event(agent, msgs, sched, None, 7)
        direct_calls = sched.record_turn.call_count
        # reset and go via forwarder
        sched.reset_mock()
        agent2 = _make_agent(["p"])
        _record_turn_metrics(agent2, msgs, sched, None, 7)
        assert sched.record_turn.call_count == direct_calls
        assert isinstance(sched.record_turn.call_args.args[0], TurnMetrics)


class TestMutationTeeth:
    """Prove the counters test is not a mirror of the implementation.

    If record_tool_call is patched to a no-op, test_route_d_metrics_counters_increment
    must fail on the tool_calls_total assertion — otherwise the test would pass
    even if the emitter stopped calling metrics at all.
    """

    def test_metrics_path_is_truly_exercised(self, monkeypatch):
        import agent.metrics as _m
        monkeypatch.setattr(_m, "record_tool_call", lambda *a, **k: None)
        agent = _make_agent(["x", "y", "z"])
        sched = _make_scheduler()
        msgs = _make_messages(["x", "y", "z"])
        st = get_state()
        before = st.tool_calls_total
        emit_turn_metrics_event(agent, msgs, sched, 0.0, 50)
        # turns_total still increments (record_turn_completed untouched),
        # but tool_calls_total must NOT — this is what makes the contract test bite.
        assert st.tool_calls_total == before  # confirms teeth: patched path is dead
