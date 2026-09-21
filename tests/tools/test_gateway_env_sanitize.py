"""Contract tests for T12: gateway startup purges stale VERMES_CRON_SESSION.

Regression guard for the env-fallback trust boundary identified after the
P0 contextvar fix (L-007). A desktop launch (``electron/main.js``) builds the
gateway child env with ``{...process.env}``, so an ``export``ed
``VERMES_CRON_SESSION=1`` in the parent shell would leak into the gateway
process and — via ``is_cron_session()``'s env fallback — misclassify every
real user message as a cron session.

The fix is ``sanitize_gateway_process_env()``, called at the very top of
``gateway.run.start_gateway`` (single-threaded, before adapters/cron). It pops
the stale env key so the gateway process is clean; the env fallback then only
fires in the standalone ``vermes cron`` daemon and legacy test/CLI processes.
"""

import os
from unittest import mock

import pytest

from gateway.run import sanitize_gateway_process_env
from gateway.session_context import (
    enter_cron_session,
    is_cron_session,
    leave_cron_session,
)


@pytest.fixture(autouse=True)
def _clean_env_and_contextvar():
    from gateway.session_context import _CRON_SESSION, _UNSET

    token = _CRON_SESSION.set(_UNSET)
    old = os.environ.pop("VERMES_CRON_SESSION", None)
    try:
        yield
    finally:
        _CRON_SESSION.reset(token)
        if old is not None:
            os.environ["VERMES_CRON_SESSION"] = old


def test_sanitize_pops_stale_env_from_desktop_launch():
    """Scenario 1: env pre-polluted (desktop) -> sanitize clears the key."""
    os.environ["VERMES_CRON_SESSION"] = "1"
    sanitize_gateway_process_env()
    assert "VERMES_CRON_SESSION" not in os.environ


def test_sanitize_is_noop_when_env_absent():
    """sanitize must not raise or mutate anything when the key is missing."""
    os.environ.pop("VERMES_CRON_SESSION", None)
    before = dict(os.environ)
    sanitize_gateway_process_env()
    assert dict(os.environ) == before


def test_after_sanitize_user_path_is_not_cron():
    """Scenario 2: after sanitize, approval user path is not cron.

    ``tools.approval._is_cron_session`` and ``_is_gateway_approval_context``
    must not report cron for a real user message once the env is purged.
    """
    os.environ["VERMES_CRON_SESSION"] = "1"
    sanitize_gateway_process_env()

    from tools.approval import _is_cron_session, _is_gateway_approval_context

    assert _is_cron_session() is False
    # A gateway session (VERMES_SESSION_PLATFORM set) is NOT cron.
    from gateway.session_context import set_session_vars, clear_session_vars

    set_session_vars(platform="telegram", chat_id="123")
    try:
        assert _is_gateway_approval_context() is True
    finally:
        clear_session_vars([])


def test_contextvar_cron_unaffected_by_sanitize():
    """Scenario 3: during enter_cron_session(), still cron (contextvar wins)."""
    os.environ["VERMES_CRON_SESSION"] = "1"
    sanitize_gateway_process_env()
    token = enter_cron_session()
    try:
        assert is_cron_session() is True
    finally:
        leave_cron_session(token)


def test_standalone_cron_env_fallback_preserved():
    """Scenario 4: standalone vermes cron (env only, never sanitized) = cron.

    The standalone daemon does not call sanitize_gateway_process_env; the env
    fallback must still report cron there.
    """
    os.environ["VERMES_CRON_SESSION"] = "1"
    assert is_cron_session() is True
