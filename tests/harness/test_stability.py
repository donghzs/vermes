"""Tests for harness.stability (B2 — opt-in best/worst-of-N probe)."""

from __future__ import annotations

import pytest

from harness.stability import StabilityReport, probe_stability, stability_probe


@pytest.mark.asyncio
async def test_constant_score_is_stable():
    report = await probe_stability(lambda: 0.9, n=3)
    assert isinstance(report, StabilityReport)
    assert report.n == 3
    assert report.best_score == report.worst_score == 0.9
    assert report.delta == 0.0
    assert report.stable is True
    assert report.errors == []


@pytest.mark.asyncio
async def test_variance_detected_as_unstable():
    flips = [1.0, 0.2, 1.0, 0.2]

    def flaky():
        return flips.pop(0) if flips else 0.2

    report = await probe_stability(flaky, n=4)
    assert report.best_score == 1.0
    assert report.worst_score == 0.2
    assert report.delta == 0.8
    assert report.stable is False  # delta >> DEFAULT_STABLE_DELTA


@pytest.mark.asyncio
async def test_score_fn_maps_result():
    def tool():
        return {"score": 0.75, "text": "ok"}

    report = await probe_stability(tool, n=2, score_fn=lambda r: r["score"])
    assert report.mean_score == 0.75
    assert report.best_result == {"score": 0.75, "text": "ok"}


@pytest.mark.asyncio
async def test_tuple_result_shape():
    # result is (score, value)
    report = await probe_stability(lambda: (0.5, "v"), n=2)
    assert report.best_score == 0.5
    assert report.best_result == "v"


@pytest.mark.asyncio
async def test_raised_runs_are_captured_not_fatal():
    def boom():
        raise RuntimeError("nope")

    report = await probe_stability(boom, n=3)
    assert report.n == 3
    assert report.scores == []
    assert len(report.errors) == 3


@pytest.mark.asyncio
async def test_async_fn_supported():
    async def a_tool():
        return 0.6

    report = await probe_stability(a_tool, n=2)
    assert report.best_score == 0.6


@pytest.mark.asyncio
async def test_stability_probe_decorator_returns_report_and_best():
    @stability_probe(n=3, score_fn=lambda r: r)
    async def my_tool():
        return 0.8

    report, best = await my_tool()
    assert isinstance(report, StabilityReport)
    assert best == 0.8
    assert report.best_score == 0.8
