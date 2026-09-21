"""Profile authorization-gate isolation (upstream #113270).

A child spawned FOR profile B must not inherit the spawner's platform
authorization gates (DISCORD_ALLOWED_CHANNELS, TELEGRAM_ALLOW_ALL_USERS,
GATEWAY_ALLOWED_USERS, ...).  The dispatcher / per-profile gateway restart
may itself run under profile A's gateway; without the strip, the profile-B
worker would enforce A's channel/user/role allow-list as its own.

Vermes implements the shape matcher in ``tools/env_passthrough``
(:func:`is_profile_gate_env` / :func:`strip_profile_gate_env`) and applies it
at the two real profile-child seams:

* ``vermes_cli.kanban_db._default_spawn`` — ``vermes -p <assignee>`` worker.
* ``vermes_cli.gateway.launch_detached_profile_gateway_restart`` — the
  post-update per-profile gateway respawn watcher.
"""

from __future__ import annotations

import os

from tools.env_passthrough import (
    is_profile_gate_env,
    strip_profile_gate_env,
)


def test_detects_discord_channel_gate():
    assert is_profile_gate_env("DISCORD_ALLOWED_CHANNELS") is True
    assert is_profile_gate_env("DISCORD_ALLOWED_USERS") is True


def test_detects_telegram_gate():
    assert is_profile_gate_env("TELEGRAM_ALLOWED_USERS") is True
    assert is_profile_gate_env("TELEGRAM_ALLOW_ALL_USERS") is True
    assert is_profile_gate_env("TELEGRAM_GROUP_ALLOWED_CHATS") is True


def test_detects_gateway_and_matrix_gates():
    assert is_profile_gate_env("GATEWAY_ALLOWED_USERS") is True
    assert is_profile_gate_env("GATEWAY_ALLOW_ALL_USERS") is True
    assert is_profile_gate_env("MATRIX_ALLOWED_USERS") is True


def test_never_matches_vermes_process_settings():
    assert is_profile_gate_env("VERMES_ALLOW_PRIVATE_URLS") is False
    assert is_profile_gate_env("VERMES_HOME") is False


def test_never_matches_credentials():
    assert is_profile_gate_env("OPENAI_API_KEY") is False
    assert is_profile_gate_env("ANTHROPIC_API_KEY") is False
    assert is_profile_gate_env("AGNES_API_KEY") is False


def test_never_matches_leading_underscore():
    assert is_profile_gate_env("_ALLOWED_PRIVATE") is False


def test_strip_removes_gates_preserves_others():
    env = {
        "DISCORD_ALLOWED_CHANNELS": "111,222",
        "TELEGRAM_ALLOW_ALL_USERS": "true",
        "OPENAI_API_KEY": "sk-secret",
        "VERMES_HOME": "/root/profiles/coder",
        "PATH": "/usr/bin",
    }
    out = strip_profile_gate_env(dict(env))
    assert "DISCORD_ALLOWED_CHANNELS" not in out
    assert "TELEGRAM_ALLOW_ALL_USERS" not in out
    assert out["OPENAI_API_KEY"] == "sk-secret"
    assert out["VERMES_HOME"] == "/root/profiles/coder"
    assert out["PATH"] == "/usr/bin"


def test_strip_is_case_insensitive():
    # Windows env blocks resolve case-insensitively; a case-variant gate
    # would still be read by os.getenv in the child.
    assert is_profile_gate_env("discord_allowed_channels") is True
    env = {"discord_allowed_channels": "111"}
    assert "discord_allowed_channels" not in strip_profile_gate_env(dict(env))
