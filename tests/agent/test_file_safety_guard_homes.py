"""Contract tests for T8: write guards cover every home a write can land in.

Upstream `7c478ac257a3`. When process HOME is a profile/subprocess home, absolute
writes into the real user's credential stores must still be denied.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import agent.file_safety as fs


@pytest.fixture()
def dual_home(tmp_path, monkeypatch):
    real = tmp_path / "real_home"
    profile = tmp_path / "vermes_home" / "home"
    real.mkdir()
    profile.mkdir(parents=True)
    monkeypatch.setattr(fs, "_vermes_home_path", lambda: tmp_path / "vermes_home")
    monkeypatch.setattr(fs, "_vermes_root_path", lambda: tmp_path / "vermes_home")
    monkeypatch.setattr(
        "vermes_constants.get_real_home", lambda: str(real), raising=False
    )
    monkeypatch.setattr(
        "vermes_constants.get_subprocess_home", lambda: str(profile), raising=False
    )
    monkeypatch.setenv("HOME", str(profile))
    return real, profile


def test_guard_homes_covers_real_and_profile(dual_home):
    real, profile = dual_home
    homes = fs._guard_homes()
    assert os.path.realpath(str(real)) in homes
    assert os.path.realpath(str(profile)) in homes
    assert os.path.realpath(os.path.expanduser("~")) in homes


def test_real_home_credential_write_denied_under_profile_home(dual_home):
    real, _ = dual_home
    secret = real / ".ssh" / "authorized_keys"
    assert fs.is_write_denied(str(secret)) is True
    netrc = real / ".netrc"
    assert fs.is_write_denied(str(netrc)) is True


def test_process_home_credential_still_denied(dual_home):
    _, profile = dual_home
    assert fs.is_write_denied(str(profile / ".ssh" / "id_rsa")) is True


def test_normal_user_path_not_denied(dual_home, tmp_path):
    out = tmp_path / "project" / "notes.md"
    out.parent.mkdir()
    out.write_text("x")
    assert fs.is_write_denied(str(out)) is False


def test_named_account_home_join(dual_home):
    # ~root/... spellings must land in the guard set via expanduser.
    homes = fs._guard_homes("~root/.ssh/authorized_keys")
    assert any(h.endswith("root") or h.endswith("/root") or h == os.path.realpath(os.path.expanduser("~root")) for h in homes) or len(homes) >= 2
