"""L-013 contract tests: frozen execute_code must never spawn the GUI exe.

Regression: PyInstaller ``sys.executable`` is the app bootloader, not a
Python CLI. The old resolver ``return sys.executable`` after a failed PATH
scan spawned that exe as the child interpreter → RPC timeout after 300s.
Official Hermes has no frozen branch (CLI + venv ``sys.executable`` is real);
Vermes must resolve a real interpreter or fail fast.

Covered:
- frozen + empty PATH / no match → ``_NoChildPython`` (hard fail, not exe)
- never returns ``sys.executable`` when frozen
- bundled CLI preferred when present
- versioned name (python3.11) discovered on PATH
- probe: stdin=DEVNULL + success-only cache (failure not sticky)
"""

import os
import sys

import pytest

from tools.code_execution_tool import (
    _NoChildPython,
    _bundled_python_candidates,
    _is_usable_python,
    _path_python_candidates,
    _resolve_child_python,
    _usable_python_cache,
)


def _with_frozen(monkeypatch, value):
    if value is False:
        monkeypatch.delattr(sys, "frozen", raising=False)
    else:
        monkeypatch.setattr(sys, "frozen", value, raising=False)


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    _usable_python_cache.clear()
    yield
    _usable_python_cache.clear()


# ── hard fail: never sys.executable when frozen ──────────────────────────

def test_frozen_no_match_raises_not_executable(monkeypatch):
    _with_frozen(monkeypatch, True)
    monkeypatch.setenv("PATH", "/nonexistent-bin")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    # Hide real python from well-known roots by making _is_usable_python fail
    monkeypatch.setattr(
        "tools.code_execution_tool._is_usable_python", lambda p: False
    )
    with pytest.raises(_NoChildPython) as ei:
        _resolve_child_python("strict")  # frozen ignores strict shortcut
    msg = str(ei.value)
    assert "no usable Python" in msg
    assert sys.executable not in msg or "Tried" in msg
    assert "install" in msg.lower() or "PATH" in msg


def test_frozen_strict_also_resolves_or_raises(monkeypatch):
    """frozen + strict must NOT short-circuit to sys.executable (L-013)."""
    _with_frozen(monkeypatch, True)
    monkeypatch.setenv("PATH", "/nonexistent-bin")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.setattr(
        "tools.code_execution_tool._is_usable_python", lambda p: False
    )
    with pytest.raises(_NoChildPython):
        _resolve_child_python("strict")


def test_non_frozen_strict_still_sys_executable(monkeypatch):
    _with_frozen(monkeypatch, False)
    assert _resolve_child_python("strict") == sys.executable


# ── discovery ────────────────────────────────────────────────────────────

def test_frozen_prefers_bundled_cli(monkeypatch, tmp_path):
    _with_frozen(monkeypatch, True)
    bundled = tmp_path / "bin" / f"python{sys.version_info[0]}.{sys.version_info[1]}"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#!/bin/sh\nexit 0\n")
    bundled.chmod(0o755)
    monkeypatch.setattr(
        "tools.code_execution_tool._bundled_python_candidates",
        lambda: [str(bundled)],
    )
    monkeypatch.setattr("tools.code_execution_tool._is_usable_python", lambda p: True)
    monkeypatch.setattr("tools.code_execution_tool._path_python_candidates", lambda: [])
    got = _resolve_child_python("strict")
    assert got == str(bundled)
    assert got != sys.executable


def test_path_walk_finds_versioned_name(monkeypatch, tmp_path):
    _with_frozen(monkeypatch, True)
    pydir = tmp_path / "py"
    pydir.mkdir()
    ver = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    cand = pydir / ver
    cand.write_text("#!/bin/sh\nexit 0\n")
    cand.chmod(0o755)
    monkeypatch.setenv("PATH", str(pydir))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.setattr(
        "tools.code_execution_tool._bundled_python_candidates", lambda: []
    )
    monkeypatch.setattr("tools.code_execution_tool._is_usable_python", lambda p: True)
    got = _resolve_child_python("project")
    assert got == str(cand)


def test_path_candidates_includes_versioned_and_not_only_first(monkeypatch, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    ver = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    for d in (a, b):
        p = d / ver
        p.write_text("x")
        p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{a}{os.pathsep}{b}")
    cands = _path_python_candidates()
    assert str(a / ver) in cands
    assert str(b / ver) in cands


def test_bundled_candidates_include_framework_bin_and_meipass(monkeypatch, tmp_path):
    _with_frozen(monkeypatch, True)
    monkeypatch.setattr(
        sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False
    )
    cands = _bundled_python_candidates()
    joined = "\n".join(cands)
    assert "meipass" in joined
    assert "Python.framework" in joined or "bin" in joined


# ── probe hygiene (official code_execution_env patterns) ─────────────────

def test_probe_uses_stdin_devnull_and_success_only_cache(monkeypatch):
    """Failure must not be cached; stdin must be DEVNULL."""
    calls = []

    class _R:
        returncode = 1

    def fake_run(cmd, **kw):
        calls.append(kw)
        return _R()

    monkeypatch.setattr("tools.code_execution_tool.subprocess.run", fake_run)
    _usable_python_cache.clear()
    p = "/tmp/fake-python-for-probe"
    assert _is_usable_python(p) is False
    assert _is_usable_python(p) is False  # retried — failure not sticky
    assert len(calls) == 2
    import subprocess as sp
    assert all(kw.get("stdin") is sp.DEVNULL for kw in calls)


def test_probe_success_is_cached(monkeypatch):
    calls = []

    class _R:
        returncode = 0

    def fake_run(cmd, **kw):
        calls.append(1)
        return _R()

    monkeypatch.setattr("tools.code_execution_tool.subprocess.run", fake_run)
    _usable_python_cache.clear()
    assert _is_usable_python("/tmp/ok-python") is True
    assert _is_usable_python("/tmp/ok-python") is True
    assert len(calls) == 1
