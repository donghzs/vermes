"""Regression tests for search_files gitignore behavior (B1/B2) and the
empty-result API shape (A).

Root cause of B1/B2: ``search_files`` is ripgrep-backed and ripgrep respects
.gitignore by default. Files the agent writes into a gitignored path
(data/, tmp/, dist/, build output, ...) are invisible to search_files but
visible to a raw ``grep -r`` in the terminal — exactly the "cross-call
isolation" symptom the stress test reported. Fix: a ``respect_gitignore``
flag (default True to preserve global behavior) threaded through to rg's
``--no-ignore``; the execute_code sandbox passes False so sandbox-written
files are always findable.

Fix A: a zero-result search must keep the same dict shape as a populated one
(always emit the mode-appropriate key) instead of collapsing to
``{"total_count": 0}``.
"""

import os
import subprocess
import tempfile

import pytest

from tools.file_operations import ShellFileOperations


class _RealLocalEnv:
    """Minimal terminal env that runs shell commands via subprocess."""

    def __init__(self, cwd):
        self.cwd = cwd

    def execute(self, command, cwd=None, timeout=None, stdin_data=None):
        cp = subprocess.run(
            command, shell=True, cwd=cwd or self.cwd,
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "output": cp.stdout,
            "returncode": cp.returncode,
            "stdout": cp.stdout,
            "stderr": cp.stderr,
        }


@pytest.fixture
def git_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / ".gitignore").write_text("ignored_dir/\n")
    ignored = root / "ignored_dir"
    ignored.mkdir()
    (ignored / "secret.txt").write_text("needle-token-123\n")
    (root / "visible.txt").write_text("needle-token-123\n")
    return root


def test_search_respects_gitignore_by_default(git_repo):
    ops = ShellFileOperations(_RealLocalEnv(str(git_repo)))
    res = ops.search("needle-token-123", path=str(git_repo), respect_gitignore=True)
    joined = "\n".join(m.path for m in res.matches)
    assert "ignored_dir/secret.txt" not in joined, joined
    assert "visible.txt" in joined, joined


def test_search_finds_gitignored_when_respect_gitignore_false(git_repo):
    ops = ShellFileOperations(_RealLocalEnv(str(git_repo)))
    res = ops.search("needle-token-123", path=str(git_repo), respect_gitignore=False)
    joined = "\n".join(m.path for m in res.matches)
    assert "ignored_dir/secret.txt" in joined, joined


def test_search_returns_primary_key_on_empty(git_repo):
    ops = ShellFileOperations(_RealLocalEnv(str(git_repo)))
    res = ops.search("zzz-no-such-token", path=str(git_repo))
    d = res.to_dict()
    assert "matches" in d and d["matches"] == [], d
    assert d["total_count"] == 0


def test_search_files_mode_returns_files_key_on_empty(git_repo):
    ops = ShellFileOperations(_RealLocalEnv(str(git_repo)))
    res = ops.search("zzz-no-such-token", path=str(git_repo), target="files")
    d = res.to_dict()
    assert "files" in d and d["files"] == [], d


def test_execute_code_stub_defaults_respect_gitignore_false():
    from tools.code_execution_tool import generate_vermes_tools_module
    src = generate_vermes_tools_module(["search_files"], transport="uds")
    # The sandbox stub must default respect_gitignore=False so files written
    # inside the sandbox are always searchable.
    assert "respect_gitignore: bool = False" in src
    assert '"respect_gitignore": respect_gitignore' in src
