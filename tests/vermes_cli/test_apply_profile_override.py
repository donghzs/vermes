"""Regression tests for _apply_profile_override VERMES_HOME guard (issue #22502).

When VERMES_HOME is set to the Vermes root (e.g. systemd hardcodes
VERMES_HOME=/root/.vermes), _apply_profile_override must still read
active_profile and update VERMES_HOME to the profile directory.

When VERMES_HOME is already a profile directory (.../profiles/<name>),
_apply_profile_override must trust it and return without re-reading
active_profile (child-process inheritance contract).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _run_apply_profile_override(
    tmp_path, monkeypatch, *, VERMES_home: str | None, active_profile: str | None,
    argv: list[str] | None = None,
):
    """Run _apply_profile_override in isolation.

    Returns the value of os.environ["VERMES_HOME"] after the call,
    or None if unset.
    """
    VERMES_root = tmp_path / ".vermes"
    VERMES_root.mkdir(parents=True, exist_ok=True)

    if active_profile is not None:
        (VERMES_root / "active_profile").write_text(active_profile)

    if active_profile and active_profile != "default":
        (VERMES_root / "profiles" / active_profile).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if VERMES_home is not None:
        monkeypatch.setenv("VERMES_HOME", VERMES_home)
    else:
        monkeypatch.delenv("VERMES_HOME", raising=False)

    monkeypatch.setattr(sys, "argv", argv or ["Vermes", "gateway", "start"])

    from vermes_cli.main import _apply_profile_override
    _apply_profile_override()

    return os.environ.get("VERMES_HOME")


class TestApplyProfileOverridevermesHomeGuard:
    """Regression guard for issue #22502.

    Verifies that VERMES_HOME pointing to the Vermes root does NOT suppress
    the active_profile check, while VERMES_HOME already pointing to a
    profile directory IS trusted as-is.
    """

    def test_vermes_home_at_root_with_active_profile_is_redirected(
        self, tmp_path, monkeypatch
    ):
        """VERMES_HOME=/root/.vermes + active_profile=coder must redirect
        VERMES_HOME to .../profiles/coder.

        Bug scenario from #22502: systemd sets VERMES_HOME to the Vermes root
        and the user switches to a profile via `Vermes profile use`.
        Before the fix, the guard returned early and active_profile was ignored.
        """
        VERMES_root = tmp_path / ".vermes"
        VERMES_root.mkdir(parents=True, exist_ok=True)

        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            VERMES_home=str(VERMES_root),
            active_profile="coder",
        )

        assert result is not None, "VERMES_HOME must be set after profile redirect"
        assert "profiles" in result, (
            f"Expected VERMES_HOME to point into profiles/ dir, got: {result!r}"
        )
        assert result.endswith("coder"), (
            f"Expected VERMES_HOME to end with 'coder', got: {result!r}"
        )

    def test_vermes_home_already_profile_dir_is_trusted(self, tmp_path, monkeypatch):
        """VERMES_HOME=.../profiles/coder must not be overridden even when
        active_profile says something different.

        Preserves the child-process inheritance contract: a subprocess spawned
        with VERMES_HOME already set to a specific profile must stay in that
        profile.
        """
        VERMES_root = tmp_path / ".vermes"
        profile_dir = VERMES_root / "profiles" / "coder"
        profile_dir.mkdir(parents=True, exist_ok=True)

        (VERMES_root / "active_profile").write_text("other")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("VERMES_HOME", str(profile_dir))
        monkeypatch.setattr(sys, "argv", ["Vermes", "gateway", "start"])

        from vermes_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("VERMES_HOME") == str(profile_dir), (
            "VERMES_HOME must remain unchanged when already pointing to a profile dir"
        )

    def test_vermes_home_unset_reads_active_profile(self, tmp_path, monkeypatch):
        """Classic case: VERMES_HOME unset + active_profile=coder must set
        VERMES_HOME to the profile directory (existing behaviour must not regress).
        """
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            VERMES_home=None,
            active_profile="coder",
        )

        assert result is not None
        assert "coder" in result

    def test_vermes_home_unset_default_profile_no_redirect(self, tmp_path, monkeypatch):
        """active_profile=default must not redirect VERMES_HOME."""
        VERMES_root = tmp_path / ".vermes"
        VERMES_root.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("VERMES_HOME", raising=False)
        monkeypatch.setattr(sys, "argv", ["Vermes", "gateway", "start"])
        (VERMES_root / "active_profile").write_text("default")

        from vermes_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("VERMES_HOME") is None
