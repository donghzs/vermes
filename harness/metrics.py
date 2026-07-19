"""Structured harness metrics for memory federation and recall layers.

Collects counters and timing for:
- L4 federation: per-provider hit counts, skip reasons, search latency
- recall_hierarchical: per-layer hit counts, dedup collisions, total latency
- Per-turn recall (B2 recall_hierarchical_per_turn): cumulative invocation stats

Design:
- Single process-local ``MetricsCollector`` singleton (no external deps).
- All methods are fail-open: metrics never block the request path.
- Consumed by harness observability (E3) — not wired into default request
  path unless explicitly enabled.

Usage::

    from harness.metrics import get_metrics

    m = get_metrics()
    m.record_federation_search(provider="rag", total=5, hits=3, skipped=False)
    m.record_federation_skip(provider="retaindb", reason="signature_mismatch")
    m.record_recall_layer(layer="L4", hits=3)
    m.record_dedup_collision(layer="L4")
    m.record_recall_latency_ms(12.5)

    summary = m.summary()  # dict snapshot
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Thread-safe structured metrics collector for memory/recall federation.

    All state is in-memory and process-local.  Call ``summary()`` for a
    snapshot dict (suitable for logging or an API endpoint).  Call ``reset()``
    to clear (e.g., between tests).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._federation_searches: Dict[str, int] = defaultdict(int)
        self._federation_hits: Dict[str, int] = defaultdict(int)
        self._federation_skips: Dict[str, int] = defaultdict(int)  # provider -> skip count
        self._federation_skip_reasons: Dict[str, str] = {}  # provider -> last reason
        self._federation_errors: Dict[str, int] = defaultdict(int)
        self._federation_latency_ms: Dict[str, List[float]] = defaultdict(list)

        self._layer_hits: Dict[str, int] = defaultdict(int)
        self._dedup_collisions: Dict[str, int] = defaultdict(int)
        self._recall_invocations: int = 0
        self._recall_latency_ms: List[float] = []

        self._per_turn_invocations: int = 0
        self._per_turn_hits_total: int = 0

    # -- Federation (L4 search_all) -----------------------------------------

    def record_federation_search(
        self,
        provider: str,
        total: int = 0,
        hits: int = 0,
        skipped: bool = False,
        latency_ms: float = 0.0,
    ) -> None:
        """Record one provider's contribution to a federation search round."""
        try:
            with self._lock:
                self._federation_searches[provider] += 1
                if skipped:
                    self._federation_skips[provider] += 1
                else:
                    self._federation_hits[provider] += hits
                if latency_ms > 0:
                    self._federation_latency_ms[provider].append(latency_ms)
        except Exception:
            logger.debug("metrics: federation_search recording failed", exc_info=True)

    def record_federation_skip(self, provider: str, reason: str) -> None:
        """Record that a provider was skipped during federation."""
        try:
            with self._lock:
                self._federation_skips[provider] += 1
                self._federation_skip_reasons[provider] = reason
        except Exception:
            logger.debug("metrics: federation_skip recording failed", exc_info=True)

    def record_federation_error(self, provider: str) -> None:
        """Record a non-fatal search error from a federation provider."""
        try:
            with self._lock:
                self._federation_errors[provider] += 1
        except Exception:
            logger.debug("metrics: federation_error recording failed", exc_info=True)

    # -- recall_hierarchical (L1–L4) ----------------------------------------

    def record_recall_layer(self, layer: str, hits: int = 0) -> None:
        """Record hits returned by one layer in recall_hierarchical."""
        try:
            with self._lock:
                self._layer_hits[layer] += hits
        except Exception:
            logger.debug("metrics: recall_layer recording failed", exc_info=True)

    def record_dedup_collision(self, layer: str) -> None:
        """Record a de-duplication collision in recall_hierarchical."""
        try:
            with self._lock:
                self._dedup_collisions[layer] += 1
        except Exception:
            logger.debug("metrics: dedup_collision recording failed", exc_info=True)

    def record_recall_latency_ms(self, latency_ms: float) -> None:
        """Record total recall_hierarchical latency in milliseconds."""
        try:
            with self._lock:
                self._recall_invocations += 1
                self._recall_latency_ms.append(latency_ms)
        except Exception:
            logger.debug("metrics: recall_latency recording failed", exc_info=True)

    # -- Per-turn recall (B2) -----------------------------------------------

    def record_per_turn(self, hits_total: int = 0) -> None:
        """Record one recall_hierarchical_per_turn invocation."""
        try:
            with self._lock:
                self._per_turn_invocations += 1
                self._per_turn_hits_total += hits_total
        except Exception:
            logger.debug("metrics: per_turn recording failed", exc_info=True)

    # -- Snapshot ------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Return a snapshot dict of all collected metrics."""
        with self._lock:
            avg_latency = (
                sum(self._recall_latency_ms) / len(self._recall_latency_ms)
                if self._recall_latency_ms
                else 0.0
            )
            return {
                "federation": {
                    "searches": dict(self._federation_searches),
                    "hits": dict(self._federation_hits),
                    "skips": dict(self._federation_skips),
                    "skip_reasons": dict(self._federation_skip_reasons),
                    "errors": dict(self._federation_errors),
                    "avg_latency_ms": {
                        p: sum(v) / len(v) if v else 0.0
                        for p, v in self._federation_latency_ms.items()
                    },
                },
                "recall_layers": {
                    "hits": dict(self._layer_hits),
                    "dedup_collisions": dict(self._dedup_collisions),
                    "invocations": self._recall_invocations,
                    "avg_latency_ms": avg_latency,
                },
                "per_turn": {
                    "invocations": self._per_turn_invocations,
                    "hits_total": self._per_turn_hits_total,
                },
            }

    def reset(self) -> None:
        """Clear all collected metrics (e.g., between tests)."""
        with self._lock:
            self._federation_searches.clear()
            self._federation_hits.clear()
            self._federation_skips.clear()
            self._federation_skip_reasons.clear()
            self._federation_errors.clear()
            self._federation_latency_ms.clear()
            self._layer_hits.clear()
            self._dedup_collisions.clear()
            self._recall_invocations = 0
            self._recall_latency_ms.clear()
            self._per_turn_invocations = 0
            self._per_turn_hits_total = 0


# -- Singleton -------------------------------------------------------------

_collector: Optional[MetricsCollector] = None
_singleton_lock = threading.Lock()


def get_metrics() -> MetricsCollector:
    """Return the process-local singleton MetricsCollector."""
    global _collector
    if _collector is None:
        with _singleton_lock:
            if _collector is None:
                _collector = MetricsCollector()
    return _collector


# -- Context manager for easy latency tracking -----------------------------

class _LatencyTracker:
    """Context manager that records latency in milliseconds."""

    __slots__ = ("_collector", "_key", "_start")

    def __init__(self, collector: MetricsCollector, key: str) -> None:
        self._collector = collector
        self._key = key
        self._start = 0.0

    def __enter__(self) -> "_LatencyTracker":
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc: Any) -> None:
        elapsed_ms = (time.monotonic() - self._start) * 1000.0
        if self._key == "recall_hierarchical":
            self._collector.record_recall_latency_ms(elapsed_ms)
        # Extend with more keys as needed


def track_recall_latency() -> _LatencyTracker:
    """Context manager to track recall_hierarchical latency."""
    return _LatencyTracker(get_metrics(), "recall_hierarchical")
