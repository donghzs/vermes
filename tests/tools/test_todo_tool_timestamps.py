"""Tests for TodoStore timestamp stamping (started_at / finished_at).

These timestamps power the frontend real-time task drawer: per-step elapsed
time while in_progress, and total duration once finished. The extra fields
are additive — older frontends that ignore them keep working.
"""
import time

from tools.todo_tool import TodoStore


def _now():
    return time.time()


def test_in_progress_sets_started_at():
    s = TodoStore()
    s.write([{"id": "1", "content": "第一步", "status": "in_progress"}])
    items = s.read()
    assert len(items) == 1
    assert isinstance(items[0]["started_at"], (int, float))
    assert items[0]["finished_at"] is None


def test_completed_sets_finished_at_and_preserves_started_at():
    s = TodoStore()
    s.write([{"id": "1", "content": "第一步", "status": "in_progress"}])
    first = s.read()[0]
    time.sleep(0.01)
    s.write([{"id": "1", "content": "第一步", "status": "completed"}], merge=True)
    items = s.read()
    assert items[0]["status"] == "completed"
    # started_at preserved across the transition
    assert items[0]["started_at"] == first["started_at"]
    # finished_at recorded
    assert isinstance(items[0]["finished_at"], (int, float))
    assert items[0]["finished_at"] >= items[0]["started_at"]


def test_reentry_in_progress_keeps_original_started_at():
    s = TodoStore()
    s.write([{"id": "1", "content": "第一步", "status": "in_progress"}])
    t0 = s.read()[0]["started_at"]
    time.sleep(0.01)
    # agent re-marks the same step in_progress (no transition) — must keep t0
    s.write([{"id": "1", "status": "in_progress"}], merge=True)
    assert s.read()[0]["started_at"] == t0


def test_merge_preserves_timestamps_of_untouched_items():
    s = TodoStore()
    s.write([
        {"id": "1", "content": "A", "status": "completed"},
        {"id": "2", "content": "B", "status": "in_progress"},
    ])
    snap = {it["id"]: it for it in s.read()}
    # add a new item, leave 1 and 2 untouched
    s.write([{"id": "3", "content": "C", "status": "pending"}], merge=True)
    after = {it["id"]: it for it in s.read()}
    assert after["1"]["started_at"] == snap["1"]["started_at"]
    assert after["1"]["finished_at"] == snap["1"]["finished_at"]
    assert after["2"]["started_at"] == snap["2"]["started_at"]


def test_summary_unchanged_and_backward_compatible():
    """summary still reports counts only; started_at is per-item (ignored by old UIs)."""
    s = TodoStore()
    s.write([
        {"id": "1", "content": "A", "status": "completed"},
        {"id": "2", "content": "B", "status": "in_progress"},
        {"id": "3", "content": "C", "status": "pending"},
    ])
    from tools.todo_tool import todo_tool
    store = s
    out = todo_tool(store=store)  # read
    import json
    data = json.loads(out)
    assert data["summary"] == {"total": 3, "pending": 1, "in_progress": 1, "completed": 1, "cancelled": 0}
    # every item carries the optional timestamp fields
    for it in data["todos"]:
        assert "started_at" in it
        assert "finished_at" in it
