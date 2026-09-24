"""Takealong round 3 — L-028~L-032 contract tests.

* L-028 (`547fff75003a`): fail-closed passthrough probe.
* L-029 (`802a9975d283`): scoped_passthrough_additions overlay.
* L-030 (`3fe8e5e443d1`): routed children scrub launch credentials.
* L-031 (`3fc1a184f8c0`+`9e232a7ff5c1`): provider_flag container guards.
* L-032 (`dcdbcb8a2b14`): source_supplied_names split surface.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_require_is_env_passthrough_is_callable():
    from tools.env_passthrough import require_is_env_passthrough
    fn = require_is_env_passthrough()
    assert callable(fn)


def test_scoped_passthrough_additions_reads_profile_env(tmp_path, monkeypatch):
    """L-029: declared names in profile .env that the filtered env lacks."""
    home = tmp_path / "profileA"
    home.mkdir()
    (home / ".env").write_text(
        "# comment\nMY_SKILL_TOKEN=sk-from-profile\nOTHER_UNDECLARED=zzz\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("vermes_constants.get_vermes_home", lambda: home, raising=False)
    monkeypatch.setattr(
        "tools.env_passthrough.source_supplied_names",
        lambda: frozenset({"MY_SKILL_TOKEN"}),
    )
    from tools.env_passthrough import scoped_passthrough_additions

    out = scoped_passthrough_additions({"PATH": "/usr/bin"})
    assert out.get("MY_SKILL_TOKEN") == "sk-from-profile"
    # Undeclared name never leaks in
    assert "OTHER_UNDECLARED" not in out


def test_scoped_passthrough_empty_without_profile_home(monkeypatch):
    monkeypatch.setattr("vermes_constants.get_vermes_home", lambda: None, raising=False)
    monkeypatch.setattr(
        "tools.env_passthrough.source_supplied_names",
        lambda: frozenset({"X"}),
    )
    from tools.env_passthrough import scoped_passthrough_additions
    assert scoped_passthrough_additions(set()) == {}


def test_sanitize_subprocess_env_overlays_scoped(tmp_path, monkeypatch):
    """L-029 wired into local._sanitize_subprocess_env."""
    home = tmp_path / "p"
    home.mkdir()
    (home / ".env").write_text("MY_SKILL_TOKEN=from-scope\n", encoding="utf-8")
    monkeypatch.setattr("vermes_constants.get_vermes_home", lambda: home, raising=False)
    monkeypatch.setattr(
        "tools.env_passthrough.source_supplied_names",
        lambda: frozenset({"MY_SKILL_TOKEN"}),
    )
    from tools.environments.local import _sanitize_subprocess_env

    out = _sanitize_subprocess_env({"PATH": "/bin"})
    assert out.get("MY_SKILL_TOKEN") == "from-scope"


def test_sanitize_fails_closed_when_passthrough_probe_breaks(monkeypatch):
    """L-028: probe failure must raise, not silently drop declared secrets."""
    import tools.env_passthrough as ep

    def _boom():
        raise RuntimeError("scope down")

    monkeypatch.setattr(ep, "require_is_env_passthrough", _boom)
    from tools.environments import local as loc
    # Re-import path uses require_is_env_passthrough inside the function
    with pytest.raises(RuntimeError):
        loc._sanitize_subprocess_env({"PATH": "/bin"})


def test_routed_child_scrubs_launch_credentials(tmp_path, monkeypatch):
    """L-030: target home ≠ process home → provider creds scrubbed."""
    target = tmp_path / "profileB"
    cur = tmp_path / "profileA"
    target.mkdir(); cur.mkdir()
    (target / ".env").write_text("MY_SKILL_TOKEN=b-own\n", encoding="utf-8")
    monkeypatch.setattr("vermes_constants.get_vermes_home", lambda: target, raising=False)
    monkeypatch.setattr(
        "tools.env_passthrough.source_supplied_names",
        lambda: frozenset({"MY_SKILL_TOKEN"}),
    )
    monkeypatch.setenv("VERMES_HOME", str(cur))
    from tools.environments.local import _sanitize_subprocess_env, _vermes_PROVIDER_ENV_BLOCKLIST

    launch_cred = next(iter(_vermes_PROVIDER_ENV_BLOCKLIST), "OPENAI_API_KEY")
    out = _sanitize_subprocess_env(
        {"PATH": "/bin", launch_cred: "sk-launch", "PLAIN": "x"}
    )
    assert launch_cred not in out
    assert out.get("PLAIN") == "x"
    assert out.get("MY_SKILL_TOKEN") == "b-own"


def test_provider_flag_and_container_guards():
    """L-031: plugin classification honored; bool coerced; guards stay on by default."""
    from tools.approval import (
        _should_skip_container_guards,
        provider_flag,
        register_provider_flag,
    )

    assert _should_skip_container_guards("singularity") is True
    assert _should_skip_container_guards("docker") is True  # no host access
    assert _should_skip_container_guards("docker", has_host_access=True) is False
    assert _should_skip_container_guards("local") is False
    assert _should_skip_container_guards("weird-plugin") is False  # default guards ON

    register_provider_flag("weird-plugin", "skip_container_guards", True)
    assert _should_skip_container_guards("weird-plugin") is True
    # Truthy non-bool must not leak
    register_provider_flag("messy-plugin", "skip_container_guards", "yes")
    assert provider_flag("messy-plugin", "skip_container_guards", False) is True
    assert isinstance(provider_flag("messy-plugin", "skip_container_guards", False), bool)


def test_source_supplied_names_split_surface():
    """L-032: declared-name surface exists and returns a frozenset."""
    from tools.env_passthrough import source_supplied_names
    names = source_supplied_names()
    assert isinstance(names, frozenset)
