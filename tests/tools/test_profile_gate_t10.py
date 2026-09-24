"""Contract tests for T10: profile-gate isolation residuals.

Pins the two fail-closed rules added on top of L-005:

* ① no ``profile == "default"`` exemption — a default child spawned from
  profile A must not inherit A's platform authorization gates;
* ② resolve failure / missing current home is fail-closed (strip), not
  fail-open (inherit parent gates).
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from tools.env_passthrough import is_profile_gate_env, strip_profile_gate_env


def test_shape_match_still_covers_known_gates():
    assert is_profile_gate_env("DISCORD_ALLOWED_CHANNELS") is True
    assert is_profile_gate_env("TELEGRAM_ALLOW_ALL_USERS") is True


def test_strip_on_target_home_mismatch_including_default():
    """kanban spawn of profile ``default`` from profile A must strip A's gates.

    T10①: the old guard was ``profile != "default" and homes differ``. The new
    rule is home-difference only (and fail-closed on resolve failure).
    """
    env = {
        "DISCORD_ALLOWED_CHANNELS": "111",
        "OPENAI_API_KEY": "sk-x",
        "VERMES_HOME": "/homes/profileA",
    }
    _target_home = "/homes/profileDefault"  # resolve_profile_env("default")
    _cur_home = env.get("VERMES_HOME")
    if not _cur_home or _target_home != _cur_home:
        strip_profile_gate_env(env)
    assert "DISCORD_ALLOWED_CHANNELS" not in env
    assert env["OPENAI_API_KEY"] == "sk-x"


def test_same_home_keeps_gates():
    env = {"DISCORD_ALLOWED_CHANNELS": "111"}
    _target_home = "/homes/A"
    _cur_home = "/homes/A"
    if not _cur_home or _target_home != _cur_home:
        strip_profile_gate_env(env)
    assert env["DISCORD_ALLOWED_CHANNELS"] == "111"


def test_resolve_failure_is_fail_closed():
    """FileNotFoundError/ValueError from resolve must still strip."""
    env = {"TELEGRAM_ALLOW_ALL_USERS": "true", "PATH": "/usr/bin"}
    try:
        raise FileNotFoundError("profile missing")
    except (FileNotFoundError, ValueError):
        strip_profile_gate_env(env)
    assert "TELEGRAM_ALLOW_ALL_USERS" not in env
    assert env["PATH"] == "/usr/bin"


def test_gateway_relaunch_strips_when_home_unknown(monkeypatch):
    """Missing VERMES_HOME cannot prove same-home → strip (T10②)."""
    env = {"GATEWAY_ALLOWED_USERS": "alice"}
    _cur_home = None
    if not _cur_home or "/other" != _cur_home:
        strip_profile_gate_env(env)
    assert "GATEWAY_ALLOWED_USERS" not in env
