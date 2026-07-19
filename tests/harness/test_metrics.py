"""Tests for harness/metrics.py — structured federation & recall metrics."""

import threading

import pytest

from harness.metrics import MetricsCollector, get_metrics, track_recall_latency


class TestMetricsCollector:
    """Unit tests for the in-memory metrics collector."""

    def setup_method(self):
        self.m = MetricsCollector()
        self.m.reset()

    def test_record_federation_search(self):
        self.m.record_federation_search("rag", total=5, hits=3)
        s = self.m.summary()
        assert s["federation"]["searches"]["rag"] == 1
        assert s["federation"]["hits"]["rag"] == 3

    def test_record_federation_skip(self):
        self.m.record_federation_skip("retaindb", "signature_mismatch")
        s = self.m.summary()
        assert s["federation"]["skips"]["retaindb"] == 1
        assert s["federation"]["skip_reasons"]["retaindb"] == "signature_mismatch"

    def test_record_federation_error(self):
        self.m.record_federation_error("honcho")
        s = self.m.summary()
        assert s["federation"]["errors"]["honcho"] == 1

    def test_record_recall_layer(self):
        self.m.record_recall_layer("L4_federation", hits=5)
        self.m.record_recall_layer("L4_federation", hits=3)
        s = self.m.summary()
        assert s["recall_layers"]["hits"]["L4_federation"] == 8

    def test_record_dedup_collision(self):
        self.m.record_dedup_collision("L4")
        self.m.record_dedup_collision("L4")
        self.m.record_dedup_collision("L3")
        s = self.m.summary()
        assert s["recall_layers"]["dedup_collisions"]["L4"] == 2
        assert s["recall_layers"]["dedup_collisions"]["L3"] == 1

    def test_record_recall_latency(self):
        self.m.record_recall_latency_ms(12.5)
        self.m.record_recall_latency_ms(7.5)
        s = self.m.summary()
        assert s["recall_layers"]["invocations"] == 2
        assert s["recall_layers"]["avg_latency_ms"] == 10.0

    def test_record_per_turn(self):
        self.m.record_per_turn(hits_total=0)
        self.m.record_per_turn(hits_total=5)
        s = self.m.summary()
        assert s["per_turn"]["invocations"] == 2
        assert s["per_turn"]["hits_total"] == 5

    def test_reset(self):
        self.m.record_federation_search("rag", total=1, hits=1)
        self.m.record_recall_layer("L1", hits=1)
        self.m.reset()
        s = self.m.summary()
        assert s["federation"]["searches"] == {}
        assert s["recall_layers"]["hits"] == {}
        assert s["per_turn"]["invocations"] == 0

    def test_thread_safety(self):
        """Concurrent writes from multiple threads should not crash."""
        def worker():
            for i in range(100):
                m.record_federation_search("rag", total=i, hits=i % 3)
                m.record_recall_layer("L4", hits=1)
                m.record_per_turn(hits_total=1)

        m = self.m
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        s = m.summary()
        assert s["federation"]["searches"]["rag"] == 400
        assert s["recall_layers"]["hits"]["L4"] == 400
        assert s["per_turn"]["invocations"] == 400

    def test_fail_open_on_exception(self):
        """Metrics recording should never raise."""
        # Pass invalid types — should not raise
        self.m.record_federation_search(None, total="bad", hits="bad")  # type: ignore
        self.m.record_recall_layer(None, hits="bad")  # type: ignore

    def test_latency_tracker_context_manager(self):
        with track_recall_latency():
            pass  # Simulate some work
        s = self.m.summary()
        # The singleton is separate from self.m, so check singleton
        singleton = get_metrics()
        ss = singleton.summary()
        assert ss["recall_layers"]["invocations"] >= 1


class TestGetMetricsSingleton:
    """Test the singleton accessor."""

    def test_singleton_returns_same_instance(self):
        a = get_metrics()
        b = get_metrics()
        assert a is b

    def test_singleton_summary_is_dict(self):
        s = get_metrics().summary()
        assert isinstance(s, dict)
        assert "federation" in s
        assert "recall_layers" in s
        assert "per_turn" in s
