"""Entropy gardener — periodic code quality scanner.

Scans the codebase for quality metrics (large functions, print() calls,
broad except, TODO/FIXME) and generates a debt report.  Designed to be
called from a cron job or heartbeat; side-effect free unless the caller
acts on the report.

Design principles
-----------------
1. **纯本地扫描** — No LLM, no network, no agent fork.  Just AST + grep.
2. **可 cron** — Designed to run weekly (e.g. Sunday 03:00).
3. **可增量** — Can compare against last scan to show trends.
4. **零依赖** — Stdlib only (ast, os, re, json, logging).

Integration (NOT YET WIRED)
---------------------------
Called from a cron job or heartbeat::

    from agent.entropy_gardener import scan_and_report
    report = scan_and_report()
    # report.to_dict() → save to file or send via message

This file does NOT import or modify any existing module.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("vermes.entropy_gardener")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Directories to scan (relative to project root)
SCAN_DIRS = ["agent", "hermes_cli", "tools"]

# Directories to exclude
EXCLUDE_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", "build", "dist"}

# File extensions to scan
SCAN_EXTENSIONS = {".py"}

# Thresholds
LARGE_FUNCTION_THRESHOLD = 500  # lines
LARGE_FILE_THRESHOLD = 2000  # lines

# Patterns to count
PRINT_PATTERN = re.compile(r"^\s*print\s*\(", re.MULTILINE)
BARE_EXCEPT_PATTERN = re.compile(r"^\s*except\s*:", re.MULTILINE)
EXCEPT_PASS_PATTERN = re.compile(
    r"^\s*except\s+Exception\s*:\s*$\s*pass\s*$", re.MULTILINE
)
TODO_FIXME_PATTERN = re.compile(
    r"#\s*(TODO|FIXME|HACK|XXX|WORKAROUND)\b", re.IGNORECASE
)
NOQA_PATTERN = re.compile(r"#\s*noqa\b", re.IGNORECASE)
TYPE_IGNORE_PATTERN = re.compile(r"#\s*type:\s*ignore", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FunctionMetric:
    """Metric for a single function."""

    filepath: str
    name: str
    start_line: int
    end_line: int
    length: int

    @property
    def is_large(self) -> bool:
        return self.length > LARGE_FUNCTION_THRESHOLD


@dataclass(frozen=True)
class FileMetric:
    """Metric for a single file."""

    filepath: str
    line_count: int
    print_count: int
    bare_except_count: int
    except_pass_count: int
    todo_fixme_count: int
    noqa_count: int
    type_ignore_count: int
    large_functions: tuple[FunctionMetric, ...] = field(default_factory=tuple)

    @property
    def is_large(self) -> bool:
        return self.line_count > LARGE_FILE_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        return {
            "filepath": self.filepath,
            "line_count": self.line_count,
            "print_count": self.print_count,
            "bare_except_count": self.bare_except_count,
            "except_pass_count": self.except_pass_count,
            "todo_fixme_count": self.todo_fixme_count,
            "noqa_count": self.noqa_count,
            "type_ignore_count": self.type_ignore_count,
            "large_functions": [
                {
                    "name": f.name,
                    "start": f.start_line,
                    "end": f.end_line,
                    "length": f.length,
                }
                for f in self.large_functions
            ],
        }


@dataclass(frozen=True)
class DebtReport:
    """Aggregated debt report from a scan."""

    scan_time: float  # unix timestamp
    scan_duration_ms: int
    total_files: int
    total_lines: int
    total_print: int
    total_bare_except: int
    total_except_pass: int
    total_todo_fixme: int
    total_noqa: int
    total_type_ignore: int
    large_functions: tuple[FunctionMetric, ...]
    large_files: tuple[FileMetric, ...]
    file_metrics: tuple[FileMetric, ...]

    def summary(self) -> str:
        """Human-readable one-line summary."""
        return (
            f"Scanned {self.total_files} files ({self.total_lines} lines) in "
            f"{self.scan_duration_ms}ms — "
            f"{len(self.large_functions)} large functions, "
            f"{self.total_print} print(), "
            f"{self.total_bare_except} bare except, "
            f"{self.total_todo_fixme} TODO/FIXME"
        )

    def top_large_functions(self, n: int = 10) -> list[FunctionMetric]:
        """Return the N largest functions."""
        return sorted(self.large_functions, key=lambda f: -f.length)[:n]

    def top_large_files(self, n: int = 10) -> list[FileMetric]:
        """Return the N largest files."""
        return sorted(self.file_metrics, key=lambda f: -f.line_count)[:n]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_time": self.scan_time,
            "scan_duration_ms": self.scan_duration_ms,
            "total_files": self.total_files,
            "total_lines": self.total_lines,
            "total_print": self.total_print,
            "total_bare_except": self.total_bare_except,
            "total_except_pass": self.total_except_pass,
            "total_todo_fixme": self.total_todo_fixme,
            "total_noqa": self.total_noqa,
            "total_type_ignore": self.total_type_ignore,
            "large_functions_count": len(self.large_functions),
            "large_files_count": len(self.large_files),
            "top_large_functions": [
                {
                    "filepath": f.filepath,
                    "name": f.name,
                    "start": f.start_line,
                    "end": f.end_line,
                    "length": f.length,
                }
                for f in self.top_large_functions()
            ],
            "top_large_files": [
                {
                    "filepath": f.filepath,
                    "line_count": f.line_count,
                }
                for f in self.top_large_files()
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def _should_exclude(dirpath: str) -> bool:
    """Check if a directory should be excluded from scanning."""
    parts = Path(dirpath).parts
    return any(part in EXCLUDE_DIRS for part in parts)


def _scan_python_file(filepath: str) -> FileMetric | None:
    """Scan a single Python file and return its metrics.

    Returns None if the file cannot be parsed.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return None

    line_count = source.count("\n") + 1

    # Pattern counts
    print_count = len(PRINT_PATTERN.findall(source))
    bare_except_count = len(BARE_EXCEPT_PATTERN.findall(source))
    except_pass_count = len(EXCEPT_PASS_PATTERN.findall(source))
    todo_fixme_count = len(TODO_FIXME_PATTERN.findall(source))
    noqa_count = len(NOQA_PATTERN.findall(source))
    type_ignore_count = len(TYPE_IGNORE_PATTERN.findall(source))

    # AST analysis for function sizes
    large_functions: list[FunctionMetric] = []
    try:
        tree = ast.parse(source, filename=filepath)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = node.lineno
                end = getattr(node, "end_lineno", start)
                length = end - start + 1
                if length > LARGE_FUNCTION_THRESHOLD:
                    large_functions.append(
                        FunctionMetric(
                            filepath=filepath,
                            name=node.name,
                            start_line=start,
                            end_line=end,
                            length=length,
                        )
                    )
    except SyntaxError:
        # File has syntax errors — skip AST analysis but keep pattern counts
        pass

    return FileMetric(
        filepath=filepath,
        line_count=line_count,
        print_count=print_count,
        bare_except_count=bare_except_count,
        except_pass_count=except_pass_count,
        todo_fixme_count=todo_fixme_count,
        noqa_count=noqa_count,
        type_ignore_count=type_ignore_count,
        large_functions=tuple(large_functions),
    )


