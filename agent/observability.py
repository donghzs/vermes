"""OpenTelemetry observability layer for Vermes (取法 Codex/dsh otel trace/span).

A thin, fail-open wrapper around ``opentelemetry``. The goal is to give the
runtime a *consistent span API* mirroring Codex/dsh's trace/span discipline
without forcing a hard dependency on a collector:

- When ``opentelemetry`` is NOT installed → every span is a **no-op** (the
  code path still runs, just records nothing). No import error, no crash.
- When ``opentelemetry`` IS installed but no OTLP collector is reachable →
  spans are emitted to an in-memory/no-op exporter (fail-open, no network
  blocking).
- When a collector IS configured (``OTEL_EXPORTER_OTLP_ENDPOINT`` or
  ``VERMES_OTEL_ENDPOINT``) → spans flow out normally.

Self-owned ``metrics.py`` (Prometheus text) is preserved as the in-process
fallback; this module is the cross-process / cross-session trace layer.

Usage::

    from agent.observability import tracer, span
    with span("turn", attributes={"task_id": tid}):
        ...
    # or manually:
    with tracer.start_as_current_span("dispatch") as sp:
        sp.set_attribute("tool", name)
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)

# Module-level cached handles. ``None`` means "otel unavailable → no-op".
_TRACER: Any = None
_OTEL_AVAILABLE: Optional[bool] = None


def otel_available() -> bool:
    """Return True iff the opentelemetry SDK is importable (cached)."""
    global _OTEL_AVAILABLE
    if _OTEL_AVAILABLE is not None:
        return _OTEL_AVAILABLE
    try:
        import opentelemetry  # noqa: F401
        import opentelemetry.trace  # noqa: F401
        _OTEL_AVAILABLE = True
    except Exception:
        _OTEL_AVAILABLE = False
    return _OTEL_AVAILABLE


def _get_tracer() -> Any:
    """Lazily build (and cache) the global tracer, or return a no-op sentinel.

    Never raises. When otel is unavailable or setup fails, returns a small
    no-op object whose ``start_as_current_span`` is a contextmanager that
    yields a no-op span.
    """
    global _TRACER
    if _TRACER is not None:
        return _TRACER

    if not otel_available():
        _TRACER = _NoopTracer()
        return _TRACER

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )

        # Prefer an OTLP exporter when an endpoint is configured; otherwise
        # fall back to a no-op exporter (no network, no crash).
        endpoint = os.getenv("VERMES_OTEL_ENDPOINT") or os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT"
        )
        exporter = None
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore
                    OTLPSpanExporter,
                )

                exporter = OTLPSpanExporter(endpoint=endpoint)
            except Exception as _exp_err:  # pragma: no cover - optional dep
                logger.warning("OTLP exporter init failed (fallback no-op): %s", _exp_err)
        if exporter is None:
            # No collector configured: use a silent no-op exporter so spans
            # are created but discarded (fail-open, zero network cost).
            try:
                from opentelemetry.sdk.trace.export import SimpleSpanProcessor
                from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # type: ignore
                    InMemorySpanExporter,
                )

                exporter = InMemorySpanExporter()
                _provider = TracerProvider(resource=Resource.create({"service.name": "vermes"}))
                _provider.add_span_processor(SimpleSpanProcessor(exporter))
                trace.set_tracer_provider(_provider)
                _TRACER = trace.get_tracer("vermes")
                return _TRACER
            except Exception:
                exporter = None

        if exporter is None:
            # Absolute fallback: console exporter (dev visibility) but still
            # fail-open if even that fails.
            exporter = ConsoleSpanExporter()

        _provider = TracerProvider(resource=Resource.create({"service.name": "vermes"}))
        _provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(_provider)
        _TRACER = trace.get_tracer("vermes")
    except Exception as _err:  # pragma: no cover - defensive
        logger.warning("otel tracer init failed (no-op fallback): %s", _err)
        _TRACER = _NoopTracer()
    return _TRACER


class _NoopSpan:
    """A span that records nothing but satisfies the contextmanager protocol."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record_exception(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _NoopTracer:
    """No-op tracer used when otel is unavailable or setup fails."""

    @contextmanager
    def start_as_current_span(self, name: str, *args: Any, **kwargs: Any) -> Iterator[_NoopSpan]:
        yield _NoopSpan()


@property
def tracer() -> Any:
    """The active tracer (real or no-op). Access lazily so import is cheap."""
    return _get_tracer()


@contextmanager
def span(name: str, attributes: Optional[Dict[str, Any]] = None) -> Iterator[Any]:
    """Contextmanager helper: ``with span('turn', {...}): ...``.

    Always safe — yields a real span when otel is wired, a no-op otherwise.
    """
    _t = _get_tracer()
    try:
        ctx = _t.start_as_current_span(name)
    except Exception:
        yield _NoopSpan()
        return
    with ctx as sp:
        if attributes and hasattr(sp, "set_attribute"):
            for _k, _v in attributes.items():
                try:
                    sp.set_attribute(_k, _v)
                except Exception:
                    pass
        yield sp
