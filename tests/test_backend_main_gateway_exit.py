"""Defect 1 contract: packaged gateway must not swallow non-zero SystemExit.

``_run_gateway`` used to ``except SystemExit: logger.info(...); return`` so a
``run_gateway()`` ``sys.exit(1)`` startup failure exited the backend with rc=0.
Electron only auto-restarts on ``code !== 0`` — the gateway died silently.

Invariants:
- SystemExit(0) / None → swallowed (clean stop, no restart storm)
- SystemExit(1+) → re-raised (parent can observe and restart)
- Other exceptions → sys.exit(1)
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

import backend_main as bm


def test_systemexit_zero_swallowed():
    with patch.object(bm, "logger"), patch(
        "vermes_cli.main.main", side_effect=SystemExit(0)
    ):
        # Should return normally — not raise
        bm._run_gateway()


def test_systemexit_none_swallowed():
    with patch.object(bm, "logger"), patch(
        "vermes_cli.main.main", side_effect=SystemExit(None)
    ):
        bm._run_gateway()


def test_systemexit_nonzero_propagates():
    with patch.object(bm, "logger"), patch(
        "vermes_cli.main.main", side_effect=SystemExit(1)
    ):
        with pytest.raises(SystemExit) as ei:
            bm._run_gateway()
    assert ei.value.code == 1


def test_systemexit_string_code_propagates():
    """sys.exit('msg') uses a string code — must still surface as failure."""
    with patch.object(bm, "logger"), patch(
        "vermes_cli.main.main", side_effect=SystemExit("boom")
    ):
        with pytest.raises(SystemExit) as ei:
            bm._run_gateway()
    assert ei.value.code == "boom"


def test_unexpected_exception_exits_one():
    with patch.object(bm, "logger"), patch(
        "vermes_cli.main.main", side_effect=RuntimeError("import boom")
    ):
        with pytest.raises(SystemExit) as ei:
            bm._run_gateway()
    assert ei.value.code == 1


def test_argv_restored_after_systemexit():
    """argv snapshot must restore even when cli_main raises SystemExit."""
    before = list(sys.argv)
    with patch.object(bm, "logger"), patch(
        "vermes_cli.main.main", side_effect=SystemExit(1)
    ):
        with pytest.raises(SystemExit):
            bm._run_gateway()
    assert sys.argv == before


def test_main_detects_gateway_and_does_not_swallow():
    """main() gateway branch must not catch SystemExit and return rc=0."""
    argv = ["vermes-backend", "-m", "vermes_cli.main", "gateway", "run", "--replace"]
    with patch.object(sys, "argv", argv), patch.object(
        bm, "_run_gateway", side_effect=SystemExit(1)
    ) as m:
        with pytest.raises(SystemExit) as ei:
            bm.main()
    m.assert_called_once()
    assert ei.value.code == 1
