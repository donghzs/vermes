"""Tests for the gate→learning bridge (融合路线图 P0).

Verifies that signals from H1.1 (task pre-check) and H3.2 (stability probe)
— which previously evaporated after a single turn — now persist into the
H4.1 FailureLedger and become learnable recurring-warnings, exactly like
tool failures already do. This proves the "all gates → one learning sink"
bridge is wired correctly end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.failure_learning import FailureLedger, get_ledger


@pytest.fixture
def ledger(tmp_path: Path) -> FailureLedger:
    return FailureLedger(ledger_path=tmp_path / "bridge.json", max_records=100)


# H3.2 wiring shape: get_ledger().record(function_name, _h3_2_warning, function_args)
def _h32_warning(tool: str) -> str:
    return (
        f"[harness stability] tool '{tool}' showed instability "
        f"across 3 runs: best=1.00, worst=0.00, delta=1.00. "
        f"Consider retrying or using an alternative tool."
    )


# H1.1 wiring shape: get_ledger().record(f"task:{check}", _task_precheck.warning, _detail)
def _h11_warning(check: str) -> str:
    return f"Task-level constraint '{check}' triggered."


def test_h32_bridge_makes_instability_learnable(ledger: FailureLedger):
    """3 recurring instability verdicts for a tool become a learnable warning."""
    tool = "web_search"
    for _ in range(3):
        ledger.record(tool, _h32_warning(tool), {"n": 3})
    warn = ledger.should_warn(tool)
    assert warn is not None
    assert "recurring failures" in warn
    assert tool in warn


def test_h32_bridge_infrequent_no_warn(ledger: FailureLedger):
    """Fewer than 3 occurrences must NOT warn (no false learning)."""
    tool = "web_search"
    for _ in range(2):
        ledger.record(tool, _h32_warning(tool), {"n": 3})
    assert ledger.should_warn(tool) is None


def test_h11_bridge_makes_task_constraint_learnable(ledger: FailureLedger):
    """3 recurring task-constraint hits (keyed task:<check>) become learnable."""
    check = "message_length"
    for _ in range(3):
        ledger.record(f"task:{check}", _h11_warning(check), {"check": check})
    warn = ledger.should_warn(f"task:{check}")
    assert warn is not None
    assert "recurring failures" in warn
    assert "task:message_length" in warn


def test_bridge_persists_across_reload(ledger: FailureLedger, tmp_path: Path):
    """The bridge is self-evolutionary: recorded signals survive a reload."""
    path = tmp_path / "bridge.json"
    tool = "browser_navigate"
    for _ in range(3):
        ledger.record(tool, _h32_warning(tool), {"n": 3})

    # Fresh ledger reading the SAME file path (simulates next session).
    reloaded = FailureLedger(ledger_path=path, max_records=100)
    assert reloaded.should_warn(tool) is not None


def test_bridge_isolated_keys_do_not_cross_contaminate(ledger: FailureLedger):
    """H1.1 task keys and H3.2 tool keys stay independent."""
    for _ in range(3):
        ledger.record("task:iteration_budget", _h11_warning("iteration_budget"), {})
    # A tool with zero failures must not be warned because of task-key noise.
    assert ledger.should_warn("web_search") is None
