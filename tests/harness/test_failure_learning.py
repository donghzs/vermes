"""Tests for harness.failure_learning — H4.1 persistent failure classification learning."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.failure_learning import (
    FailurePattern,
    FailureLedger,
    get_ledger,
)


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def tmp_ledger_path(tmp_path: Path) -> Path:
    """Provide a temporary ledger file path."""
    return tmp_path / "harness" / "failure_patterns.json"


@pytest.fixture
def ledger(tmp_ledger_path: Path) -> FailureLedger:
    """Provide a fresh FailureLedger with a temp path."""
    return FailureLedger(ledger_path=tmp_ledger_path, max_records=100)


# --------------------------------------------------------------------------- #
# FailurePattern dataclass                                                     #
# --------------------------------------------------------------------------- #


class TestFailurePattern:
    def test_default_values(self):
        fp = FailurePattern(pattern_str="network_error", tool_name="web_search")
        assert fp.pattern_str == "network_error"
        assert fp.tool_name == "web_search"
        assert fp.count == 1
        assert fp.last_seen > 0
        assert fp.examples == []

    def test_to_dict(self):
        fp = FailurePattern(
            pattern_str="timeout",
            tool_name="browser_tool",
            count=5,
            examples=["error1", "error2"],
        )
        d = fp.to_dict()
        assert d["pattern_str"] == "timeout"
        assert d["tool_name"] == "browser_tool"
        assert d["count"] == 5
        assert d["examples"] == ["error1", "error2"]

    def test_from_dict(self):
        data = {
            "pattern_str": "permission_denied",
            "tool_name": "write_file",
            "count": 3,
            "last_seen": 1234567890,
            "examples": ["err1"],
        }
        fp = FailurePattern.from_dict(data)
        assert fp.pattern_str == "permission_denied"
        assert fp.tool_name == "write_file"
        assert fp.count == 3
        assert fp.last_seen == 1234567890
        assert fp.examples == ["err1"]

    def test_from_dict_missing_fields(self):
        """from_dict should handle missing fields gracefully."""
        fp = FailurePattern.from_dict({})
        assert fp.pattern_str == ""
        assert fp.tool_name == ""
        assert fp.count == 1


# --------------------------------------------------------------------------- #
# FailureLedger — record                                                       #
# --------------------------------------------------------------------------- #


class TestLedgerRecord:
    def test_record_exception(self, ledger: FailureLedger):
        ledger.record("web_search", RuntimeError("connection timeout"), {"query": "test"})
        patterns = ledger.get_patterns("web_search")
        assert len(patterns) == 1
        assert patterns[0].tool_name == "web_search"
        assert patterns[0].count == 1

    def test_record_string_error(self, ledger: FailureLedger):
        ledger.record("terminal_tool", "some error string")
        patterns = ledger.get_patterns("terminal_tool")
        assert len(patterns) == 1

    def test_record_multiple_same_pattern_increments_count(self, ledger: FailureLedger):
        for _ in range(3):
            ledger.record("web_search", ConnectionError("timeout"))
        patterns = ledger.get_patterns("web_search")
        assert len(patterns) == 1
        assert patterns[0].count == 3

    def test_record_different_patterns_for_same_tool(self, ledger: FailureLedger):
        ledger.record("web_search", ConnectionError("timeout"))
        ledger.record("web_search", FileNotFoundError("not found"))
        patterns = ledger.get_patterns("web_search")
        assert len(patterns) == 2

    def test_record_different_tools(self, ledger: FailureLedger):
        ledger.record("web_search", RuntimeError("err1"))
        ledger.record("terminal_tool", RuntimeError("err2"))
        assert len(ledger.get_patterns("web_search")) == 1
        assert len(ledger.get_patterns("terminal_tool")) == 1
        assert len(ledger.get_patterns()) == 2

    def test_record_stores_examples(self, ledger: FailureLedger):
        ledger.record("web_search", ValueError("bad input"))
        patterns = ledger.get_patterns("web_search")
        assert len(patterns[0].examples) == 1
        assert "bad input" in patterns[0].examples[0]

    def test_record_limits_examples_to_3(self, ledger: FailureLedger):
        for i in range(5):
            ledger.record("web_search", ValueError(f"error {i}"))
        patterns = ledger.get_patterns("web_search")
        assert len(patterns[0].examples) <= 3

    def test_record_truncates_long_error_messages(self, ledger: FailureLedger):
        long_msg = "x" * 500
        ledger.record("web_search", ValueError(long_msg))
        patterns = ledger.get_patterns("web_search")
        assert all(len(ex) <= 200 for ex in patterns[0].examples)

    def test_record_never_raises(self, ledger: FailureLedger):
        """record should never raise, even with weird inputs."""
        ledger.record("web_search", None)  # type: ignore
        ledger.record("", RuntimeError("empty tool"))
        ledger.record("web_search", "")  # empty error string
        # No exception = pass


# --------------------------------------------------------------------------- #
# FailureLedger — persistence                                                  #
# --------------------------------------------------------------------------- #


class TestLedgerPersistence:
    def test_persists_to_disk(self, tmp_ledger_path: Path):
        ledger1 = FailureLedger(ledger_path=tmp_ledger_path)
        ledger1.record("web_search", ConnectionError("timeout"))
        assert tmp_ledger_path.exists()

        # Load a new ledger from the same path.
        ledger2 = FailureLedger(ledger_path=tmp_ledger_path)
        patterns = ledger2.get_patterns("web_search")
        assert len(patterns) == 1
        assert patterns[0].count == 1

    def test_atomic_write_no_corruption(self, tmp_ledger_path: Path):
        ledger = FailureLedger(ledger_path=tmp_ledger_path)
        ledger.record("web_search", RuntimeError("err"))
        # File should be valid JSON.
        with open(tmp_ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "patterns" in data
        assert "record_count" in data

    def test_load_nonexistent_file_is_empty(self, tmp_path: Path):
        ledger = FailureLedger(ledger_path=tmp_path / "nonexistent.json")
        assert ledger.get_patterns() == []


# --------------------------------------------------------------------------- #
# FailureLedger — should_warn                                                  #
# --------------------------------------------------------------------------- #


class TestLedgerShouldWarn:
    def test_no_patterns_returns_none(self, ledger: FailureLedger):
        assert ledger.should_warn("web_search") is None

    def test_single_failure_no_warning(self, ledger: FailureLedger):
        """A single failure shouldn't trigger a warning (need 3+)."""
        ledger.record("web_search", ConnectionError("timeout"))
        assert ledger.should_warn("web_search") is None

    def test_three_failures_triggers_warning(self, ledger: FailureLedger):
        for _ in range(3):
            ledger.record("web_search", ConnectionError("timeout"))
        warning = ledger.should_warn("web_search")
        assert warning is not None
        assert "web_search" in warning
        assert "recurring" in warning.lower()

    def test_warning_includes_pattern_info(self, ledger: FailureLedger):
        for _ in range(5):
            ledger.record("browser_tool", FileNotFoundError("not found"))
        warning = ledger.should_warn("browser_tool")
        assert warning is not None
        assert "browser_tool" in warning

    def test_different_tool_no_warning(self, ledger: FailureLedger):
        for _ in range(5):
            ledger.record("web_search", ConnectionError("timeout"))
        # terminal_tool has no failures → no warning
        assert ledger.should_warn("terminal_tool") is None

    def test_should_warn_never_raises(self, ledger: FailureLedger):
        assert ledger.should_warn("") is not None or ledger.should_warn("") is None
        assert ledger.should_warn(None) is not None or ledger.should_warn(None) is None  # type: ignore


