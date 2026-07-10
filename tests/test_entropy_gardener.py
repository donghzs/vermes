"""Tests for agent.entropy_gardener — pure unit tests, no external deps.

Run: python -m pytest tests/test_entropy_gardener.py -v
"""

import os
import tempfile
import textwrap
import pytest

from agent.entropy_gardener import (
    DebtReport,
    FileMetric,
    FunctionMetric,
    _scan_python_file,
    scan_codebase,
    scan_and_report,
    compare_reports,
    LARGE_FUNCTION_THRESHOLD,
    LARGE_FILE_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_project():
    """Create a minimal project structure for scanning."""
    with tempfile.TemporaryDirectory() as root:
        # Create agent/ directory
        agent_dir = os.path.join(root, "agent")
        os.makedirs(agent_dir)

        # Clean file
        with open(os.path.join(agent_dir, "clean.py"), "w") as f:
            f.write(textwrap.dedent("""\
                import logging

                logger = logging.getLogger(__name__)

                def add(a, b):
                    return a + b

                def main():
                    result = add(1, 2)
                    logger.info("result: %s", result)
                    return result
            """))

        # Dirty file with print, bare except, TODO
        with open(os.path.join(agent_dir, "dirty.py"), "w") as f:
            f.write(textwrap.dedent("""\
                # TODO: fix this later
                def messy(x):
                    print("debug")
                    try:
                        return x
                    except:
                        return None

                def big_function():
            """))
            # Write a function > 500 lines (pad with pass)
            for i in range(520):
                f.write(f"    var_{i} = {i}\n")

        # File with noqa and type: ignore
        with open(os.path.join(agent_dir, "linted.py"), "w") as f:
            f.write(textwrap.dedent("""\
                def foo():  # type: ignore
                    x = 1  # noqa
                    return x
            """))

        # Create __pycache__ (should be excluded)
        pycache = os.path.join(agent_dir, "__pycache__")
        os.makedirs(pycache)
        with open(os.path.join(pycache, "should_not_scan.py"), "w") as f:
            f.write("print('hidden')\n")

        # Create tools/ directory
        tools_dir = os.path.join(root, "tools")
        os.makedirs(tools_dir)
        with open(os.path.join(tools_dir, "helper.py"), "w") as f:
            f.write("def help():\n    return 'help'\n")

        yield root


# ---------------------------------------------------------------------------
# FunctionMetric tests
# ---------------------------------------------------------------------------


class TestFunctionMetric:
    def test_is_large(self):
        f = FunctionMetric("a.py", "big", 1, 600, 600)
        assert f.is_large is True

    def test_is_not_large(self):
        f = FunctionMetric("a.py", "small", 1, 50, 50)
        assert f.is_large is False

    def test_boundary(self):
        f = FunctionMetric("a.py", "boundary", 1, LARGE_FUNCTION_THRESHOLD + 1, LARGE_FUNCTION_THRESHOLD + 1)
        assert f.is_large is True

    def test_exact_threshold(self):
        f = FunctionMetric("a.py", "exact", 1, LARGE_FUNCTION_THRESHOLD, LARGE_FUNCTION_THRESHOLD)
        assert f.is_large is False  # > threshold, not >=


# ---------------------------------------------------------------------------
# FileMetric tests
# ---------------------------------------------------------------------------


class TestFileMetric:
    def test_to_dict(self):
        m = FileMetric(
            filepath="test.py",
            line_count=100,
            print_count=2,
            bare_except_count=1,
            except_pass_count=0,
            todo_fixme_count=3,
            noqa_count=1,
            type_ignore_count=1,
            large_functions=(),
        )
        d = m.to_dict()
        assert d["filepath"] == "test.py"
        assert d["line_count"] == 100
        assert d["print_count"] == 2
        assert d["large_functions"] == []

    def test_is_large_file(self):
        m = FileMetric("big.py", LARGE_FILE_THRESHOLD + 1, 0, 0, 0, 0, 0, 0, ())
        assert m.is_large is True


# ---------------------------------------------------------------------------
# _scan_python_file tests
# ---------------------------------------------------------------------------


class TestScanPythonFile:
    def test_clean_file(self, tmp_project):
        filepath = os.path.join(tmp_project, "agent", "clean.py")
        m = _scan_python_file(filepath)
        assert m is not None
        assert m.line_count > 0
        assert m.print_count == 0
        assert m.bare_except_count == 0
        assert m.todo_fixme_count == 0
        assert len(m.large_functions) == 0

    def test_dirty_file(self, tmp_project):
        filepath = os.path.join(tmp_project, "agent", "dirty.py")
        m = _scan_python_file(filepath)
        assert m is not None
        assert m.print_count >= 1
        assert m.bare_except_count >= 1
        assert m.todo_fixme_count >= 1
        assert len(m.large_functions) >= 1

    def test_linted_file(self, tmp_project):
        filepath = os.path.join(tmp_project, "agent", "linted.py")
        m = _scan_python_file(filepath)
        assert m is not None
        assert m.noqa_count >= 1
        assert m.type_ignore_count >= 1

    def test_nonexistent_file(self):
        assert _scan_python_file("/nonexistent/file.py") is None

    def test_syntax_error_file(self, tmp_project):
        """File with syntax error should still return pattern counts."""
        filepath = os.path.join(tmp_project, "agent", "broken.py")
        with open(filepath, "w") as f:
            f.write("def broken(:\n    print('oops')\n")
        m = _scan_python_file(filepath)
        assert m is not None
        assert m.print_count >= 1
        # large_functions should be empty (AST parse failed)
        assert len(m.large_functions) == 0


# ---------------------------------------------------------------------------
# scan_codebase tests
# ---------------------------------------------------------------------------


class TestScanCodebase:
    def test_scans_tmp_project(self, tmp_project):
        report = scan_codebase(tmp_project)
        assert report.total_files >= 3  # clean.py, dirty.py, linted.py, helper.py
        assert report.total_lines > 0
        assert report.total_print >= 1
        assert report.total_bare_except >= 1
        assert report.total_todo_fixme >= 1
        assert len(report.large_functions) >= 1

    def test_excludes_pycache(self, tmp_project):
        report = scan_codebase(tmp_project)
        # The __pycache__/should_not_scan.py should NOT be in file_metrics
        for fm in report.file_metrics:
            assert "__pycache__" not in fm.filepath

    def test_scans_tools_dir(self, tmp_project):
        report = scan_codebase(tmp_project)
        tool_files = [fm for fm in report.file_metrics if "tools" in fm.filepath]
        assert len(tool_files) >= 1

    def test_scan_duration_positive(self, tmp_project):
        report = scan_codebase(tmp_project)
        assert report.scan_duration_ms >= 0

    def test_large_function_detected(self, tmp_project):
        report = scan_codebase(tmp_project)
        large = report.top_large_functions(5)
        assert len(large) >= 1
        assert any(f.name == "big_function" for f in large)

    def test_summary_string(self, tmp_project):
        report = scan_codebase(tmp_project)
        s = report.summary()
        assert "Scanned" in s
        assert "files" in s
        assert "lines" in s

    def test_to_json(self, tmp_project):
        report = scan_codebase(tmp_project)
        j = report.to_json()
        import json
        d = json.loads(j)
        assert "total_files" in d
        assert "top_large_functions" in d

    def test_top_large_files(self, tmp_project):
        report = scan_codebase(tmp_project)
        top = report.top_large_files(3)
        assert len(top) >= 1


# ---------------------------------------------------------------------------
# scan_and_report tests
# ---------------------------------------------------------------------------


class TestScanAndReport:
    def test_returns_report(self, tmp_project):
        report = scan_and_report(tmp_project)
        assert isinstance(report, DebtReport)
        assert report.total_files > 0


# ---------------------------------------------------------------------------
# compare_reports tests
# ---------------------------------------------------------------------------


class TestCompareReports:
    def test_comparison(self, tmp_project):
        old = scan_codebase(tmp_project)

        # Add a file with more print statements
        with open(os.path.join(tmp_project, "agent", "more_prints.py"), "w") as f:
            f.write("print('a')\nprint('b')\nprint('c')\n")

        new = scan_codebase(tmp_project)
        delta = compare_reports(old, new)

        assert delta["delta_files"] == 1
        assert delta["delta_print"] >= 3
        assert delta["trend"] == "degrading"

    def test_improving_trend(self, tmp_project):
        old = scan_codebase(tmp_project)

        # Remove the dirty file
        os.remove(os.path.join(tmp_project, "agent", "dirty.py"))

        new = scan_codebase(tmp_project)
        delta = compare_reports(old, new)

        assert delta["delta_files"] < 0
        assert delta["delta_print"] < 0
        assert delta["trend"] == "improving"

    def test_stable_trend(self, tmp_project):
        report = scan_codebase(tmp_project)
        delta = compare_reports(report, report)
        assert delta["delta_files"] == 0
        assert delta["delta_lines"] == 0
        assert delta["trend"] == "improving"  # equal counts → not degrading
