"""Harness: lightweight stability probe (best/worst-of-N).

Opt-in only — never on the default request path. Measures multi-run
stability of a tool or subtask. The real signal is the *delta* between
best-of-N and worst-of-N: a behavior that scores 1.0 once and 0.2 another
time is not reliable, even if its mean looks fine. This operationalizes
harness insight #4: "multi-run stability > single-run".

Usage
-----
    report = await probe_stability(my_fn, n=3, score_fn=lambda r: r["score"])
    if not report.stable:
        # surface the variance to the agent / fall back

Or as a decorator (returns ``(StabilityReport, best_result)``):

    @stability_probe(n=3, score_fn=score)
    async def my_tool(...): ...
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Sequence

logger = logging.getLogger("harness.stability")

# Default stability threshold: a behavior is "stable" when best/worst delta
# is within this band across repeated runs.
DEFAULT_STABLE_DELTA = 0.05


@dataclass
class StabilityReport:
    n: int
    best_score: float
    worst_score: float
    mean_score: float
    std_score: float
    delta: float
    best_result: Any = None
    worst_result: Any = None
    scores: list[float] = field(default_factory=list)
    results: list[Any] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def stable(self) -> bool:
        return self.n >= 2 and self.delta <= DEFAULT_STABLE_DELTA


async def _invoke(fn: Callable, is_async: bool, args: Sequence, kwargs: dict) -> Any:
    if is_async:
        return await fn(*args, **kwargs)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


async def probe_stability(
    fn: Callable,
    *,
    n: int = 3,
    score_fn: Optional[Callable[[Any], float]] = None,
    args: Sequence = (),
    kwargs: Optional[dict] = None,
) -> StabilityReport:
    """Run ``fn`` ``n`` times and collect scores + results.

    Args:
        fn: callable (sync or async). For sync fns, each run is dispatched to
            the default executor so the probe stays async-friendly.
        n: number of runs (>= 1).
        score_fn: maps a result -> float in [0, 1]. If ``None``, the result is
            interpreted as either a float or a ``(score, value)`` 2-tuple.
        args / kwargs: positional / keyword args passed to every run.

    Runs that raise are recorded in ``errors`` and skipped (they don't count
    toward ``n`` scores, but ``n`` is preserved as the requested count).
    """
    kwargs = kwargs or {}
    is_async = inspect.iscoroutinefunction(fn)
    scores: list[float] = []
    results: list[Any] = []
    errors: list[str] = []

    for _ in range(max(1, n)):
        try:
            res = await _invoke(fn, is_async, args, kwargs)
        except Exception as exc:  # noqa: BLE001 — stability probe must tolerate failures
            errors.append(repr(exc))
            continue

        if score_fn is not None:
            score = float(score_fn(res))
            results.append(res)
        elif isinstance(res, tuple) and len(res) == 2:
            score, res = float(res[0]), res[1]
            results.append(res)
        else:
            score = float(res)
            results.append(res)
        scores.append(score)

    if not scores:
        return StabilityReport(
            n=max(1, n),
            best_score=0.0,
            worst_score=0.0,
            mean_score=0.0,
            std_score=0.0,
            delta=0.0,
            errors=errors,
        )

    best_i = max(range(len(scores)), key=lambda i: scores[i])
    worst_i = min(range(len(scores)), key=lambda i: scores[i])
    mean = statistics.fmean(scores)
    std = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    return StabilityReport(
        n=len(scores),
        best_score=scores[best_i],
        worst_score=scores[worst_i],
        mean_score=mean,
        std_score=std,
        delta=scores[best_i] - scores[worst_i],
        best_result=results[best_i],
        worst_result=results[worst_i],
        scores=scores,
        results=results,
        errors=errors,
    )


def stability_probe(
    *,
    n: int = 3,
    score_fn: Optional[Callable[[Any], float]] = None,
) -> Callable[[Callable], Callable]:
    """Decorator: probe stability of a tool/subtask (opt-in).

    The wrapped callable becomes async and returns
    ``(StabilityReport, best_result)``. Best used inside an evaluation /
    self-improvement harness, never on the hot path.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> tuple[StabilityReport, Any]:
            report = await probe_stability(
                fn, n=n, score_fn=score_fn, args=args, kwargs=kwargs
            )
            return report, report.best_result

        return wrapper

    return decorator
