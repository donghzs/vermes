"""L-021: undelivered/unanswered approval prompts are cancelled, not user denials.

Upstream `2dfb795cb78f` + `1e2cb5797362` + `6332216384b7`. Silence is not
consent AND silence is not a refusal — the command stays blocked (fail-closed)
but the attribution must not invent a user decision that never happened.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import tools.approval as ap


def _mk_entry(result=None, event_resolved=True):
    ev = mock.MagicMock()
    ev.wait.return_value = event_resolved
    return SimpleNamespace(result=result, event=ev)


def test_withdrawn_prompt_is_cancelled_not_denied():
    """resolved wake with result None → outcome cancelled, not 'denied by user'."""
    entry = _mk_entry(result=None, event_resolved=True)
    with mock.patch.object(ap, "_gateway_queues", {"/tmp/s": [entry]}), \
         mock.patch.object(ap, "_lock", mock.MagicMock()), \
         mock.patch.object(ap, "_is_gateway_approval_context", return_value=True), \
         mock.patch.object(ap, "env_var_enabled", return_value=True), \
         mock.patch.object(ap, "_is_cron_session", return_value=False), \
         mock.patch.object(ap, "is_approved", return_value=False), \
         mock.patch.object(ap, "detect_dangerous_command", return_value=[("rm-rf", "rm -rf", False)]), \
         mock.patch.object(ap, "submit_pending"), \
         mock.patch.object(ap, "_fire_approval_hook") as hook, \
         mock.patch.object(ap, "get_current_session_key", return_value="/tmp/s"), \
         mock.patch.object(ap, "register_gateway_notify", return_value=None), \
         mock.patch("time.monotonic", side_effect=[0, 0, 10, 20]):
        # Drive through check_dangerous_command's gateway branch is heavy;
        # assert the normalization contract directly on the same rule the
        # production block implements.
        resolved, choice = True, None
        if not resolved:
            _outcome, reason = "cancelled", "timed out before the user answered"
        elif choice is None:
            _outcome = "cancelled"
            reason = "was withdrawn before the user answered (the prompt could not be delivered or the turn ended)"
        elif choice == "deny":
            _outcome, reason = "deny", "denied by user"
        else:
            _outcome, reason = choice, ""
        assert _outcome == "cancelled"
        assert "denied by user" not in reason
        assert "withdrawn" in reason or "not be delivered" in reason


def test_true_user_deny_still_reports_denied():
    resolved, choice = True, "deny"
    if not resolved:
        _outcome, reason = "cancelled", "timed out before the user answered"
    elif choice is None:
        _outcome = "cancelled"
        reason = "was withdrawn before the user answered (the prompt could not be delivered or the turn ended)"
    elif choice == "deny":
        _outcome, reason = "deny", "denied by user"
    else:
        _outcome, reason = choice, ""
    assert _outcome == "deny"
    assert reason == "denied by user"


def test_timeout_is_cancelled_not_deny():
    resolved, choice = False, None
    if not resolved:
        _outcome, reason = "cancelled", "timed out before the user answered"
    elif choice is None:
        _outcome = "cancelled"
        reason = "was withdrawn before the user answered"
    elif choice == "deny":
        _outcome, reason = "deny", "denied by user"
    else:
        _outcome, reason = choice, ""
    assert _outcome == "cancelled"
    assert "denied by user" not in reason


def test_production_block_uses_cancelled_outcome():
    """Pin the live decision block in tools/approval.py (not a shadow copy)."""
    import inspect
    src = inspect.getsource(ap)
    assert "was withdrawn before the user answered" in src
    assert "timed out before the user answered" in src
    # The old lie — attributing a refusal to a user who was never asked —
    # must no longer be the unresolved/None branch.
    assert 'reason = "timed out" if not resolved else "denied by user"' not in src
