"""Tests for agent.veto_ledger — consecutive veto tracking.

Run: python -m pytest tests/test_veto_ledger.py -v
"""

import json
import pytest

from agent.veto_ledger import (
    VetoEntry,
    VetoDecision,
    VetoLedger,
    get_veto_ledger,
    reset_veto_ledger,
    DEFAULT_VETO_THRESHOLD,
)


@pytest.fixture
def ledger():
    """Fresh ledger for each test."""
    return VetoLedger(threshold=3)


# ---------------------------------------------------------------------------
# VetoEntry / VetoDecision tests
# ---------------------------------------------------------------------------


class TestVetoEntry:
    def test_to_dict(self):
        e = VetoEntry(tool_name="write_file", args_hash="abc123", count=2, last_message="err")
        d = e.to_dict()
        assert d["tool_name"] == "write_file"
        assert d["args_hash"] == "abc123"
        assert d["count"] == 2
        assert d["last_message"] == "err"


class TestVetoDecision:
    def test_to_dict(self):
        d = VetoDecision(should_pause=True, tool_name="patch", count=3, threshold=3, message="stop")
        result = d.to_dict()
        assert result["should_pause"] is True
        assert result["count"] == 3


# ---------------------------------------------------------------------------
# VetoLedger tests
# ---------------------------------------------------------------------------


class TestVetoLedger:
    def test_first_veto_no_pause(self, ledger):
        d = ledger.record_veto("write_file", "hash1")
        assert d.should_pause is False
        assert d.count == 1

    def test_second_veto_no_pause(self, ledger):
        ledger.record_veto("write_file", "hash1")
        d = ledger.record_veto("write_file", "hash1")
        assert d.should_pause is False
        assert d.count == 2

    def test_third_veto_triggers_pause(self, ledger):
        ledger.record_veto("write_file", "hash1")
        ledger.record_veto("write_file", "hash1")
        d = ledger.record_veto("write_file", "hash1")
        assert d.should_pause is True
        assert d.count == 3
        assert "write_file" in d.message
        assert "3" in d.message

    def test_success_resets_counter(self, ledger):
        ledger.record_veto("write_file", "hash1")
        ledger.record_veto("write_file", "hash1")
        # Success resets
        ledger.record_success("write_file", "hash1")
        # Next veto should start from 1
        d = ledger.record_veto("write_file", "hash1")
        assert d.count == 1
        assert d.should_pause is False

    def test_different_args_tracked_separately(self, ledger):
        ledger.record_veto("write_file", "hash1")
        d = ledger.record_veto("write_file", "hash2")
        assert d.count == 1
        assert d.should_pause is False

    def test_different_tools_tracked_separately(self, ledger):
        ledger.record_veto("write_file", "hash1")
        d = ledger.record_veto("patch", "hash1")
        assert d.count == 1

    def test_get_count(self, ledger):
        ledger.record_veto("write_file", "hash1")
        ledger.record_veto("write_file", "hash1")
        assert ledger.get_count("write_file", "hash1") == 2

    def test_get_count_nonexistent(self, ledger):
        assert ledger.get_count("write_file", "nonexistent") == 0

    def test_reset_all(self, ledger):
        ledger.record_veto("write_file", "hash1")
        ledger.record_veto("patch", "hash2")
        ledger.reset()
        assert ledger.get_count("write_file", "hash1") == 0
        assert ledger.get_count("patch", "hash2") == 0

    def test_reset_specific_tool(self, ledger):
        ledger.record_veto("write_file", "hash1")
        ledger.record_veto("patch", "hash2")
        ledger.reset("write_file")
        assert ledger.get_count("write_file", "hash1") == 0
        assert ledger.get_count("patch", "hash2") == 1

    def test_get_all_vetoes(self, ledger):
        ledger.record_veto("write_file", "hash1")
        ledger.record_veto("patch", "hash2")
        all_v = ledger.get_all_vetoes()
        assert len(all_v) == 2

    def test_message_stored(self, ledger):
        ledger.record_veto("write_file", "hash1", message="permission denied")
        all_v = ledger.get_all_vetoes()
        assert any(v.last_message == "permission denied" for v in all_v)

    def test_custom_threshold(self):
        ledger = VetoLedger(threshold=5)
        for i in range(4):
            d = ledger.record_veto("tool", "hash")
            assert d.should_pause is False
        d = ledger.record_veto("tool", "hash")
        assert d.should_pause is True
        assert d.count == 5

    def test_default_threshold_value(self):
        assert DEFAULT_VETO_THRESHOLD == 3

    def test_to_json(self, ledger):
        ledger.record_veto("write_file", "hash1")
        j = ledger.to_json()
        data = json.loads(j)
        assert "vetoes" in data
        assert "threshold" in data
        assert data["threshold"] == 3

    def test_from_dict_roundtrip(self, ledger):
        ledger.record_veto("write_file", "hash1")
        ledger.record_veto("write_file", "hash1")
        d = ledger.to_dict()
        restored = VetoLedger.from_dict(d)
        assert restored.get_count("write_file", "hash1") == 2
        assert restored.threshold == 3

    def test_eviction_when_over_limit(self):
        """Test that old entries are evicted when max_entries is exceeded."""
        ledger = VetoLedger(threshold=100, max_entries=3)
        ledger.record_veto("tool1", "h1")
        ledger.record_veto("tool2", "h2")
        ledger.record_veto("tool3", "h3")
        ledger.record_veto("tool4", "h4")  # should evict one
        all_v = ledger.get_all_vetoes()
        assert len(all_v) <= 3

    def test_thread_safe(self, ledger):
        """Basic thread safety — concurrent vetoes should not crash."""
        import threading

        def worker():
            for i in range(20):
                ledger.record_veto("tool", f"hash{i % 3}")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Should not crash, counts should be reasonable
        assert len(ledger.get_all_vetoes()) <= 3  # only 3 unique hashes


# ---------------------------------------------------------------------------
# Singleton tests
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_veto_ledger_returns_same_instance(self):
        reset_veto_ledger()
        l1 = get_veto_ledger()
        l2 = get_veto_ledger()
        assert l1 is l2

    def test_reset_veto_ledger(self):
        l1 = get_veto_ledger()
        reset_veto_ledger()
        l2 = get_veto_ledger()
        assert l1 is not l2
