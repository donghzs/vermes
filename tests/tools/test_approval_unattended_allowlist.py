"""Contract tests for two upstream approval fixes adopted into Vermes.

* ``05e7e891751c`` — honor pattern-key allowlists when unattended: a
  dangerous command that is *permanently approved* must not be blocked by the
  cron deny path (``check_all_command_guards`` used to re-detect and block
  without consulting the permanent allowlist).
* ``2a630671d7bc`` — cron context is never interactive, even with leaked
  presence env: a cron worker that inherits ``VERMES_INTERACTIVE`` /
  ``VERMES_EXEC_ASK`` from its launching gateway must resolve as
  non-interactive (cron_mode), not block on an approval card nobody can answer.
"""

import os

import pytest

import tools.approval as approval_module
from tools.approval import (
    check_all_command_guards,
    check_dangerous_command,
    detect_dangerous_command,
)


@pytest.fixture(autouse=True)
def _clear_approval_state(monkeypatch):
    approval_module._permanent_approved.clear()
    approval_module.clear_session("default")
    approval_module.clear_session("test-session")
    monkeypatch.delenv("VERMES_CRON_SESSION", raising=False)
    monkeypatch.delenv("VERMES_INTERACTIVE", raising=False)
    monkeypatch.delenv("VERMES_EXEC_ASK", raising=False)
    monkeypatch.delenv("VERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("VERMES_YOLO_MODE", raising=False)
    monkeypatch.delenv("VERMES_SESSION_PLATFORM", raising=False)
    yield
    approval_module._permanent_approved.clear()
    approval_module.clear_session("default")
    approval_module.clear_session("test-session")


# ---------------------------------------------------------------------------
# 05e7e891751c — permanent allowlist honored in unattended (cron) deny path
# ---------------------------------------------------------------------------

class TestPermanentAllowlistInCronDeny:
    def test_permanent_approval_bypasses_cron_deny_all_guards(self, monkeypatch):
        """check_all_command_guards must not block a permanently-approved command."""
        monkeypatch.setenv("VERMES_CRON_SESSION", "1")

        cmd = "rm -rf /tmp/stuff"
        is_dangerous, pattern_key, _ = detect_dangerous_command(cmd)
        assert is_dangerous, "test command must be detected as dangerous"
        approval_module.approve_permanent(pattern_key)

        from unittest.mock import patch as mock_patch
        with mock_patch("tools.approval._get_cron_approval_mode", return_value="deny"):
            result = check_all_command_guards(cmd, "local")
            assert result["approved"], "permanently-approved command must pass cron deny"

    def test_unapproved_command_still_blocked_all_guards(self, monkeypatch):
        """Without permanent approval, the cron deny path still blocks."""
        monkeypatch.setenv("VERMES_CRON_SESSION", "1")

        from unittest.mock import patch as mock_patch
        with mock_patch("tools.approval._get_cron_approval_mode", return_value="deny"):
            result = check_all_command_guards("rm -rf /tmp/stuff", "local")
            assert not result["approved"]
            assert "BLOCKED" in result["message"]

    def test_permanent_approval_alias_is_honored(self, monkeypatch):
        """Approval stored under a legacy/alias key still counts."""
        monkeypatch.setenv("VERMES_CRON_SESSION", "1")

        cmd = "rm -rf /tmp/stuff"
        is_dangerous, pattern_key, _ = detect_dangerous_command(cmd)
        assert is_dangerous
        aliases = approval_module._approval_key_aliases(pattern_key)
        # approve under an alias (mimics a migrated key) — is_approved must resolve it
        approval_module._permanent_approved.update(aliases)

        from unittest.mock import patch as mock_patch
        with mock_patch("tools.approval._get_cron_approval_mode", return_value="deny"):
            result = check_all_command_guards(cmd, "local")
            assert result["approved"]


# ---------------------------------------------------------------------------
# 2a630671d7bc — cron context never interactive despite leaked presence env
# ---------------------------------------------------------------------------

class TestCronNeverInteractiveDespiteLeakedPresence:
    def test_leaked_interactive_does_not_make_cron_interactive(self, monkeypatch):
        """VERMES_INTERACTIVE leaked into a cron worker must not open a prompt."""
        monkeypatch.setenv("VERMES_CRON_SESSION", "1")
        monkeypatch.setenv("VERMES_INTERACTIVE", "1")  # leaked from launching gateway

        from unittest.mock import patch as mock_patch
        with mock_patch("tools.approval._get_cron_approval_mode", return_value="deny"):
            result = check_dangerous_command("rm -rf /tmp/stuff", "local")
            # deny path, not a pending approval card or an interactive prompt
            assert not result["approved"]
            assert "BLOCKED" in result["message"]

    def test_leaked_exec_ask_does_not_submit_pending_card(self, monkeypatch):
        """VERMES_EXEC_ASK leaked into cron must not submit a pending approval."""
        monkeypatch.setenv("VERMES_CRON_SESSION", "1")
        monkeypatch.setenv("VERMES_EXEC_ASK", "1")  # leaked

        from unittest.mock import patch as mock_patch
        with mock_patch("tools.approval._get_cron_approval_mode", return_value="deny"):
            result = check_dangerous_command("rm -rf /tmp/stuff", "local")
            assert not result["approved"]
            # Must be a cron deny BLOCK, never "approval_required" (which has no listener)
            assert result.get("status") != "approval_required"
            assert "BLOCKED" in result["message"]

    def test_leaked_interactive_and_ask_all_guards(self, monkeypatch):
        """Both presence vars leaked into cron → all_guards still denies, no hang."""
        monkeypatch.setenv("VERMES_CRON_SESSION", "1")
        monkeypatch.setenv("VERMES_INTERACTIVE", "1")
        monkeypatch.setenv("VERMES_EXEC_ASK", "1")

        from unittest.mock import patch as mock_patch
        with mock_patch("tools.approval._get_cron_approval_mode", return_value="deny"):
            result = check_all_command_guards("rm -rf /tmp/stuff", "local")
            assert not result["approved"]
            assert result.get("status") != "approval_required"
            assert "BLOCKED" in result["message"]

    def test_cron_approve_mode_still_passes_when_leaked(self, monkeypatch):
        """With cron_mode=approve, a leaked INTERACTIVE must not re-open prompts."""
        monkeypatch.setenv("VERMES_CRON_SESSION", "1")
        monkeypatch.setenv("VERMES_INTERACTIVE", "1")  # leaked

        from unittest.mock import patch as mock_patch
        with mock_patch("tools.approval._get_cron_approval_mode", return_value="approve"):
            result = check_dangerous_command("rm -rf /tmp/stuff", "local")
            assert result["approved"]
