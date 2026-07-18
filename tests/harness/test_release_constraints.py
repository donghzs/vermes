"""Tests for harness.release_constraints — pre-release constraint suite."""

import asyncio

import pytest

from harness.constraints import Constraint, ConstraintResult, run_constraints
from harness.release_constraints import (
    build_release_constraints,
    HarnessIntegrityConstraint,
    ToolRegistrationConstraint,
    ScholarForgeToolsConstraint,
    GatewayMixinsConstraint,
    RecoverableToolCoverageConstraint,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestHarnessIntegrityConstraint:
    @pytest.mark.asyncio
    async def test_passes_when_all_modules_importable(self):
        c = HarnessIntegrityConstraint()
        report = await run_constraints([c])
        assert len(report.results) == 1
        assert report.results[0].passed, report.results[0].detail

    @pytest.mark.asyncio
    async def test_name_and_severity(self):
        c = HarnessIntegrityConstraint()
        assert c.name == "harness_integrity"
        assert c.severity == "error"


class TestToolRegistrationConstraint:
    @pytest.mark.asyncio
    async def test_detects_registered_tools(self):
        c = ToolRegistrationConstraint()
        report = await run_constraints([c])
        r = report.results[0]
        assert r.passed, f"Expected pass, got: {r.detail}"
        assert "关键工具已注册" in r.detail

    @pytest.mark.asyncio
    async def test_missing_tool_reported(self):
        c = ToolRegistrationConstraint()
        c.REQUIRED_TOOLS = ["nonexistent_tool_xyz"]
        report = await run_constraints([c])
        r = report.results[0]
        assert not r.passed
        assert "nonexistent_tool_xyz" in r.detail


class TestScholarForgeToolsConstraint:
    @pytest.mark.asyncio
    async def test_scholarforge_schemas_present(self):
        c = ScholarForgeToolsConstraint()
        report = await run_constraints([c])
        r = report.results[0]
        assert r.passed, f"Expected pass, got: {r.detail}"
        assert "schema" in r.detail.lower()


class TestGatewayMixinsConstraint:
    @pytest.mark.asyncio
    async def test_all_mixins_in_mro(self):
        c = GatewayMixinsConstraint()
        report = await run_constraints([c])
        r = report.results[0]
        assert r.passed, f"Expected pass, got: {r.detail}"
        assert "mixin" in r.detail.lower()

    @pytest.mark.asyncio
    async def test_missing_mixin_detected(self):
        c = GatewayMixinsConstraint()
        c.EXPECTED_MIXINS = ["FakeMixin"]
        report = await run_constraints([c])
        r = report.results[0]
        assert not r.passed
        assert "FakeMixin" in r.detail


class TestRecoverableToolCoverageConstraint:
    @pytest.mark.asyncio
    async def test_coverage_passes(self):
        c = RecoverableToolCoverageConstraint()
        report = await run_constraints([c])
        r = report.results[0]
        assert r.passed, f"Expected pass, got: {r.detail}"
        assert "9/9" in r.detail

    @pytest.mark.asyncio
    async def test_warning_severity(self):
        c = RecoverableToolCoverageConstraint()
        assert c.severity == "warning"


class TestBuildReleaseConstraints:
    def test_returns_list_of_constraints(self):
        constraints = build_release_constraints()
        assert len(constraints) == 5
        names = [c.name for c in constraints]
        assert "harness_integrity" in names
        assert "tool_registration" in names
        assert "scholarforge_tools" in names
        assert "gateway_mixins" in names
        assert "recoverable_coverage" in names

    @pytest.mark.asyncio
    async def test_full_suite_passes(self):
        constraints = build_release_constraints()
        report = await run_constraints(constraints)
        assert report.passed, (
            f"Release constraints failed:\n"
            f"  errors: {[e.to_payload() for e in report.errors]}\n"
            f"  warnings: {[w.to_payload() for w in report.warnings]}"
        )

    @pytest.mark.asyncio
    async def test_short_circuit_on_error(self):
        constraints = build_release_constraints()
        report = await run_constraints(constraints, short_circuit=True)
        # All should pass, so short_circuit won't trigger
        assert report.passed

    @pytest.mark.asyncio
    async def test_constraint_exception_handled(self):
        class BrokenConstraint(Constraint):
            name = "broken"
            severity = "error"

            async def check(self, ctx=None):
                raise RuntimeError("boom")

        report = await run_constraints([BrokenConstraint()])
        assert not report.passed
        assert len(report.errors) == 1
        assert "boom" in report.errors[0].detail


class TestConstraintResultPayload:
    def test_to_payload_roundtrip(self):
        r = ConstraintResult(
            name="test",
            passed=True,
            severity="info",
            detail="all good",
            suggestion="none needed",
            meta={"count": 42},
        )
        payload = r.to_payload()
        assert payload["name"] == "test"
        assert payload["passed"] is True
        assert payload["severity"] == "info"
        assert payload["detail"] == "all good"
        assert payload["meta"]["count"] == 42
