"""Contract tests for T12①③: presence sanitize + task-local TERMINAL_CWD."""

from __future__ import annotations

import os

import pytest

from gateway.run import sanitize_gateway_process_env
from gateway.session_context import (
    get_session_env,
    get_terminal_cwd,
    reset_terminal_cwd,
    set_terminal_cwd,
)


@pytest.fixture()
def restore_env():
    keys = (
        "VERMES_CRON_SESSION",
        "VERMES_INTERACTIVE",
        "VERMES_EXEC_ASK",
        "VERMES_GATEWAY_SESSION",
        "TERMINAL_CWD",
    )
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_sanitize_strips_parent_presence_vars(restore_env):
    """T12①: a parent shell's exported presence must not pin interactive/ask."""
    os.environ["VERMES_INTERACTIVE"] = "1"
    os.environ["VERMES_EXEC_ASK"] = "1"
    os.environ["VERMES_GATEWAY_SESSION"] = "1"
    os.environ["VERMES_CRON_SESSION"] = "1"
    sanitize_gateway_process_env()
    assert "VERMES_INTERACTIVE" not in os.environ
    assert "VERMES_EXEC_ASK" not in os.environ
    assert "VERMES_GATEWAY_SESSION" not in os.environ
    assert "VERMES_CRON_SESSION" not in os.environ


def test_sanitize_noop_when_clean(restore_env):
    before = dict(os.environ)
    sanitize_gateway_process_env()
    assert dict(os.environ) == before


def test_terminal_cwd_contextvar_is_task_local(restore_env):
    """T12③: cron workdir must not leak to other contexts via os.environ."""
    os.environ["TERMINAL_CWD"] = "/users/workspace"
    token = set_terminal_cwd("/cron/job/workdir")
    try:
        assert get_terminal_cwd() == "/cron/job/workdir"
        assert get_session_env("TERMINAL_CWD") == "/cron/job/workdir"
        # Process env untouched — concurrent user sessions still see theirs.
        assert os.environ["TERMINAL_CWD"] == "/users/workspace"
    finally:
        reset_terminal_cwd(token)
    assert get_terminal_cwd() == "/users/workspace"


def test_unset_contextvar_falls_back_to_environ(restore_env):
    os.environ["TERMINAL_CWD"] = "/from/env"
    assert get_terminal_cwd() == "/from/env"
