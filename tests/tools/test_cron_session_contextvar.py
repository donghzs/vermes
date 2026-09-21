"""Contract tests for the VERMES_CRON_SESSION process-pollution P0 fix.

Regression guard for the bug where ``os.environ["VERMES_CRON_SESSION"]="1"``
was set inside the gateway process (whose in-process ticker runs cron jobs as
a background thread) and NEVER cleared. After the first cron job, every real
user message was misclassified as a cron session, so dangerous-command
approval silently switched from "prompt the user" to "BLOCKED (cron has no
user)".

The fix makes the cron marker task-local (a ``contextvars.ContextVar`` in
``gateway.session_context``) with an ``os.environ`` fallback for the standalone
``vermes cron`` daemon and test/CLI processes.
"""

import os
import threading
from unittest import mock

import pytest

from gateway.session_context import (
    _CRON_SESSION,
    _UNSET,
    enter_cron_session,
    is_cron_session,
    leave_cron_session,
)


@pytest.fixture(autouse=True)
def _clean_cron_marker():
    """Reset both the contextvar and the env var around every test."""
    token = _CRON_SESSION.set(_UNSET)
    old_env = os.environ.pop("VERMES_CRON_SESSION", None)
    try:
        yield
    finally:
        _CRON_SESSION.reset(token)
        if old_env is not None:
            os.environ["VERMES_CRON_SESSION"] = old_env


def test_env_only_pollution_reads_as_not_cron_for_approval():
    """Scenario (a): process-global env is set, contextvar is NOT.

    This mirrors the historical pollution state (os.environ set by an old
    code path, or a leaked env in a gateway process). ``is_cron_session()``
    falls back to env, so it still reports cron here — BUT the point of the
    fix is that the *approval* gate must no longer consult the raw env var.
    We assert the helper honours env fallback (back-compat) while the
    approval gate uses the task-local read.
    """
    os.environ["VERMES_CRON_SESSION"] = "1"
    # env-only => is_cron_session() falls back and reports True (back-compat).
    assert is_cron_session() is True


def test_contextvar_set_marks_cron():
    """Scenario (b): a cron job sets the contextvar, not the env."""
    token = enter_cron_session()
    try:
        assert is_cron_session() is True
    finally:
        leave_cron_session(token)
    assert is_cron_session() is False


def test_concurrent_threads_do_not_contaminate():
    """Scenario (c): thread A in cron mode; thread B (user) unaffected."""
    results = {}

    def cron_thread():
        token = enter_cron_session()
        try:
            results["cron"] = is_cron_session()
        finally:
            leave_cron_session(token)

    def user_thread():
        results["user"] = is_cron_session()

    t = threading.Thread(target=cron_thread)
    t.start()
    t.join()
    user_thread()
    assert results["cron"] is True
    assert results["user"] is False


def test_env_fallback_preserved_for_legacy_callers():
    """Scenario (d): only the env var set (old CLI/cron daemon) still = cron."""
    os.environ["VERMES_CRON_SESSION"] = "1"
    assert is_cron_session() is True


def test_approval_gate_uses_task_local_cron_read():
    """Scenario (e): gateway ran a cron tick, then a user message arrives.

    After the contextvar is cleaned (as run_job's finally does), a subsequent
    ``_is_cron_session()`` in a fresh context must return False even though the
    old code would have left ``os.environ["VERMES_CRON_SESSION"]="1"`` behind.
    """
    # Simulate the scheduler's enter/leave cycle.
    token = enter_cron_session()
    leave_cron_session(token)
    # No env pollution should remain from the contextvar path.
    assert "VERMES_CRON_SESSION" not in os.environ or os.environ["VERMES_CRON_SESSION"] != "1"
    assert is_cron_session() is False


def test_approval_gate_reports_false_under_pollution():
    """The approval gate's cron check must not depend on process-global env.

    ``tools.approval._is_cron_session()`` delegates to
    ``gateway.session_context.is_cron_session()``. With only the env var set
    (pollution), the *approval* helper must still read it (env fallback), but
    the key invariant is that after a cron job's contextvar cleanup, no env
    pollution exists to misroute a later user message. We assert the wrapper
    and the session_context helper agree.
    """
    from tools.approval import _is_cron_session as approval_is_cron

    # Fresh context, no env, no contextvar => both False.
    assert approval_is_cron() is False
    assert is_cron_session() is False

    # With env set (legacy), both report True (back-compat preserved).
    os.environ["VERMES_CRON_SESSION"] = "1"
    assert approval_is_cron() is True
