"""Tests for scripts/quality_gate.py — threshold enforcement.

Run: python -m pytest tests/test_quality_gate.py -v
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import pytest

from agent.entropy_gardener import DebtReport, FileMetric, FunctionMetric
from scripts.quality_gate import check_thresholds, MAX_LARGE_FUNCTIONS, MAX_PRINT_CALLS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_report():
    """A report that passes all thresholds."""
    return DebtReport(
        scan_time=0,
        scan_duration_ms=100,
        total_files=10,
        total_lines=1000,
        total_print=0,
        total_bare_except=0,
        total_except_pass=0,
        total_todo_fixme=0,
        total_noqa=0,
        total_type_ignore=0,
        large_functions=(),
        large_files=(),
        file_metrics=(),
    )


@pytest.fixture
def dirty_report():
    """A report that violates all thresholds."""
    large_funcs = tuple(
        FunctionMetric(filepath=f"f{i}.py", name=f"func_{i}", start_line=1, end_line=600, length=600)
        for i in range(20)
    )
    return DebtReport(
        scan_time=0,
        scan_duration_ms=100,
        total_files=100,
        total_lines=10000,
        total_print=50,
        total_bare_except=2,
        total_except_pass=700,
        total_todo_fixme=20,
        total_noqa=0,
        total_type_ignore=0,
        large_functions=large_funcs,
        large_files=(),
        file_metrics=(),
    )


# ---------------------------------------------------------------------------
# check_thresholds tests
# ---------------------------------------------------------------------------


class TestCheckThresholds:
    def test_clean_report_no_violations(self, clean_report):
        violations = check_thresholds(clean_report)
        assert len(violations) == 0

    def test_dirty_report_all_violations(self, dirty_report):
        violations = check_thresholds(dirty_report)
        assert len(violations) == 5  # all 5 thresholds violated

    def test_large_functions_violation(self, clean_report):
        funcs = tuple(
            FunctionMetric(filepath="f.py", name="big", start_line=1, end_line=600, length=600)
            for _ in range(MAX_LARGE_FUNCTIONS + 1)
        )
        report = DebtReport(
            scan_time=0, scan_duration_ms=0, total_files=1, total_lines=1,
            total_print=0, total_bare_except=0, total_except_pass=0,
            total_todo_fixme=0, total_noqa=0, total_type_ignore=0,
            large_functions=funcs, large_files=(), file_metrics=(),
        )
        violations = check_thresholds(report)
        assert any(v["metric"] == "large_functions" for v in violations)

    def test_print_violation(self, clean_report):
        report = DebtReport(
            scan_time=0, scan_duration_ms=0, total_files=1, total_lines=1,
            total_print=MAX_PRINT_CALLS + 1,
            total_bare_except=0, total_except_pass=0,
            total_todo_fixme=0, total_noqa=0, total_type_ignore=0,
            large_functions=(), large_files=(), file_metrics=(),
        )
        violations = check_thresholds(report)
        assert any(v["metric"] == "print_calls" for v in violations)

    def test_bare_except_violation(self, clean_report):
        report = DebtReport(
            scan_time=0, scan_duration_ms=0, total_files=1, total_lines=1,
            total_print=0,
            total_bare_except=1,
            total_except_pass=0,
            total_todo_fixme=0, total_noqa=0, total_type_ignore=0,
            large_functions=(), large_files=(), file_metrics=(),
        )
        violations = check_thresholds(report)
        assert any(v["metric"] == "bare_except" for v in violations)

    def test_violation_message_format(self, dirty_report):
        violations = check_thresholds(dirty_report)
        for v in violations:
            assert "metric" in v
            assert "value" in v
            assert "threshold" in v
            assert "message" in v
            assert v["value"] > v["threshold"]


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_clean_project_exit_zero(self, tmp_project):
        """Run quality_gate.py on a clean project — should exit 0."""
        result = subprocess.run(
            [sys.executable, "scripts/quality_gate.py", "--root", tmp_project, "--json"],
            capture_output=True, text=True, cwd=project_root_str(),
        )
        data = json.loads(result.stdout)
        assert data["passed"] is True
        assert len(data["violations"]) == 0

    def test_dirty_project_exit_one(self, tmp_project):
        """Run quality_gate.py on a project with violations — should exit 1."""
        # Add many print() calls
        with open(os.path.join(tmp_project, "agent", "dirty.py"), "w") as f:
            for i in range(30):
                f.write(f"print('msg{i}')\n")

        result = subprocess.run(
            [sys.executable, "scripts/quality_gate.py", "--root", tmp_project, "--json"],
            capture_output=True, text=True, cwd=project_root_str(),
        )
        data = json.loads(result.stdout)
        assert data["passed"] is False
        assert len(data["violations"]) > 0

    def test_text_output_format(self, tmp_project):
        """Test human-readable output (not JSON)."""
        result = subprocess.run(
            [sys.executable, "scripts/quality_gate.py", "--root", tmp_project],
            capture_output=True, text=True, cwd=project_root_str(),
        )
        assert "Scanned" in result.stdout
        assert "files" in result.stdout


# ---------------------------------------------------------------------------
# Fixtures for CLI tests
# ---------------------------------------------------------------------------


def project_root_str():
    """Get the project root path."""
    return str(__import__("pathlib").Path(__file__).resolve().parent.parent)


@pytest.fixture
def tmp_project():
    """Create a minimal clean project for CLI tests."""
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "agent"))
        with open(os.path.join(root, "agent", "clean.py"), "w") as f:
            f.write("import logging\nlogger = logging.getLogger(__name__)\n\ndef add(a, b):\n    return a + b\n")
        os.makedirs(os.path.join(root, "tools"))
        with open(os.path.join(root, "tools", "helper.py"), "w") as f:
            f.write("def help():\n    return 'help'\n")
        yield root
