"""L3 live-hook adapter must be side-effect-free.

The per-turn prompt-injection path (``recall_context``) intentionally records
a self-assessment raw_event so Vermes can observe its own retrieval quality.
But the live L3 adapter behind ``memory_fabric.set_l3_live_hook``
(``recall_context_as_hits``) is reached on every ``memory_search`` — if it ran
the same path it would corrupt the recall subsystem's own evaluation data. It
must therefore use the pure read path (``_collect_recall_sections``).
"""
import agent.memory_recall as mr
from agent.memory_recall import recall_context_as_hits


def test_recall_context_as_hits_has_no_self_assessment_side_effect(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import agent.self_assessment as sa

    recorded = []
    monkeypatch.setattr(sa, "assess_and_record", lambda *a, **k: recorded.append(1))

    # No recall DBs present -> empty, fail-soft.
    hits = recall_context_as_hits("some user message about postgres tuning")
    assert isinstance(hits, list)
    assert recorded == [], "recall_context_as_hits must not call assess_and_record"


def test_recall_context_still_records_self_assessment(monkeypatch, tmp_path):
    """Sanity check: the prompt-injection path keeps its side effect."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import agent.self_assessment as sa

    recorded = []
    monkeypatch.setattr(sa, "assess_and_record", lambda *a, **k: recorded.append(1))

    mr.recall_context("some user message")
    assert recorded == [1], "recall_context must still record a self-assessment raw_event"
