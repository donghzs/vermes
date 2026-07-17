"""Tests for harness.constraints (B3 — generic constraint base + runner)."""

from __future__ import annotations

import pytest

from harness.constraints import (
    Constraint,
    ConstraintReport,
    ConstraintResult,
    run_constraints,
)


class MinLengthConstraint(Constraint):
    """Demo constraint: proves any domain can reuse the base."""

    name = "min_length"
    severity = "error"

    def __init__(self, min_len: int = 3) -> None:
        self.min_len = min_len

    async def check(self, ctx) -> ConstraintResult:
        text = ctx if isinstance(ctx, str) else str(ctx)
        ok = len(text) >= self.min_len
        return ConstraintResult(
            name=self.name,
            passed=ok,
            severity=self.severity,
            detail=f"len={len(text)} (need >={self.min_len})",
            suggestion="提供更长的内容" if not ok else "",
        )


class SoftWarningConstraint(Constraint):
    name = "has_prefix"
    severity = "warning"

    async def check(self, ctx) -> ConstraintResult:
        ok = isinstance(ctx, str) and ctx.startswith("ok")
        return ConstraintResult(
            name=self.name, passed=ok, severity=self.severity,
            detail="" if ok else "缺少 ok 前缀",
        )


class RaisingConstraint(Constraint):
    name = "boom"
    severity = "error"

    async def check(self, ctx) -> ConstraintResult:
        raise RuntimeError("constraint impl broken")


@pytest.mark.asyncio
async def test_run_constraints_all_pass():
    report = await run_constraints([MinLengthConstraint(3)], "hello")
    assert report.passed is True
    assert report.errors == []
    assert report.results[0].passed is True


@pytest.mark.asyncio
async def test_run_constraints_failure_blocks():
    report = await run_constraints([MinLengthConstraint(5)], "hi")
    assert report.passed is False
    assert len(report.errors) == 1
    assert report.errors[0].name == "min_length"


@pytest.mark.asyncio
async def test_warning_does_not_block_pass():
    # error passes, warning fails -> overall still passes (warning non-blocking)
    report = await run_constraints(
        [MinLengthConstraint(1), SoftWarningConstraint()], "x"
    )
    assert report.passed is True
    assert len(report.warnings) == 1


@pytest.mark.asyncio
async def test_short_circuit_stops_at_first_error():
    calls = []

    class Counting(Constraint):
        name = "c"

        async def check(self, ctx):
            calls.append(1)
            return ConstraintResult(name="c", passed=False, severity="error")

    class NeverReached(Constraint):
        name = "never"

        async def check(self, ctx):
            calls.append(1)
            return ConstraintResult(name="never", passed=False, severity="error")

    report = await run_constraints(
        [Counting(), NeverReached()], "x", short_circuit=True
    )
    assert len(calls) == 1
    assert report.results[0].name == "c"


@pytest.mark.asyncio
async def test_constraint_raising_is_recorded_not_fatal():
    report = await run_constraints([RaisingConstraint()], "x")
    assert len(report.errors) == 1
    assert report.errors[0].name == "boom"
    assert "constraint error" in report.errors[0].detail


def test_report_payload_shape():
    rep = ConstraintReport(
        results=[ConstraintResult(name="a", passed=True)]
    )
    payload = rep.to_payload()
    assert payload["passed"] is True
    assert "all" in payload and "errors" in payload and "warnings" in payload