# --------------------------------------------------------------------------- #
# FailureLedger — FIFO eviction                                               #
# --------------------------------------------------------------------------- #


class TestLedgerEviction:
    def test_max_records_eviction(self, tmp_path: Path):
        ledger = FailureLedger(
            ledger_path=tmp_path / "test.json",
            max_records=3,
        )
        # Record 5 different patterns — only 3 should be kept.
        for i in range(5):
            ledger.record(f"tool_{i}", RuntimeError(f"error {i}"))
            time.sleep(0.01)  # ensure distinct last_seen
        patterns = ledger.get_patterns()
        assert len(patterns) <= 3

    def test_clear(self, ledger: FailureLedger):
        ledger.record("web_search", RuntimeError("err"))
        assert len(ledger.get_patterns()) == 1
        ledger.clear()
        assert len(ledger.get_patterns()) == 0


# --------------------------------------------------------------------------- #
# FailureLedger — fail-open IO                                                #
# --------------------------------------------------------------------------- #


class TestLedgerFailOpen:
    def test_save_to_unwritable_path_does_not_raise(self, tmp_path: Path):
        """If the ledger path is unwritable, operations should still succeed."""
        # Use a path under a non-existent directory that can't be created.
        # Actually, mkdir(parents=True) will create it, so use /dev/null path.
        ledger = FailureLedger(ledger_path=Path("/dev/null/cannot_write.json"))
        # This should not raise:
        ledger.record("web_search", RuntimeError("err"))
        # And should still work in-memory:
        assert len(ledger.get_patterns("web_search")) == 1

    def test_record_with_none_error_does_not_raise(self, ledger: FailureLedger):
        ledger.record("web_search", None)  # type: ignore
        # Should not raise — fail-open


# --------------------------------------------------------------------------- #
# get_ledger singleton                                                         #
# --------------------------------------------------------------------------- #


class TestGetLedger:
    def test_get_ledger_returns_singleton(self):
        l1 = get_ledger()
        l2 = get_ledger()
        assert l1 is l2

    def test_get_ledger_is_failure_ledger(self):
        assert isinstance(get_ledger(), FailureLedger)
