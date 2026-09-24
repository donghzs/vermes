"""Contract tests for T5: sync-version.sh must fail loudly and not depend on grep shims.

Reproduces the silent-failure mode (EXIT=1, no output) that hit the WorkBuddy
``grep`` shim: the old script extracted ``__version__`` via
``grep | grep | tr`` under ``set -euo pipefail``, so a shimmed grep's false
negative killed the pipeline with zero diagnostics. The rewrite extracts the
version with ``ast`` and writes JSON with ``json`` — no shell grep/sed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sync-version.sh"


@pytest.fixture()
def isolated_tree(tmp_path, monkeypatch):
    """Copy the minimal tree sync-version.sh touches into a tmp sandbox."""
    init_src = (ROOT / "vermes_cli" / "__init__.py").read_text(encoding="utf-8")
    (tmp_path / "vermes_cli").mkdir()
    (tmp_path / "vermes_cli" / "__init__.py").write_text(init_src, encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "sync-version.sh").write_text(
        SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "electron").mkdir()
    (tmp_path / "frontend").mkdir()
    for pkg in (tmp_path / "electron" / "package.json",
                tmp_path / "frontend" / "package.json",
                tmp_path / "package.json"):
        pkg.write_text(json.dumps({"name": "x", "version": "0.0.0"}), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    return tmp_path


def _run(tree: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        ["bash", str(tree / "scripts" / "sync-version.sh")],
        cwd=tree,
        capture_output=True,
        text=True,
        env=env,
    )


def test_sync_versions_all_targets(isolated_tree):
    proc = _run(isolated_tree)
    assert proc.returncode == 0, proc.stderr
    expected = "2.5.2"
    # Pull expected from the copied __init__ so the test tracks the real version.
    text = (isolated_tree / "vermes_cli" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            expected = line.split('"')[1]
            break
    assert f"Version: {expected}" in proc.stdout
    assert (isolated_tree / "electron" / "version.txt").read_text().strip() == expected
    assert (isolated_tree / "version.txt").read_text().strip() == expected
    for pkg in ("electron/package.json", "frontend/package.json", "package.json"):
        data = json.loads((isolated_tree / pkg).read_text(encoding="utf-8"))
        assert data["version"] == expected, pkg
    assert f'version = "{expected}"' in (isolated_tree / "pyproject.toml").read_text()


def test_missing_init_fails_with_stderr(isolated_tree):
    (isolated_tree / "vermes_cli" / "__init__.py").unlink()
    proc = _run(isolated_tree)
    assert proc.returncode != 0
    assert "ERROR" in proc.stderr
    assert proc.stderr.strip(), "must not fail silently"


def test_unparseable_init_fails_with_stderr(isolated_tree):
    (isolated_tree / "vermes_cli" / "__init__.py").write_text(
        "this is not python !!!", encoding="utf-8"
    )
    proc = _run(isolated_tree)
    assert proc.returncode != 0
    assert "ERROR" in proc.stderr