def scan_codebase(root_dir: str = ".") -> DebtReport:
    """Scan the codebase and return a debt report.

    Parameters
    ----------
    root_dir : str
        Project root directory.  Defaults to current directory.

    Returns
    -------
    DebtReport
        Aggregated metrics across all scanned files.
    """
    start_time = time.time()

    all_file_metrics: list[FileMetric] = []
    all_large_functions: list[FunctionMetric] = []

    for scan_dir in SCAN_DIRS:
        full_dir = os.path.join(root_dir, scan_dir)
        if not os.path.isdir(full_dir):
            continue

        for dirpath, dirnames, filenames in os.walk(full_dir):
            # Filter excluded dirs in-place
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

            for filename in filenames:
                if not any(filename.endswith(ext) for ext in SCAN_EXTENSIONS):
                    continue
                filepath = os.path.join(dirpath, filename)

                metric = _scan_python_file(filepath)
                if metric is None:
                    continue

                all_file_metrics.append(metric)
                all_large_functions.extend(metric.large_functions)

    scan_duration_ms = int((time.time() - start_time) * 1000)

    total_lines = sum(m.line_count for m in all_file_metrics)
    total_print = sum(m.print_count for m in all_file_metrics)
    total_bare_except = sum(m.bare_except_count for m in all_file_metrics)
    total_except_pass = sum(m.except_pass_count for m in all_file_metrics)
    total_todo_fixme = sum(m.todo_fixme_count for m in all_file_metrics)
    total_noqa = sum(m.noqa_count for m in all_file_metrics)
    total_type_ignore = sum(m.type_ignore_count for m in all_file_metrics)

    large_files = tuple(m for m in all_file_metrics if m.is_large)

    return DebtReport(
        scan_time=time.time(),
        scan_duration_ms=scan_duration_ms,
        total_files=len(all_file_metrics),
        total_lines=total_lines,
        total_print=total_print,
        total_bare_except=total_bare_except,
        total_except_pass=total_except_pass,
        total_todo_fixme=total_todo_fixme,
        total_noqa=total_noqa,
        total_type_ignore=total_type_ignore,
        large_functions=tuple(all_large_functions),
        large_files=large_files,
        file_metrics=tuple(all_file_metrics),
    )


def scan_and_report(root_dir: str = ".") -> DebtReport:
    """Scan the codebase, log a summary, and return the report.

    Convenience function for cron/heartbeat integration.
    """
    report = scan_codebase(root_dir)
    logger.info("entropy scan: %s", report.summary())

    # Log top 5 large functions
    for func in report.top_large_functions(5):
        logger.info(
            "  large function: %s:%d %s() (%d lines)",
            func.filepath,
            func.start_line,
            func.name,
            func.length,
        )

    return report


def compare_reports(old: DebtReport, new: DebtReport) -> dict[str, Any]:
    """Compare two reports and return the delta.

    Useful for tracking trends over time.
    """
    return {
        "delta_files": new.total_files - old.total_files,
        "delta_lines": new.total_lines - old.total_lines,
        "delta_print": new.total_print - old.total_print,
        "delta_bare_except": new.total_bare_except - old.total_bare_except,
        "delta_except_pass": new.total_except_pass - old.total_except_pass,
        "delta_todo_fixme": new.total_todo_fixme - old.total_todo_fixme,
        "delta_large_functions": len(new.large_functions) - len(old.large_functions),
        "trend": "improving" if new.total_print + len(new.large_functions) <= old.total_print + len(old.large_functions) else "degrading",
    }
