"""Contract tests for read_file truncation semantics.

Regression guards for a silent data-loss bug (2026-09-22): a single-line
file (or any file whose lines are long but whose line count fits the read
window) was clipped to ``max_line_length`` inside ``_add_line_numbers``,
yet reported ``truncated=False`` and ``total_lines=0`` — silently dropping
up to ~96% of the content while lying about having read it all.

Root cause was two fold:
1. ``truncated`` was computed ONLY from ``wc -l`` (line-range truncation),
   with no awareness of in-line truncation done by ``_add_line_numbers``.
2. ``wc -l`` counts newline characters, so a file without a trailing
   newline under-counted by one (single-line file -> 0).

Fix: count lines with ``awk 'END{print NR+0}'``, and fold in-line
truncation into the ``truncated`` flag via ``_has_oversized_lines``.
"""

import os
import subprocess
import tempfile

from tools.file_operations import ShellFileOperations


class _LocalEnv:
    """Minimal terminal_env shim backed by a local shell."""

    def __init__(self):
        self.cwd = os.getcwd()

    def execute(self, command, cwd=None):
        p = subprocess.run(
            command, shell=True, cwd=cwd or self.cwd,
            capture_output=True, text=True,
        )
        return {"output": p.stdout, "returncode": p.returncode}


def _ops():
    return ShellFileOperations(_LocalEnv())


def _write(path, content: str):
    with open(path, "w") as f:
        f.write(content)


def test_single_line_long_file_reports_truncated():
    """A 50KB single-line file must NOT silently drop content + lie."""
    fo = _ops()
    tmp = tempfile.mktemp(suffix=".txt")
    try:
        _write(tmp, "X" * 50000)  # no trailing newline
        r = fo.read_file(tmp)
        # Content is in-line clipped to max_line_length — that is expected.
        assert r.total_lines == 1, f"total_lines={r.total_lines}, want 1"
        assert r.truncated is True, "truncated must be True for oversized line"
        assert r.hint, "must provide a hint on how to read the rest"
        assert "truncated" in r.hint.lower() or "read_file_raw" in r.hint
    finally:
        os.remove(tmp)


def test_no_trailing_newline_counts_all_lines():
    """wc -l under-counts; awk NR must report the real line count."""
    fo = _ops()
    tmp = tempfile.mktemp(suffix=".txt")
    try:
        _write(tmp, "a\nb\nc")  # 3 lines, no trailing newline
        r = fo.read_file(tmp)
        assert r.total_lines == 3, f"total_lines={r.total_lines}, want 3"
        assert r.truncated is False
    finally:
        os.remove(tmp)


def test_empty_file_total_lines_zero():
    fo = _ops()
    tmp = tempfile.mktemp(suffix=".txt")
    try:
        _write(tmp, "")
        r = fo.read_file(tmp)
        assert r.total_lines == 0
        assert r.truncated is False
    finally:
        os.remove(tmp)


def test_multiline_truncation_unaffected():
    """Multi-line line-range truncation behavior must not regress."""
    fo = _ops()
    tmp = tempfile.mktemp(suffix=".txt")
    try:
        _write(tmp, "".join(f"line {i}\n" for i in range(625)))
        r = fo.read_file(tmp)
        assert r.total_lines == 625
        assert r.truncated is True
        assert "offset=501" in r.hint
    finally:
        os.remove(tmp)


def test_mixed_long_lines_flagged_even_when_fits_window():
    """Long lines within the line-count window must still set truncated."""
    fo = _ops()
    tmp = tempfile.mktemp(suffix=".txt")
    try:
        # 3 lines, each 3000 chars (over max_line_length=2000), fits in the
        # default 500-line read window → line-range truncation is False, but
        # in-line truncation must still be detected.
        _write(tmp, ("Y" * 3000 + "\n") * 3)
        r = fo.read_file(tmp)
        assert r.total_lines == 3
        assert r.truncated is True, "in-line truncation must set truncated"
    finally:
        os.remove(tmp)
