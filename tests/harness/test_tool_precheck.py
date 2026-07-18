"""Tests for harness.tool_precheck — H2.1 runtime tool pre-execution constraints."""

from __future__ import annotations

import pytest

from harness.tool_precheck import (
    PreCheckResult,
    run_precheck,
    has_precheck,
    _SIDE_EFFECT_TOOLS,
)


class TestPreCheckResult:
    def test_ok_factory(self):
        r = PreCheckResult.ok()
        assert r.passed is True
        assert r.warning is None
        assert r.block is False

    def test_warn_factory(self):
        r = PreCheckResult.warn("risky")
        assert r.passed is False
        assert r.warning == "risky"
        assert r.block is False

    def test_deny_factory(self):
        r = PreCheckResult.deny("blocked")
        assert r.passed is False
        assert r.warning == "blocked"
        assert r.block is True


class TestRunPrecheck:
    def test_pure_read_tool_skips_check(self):
        """Tools not in _SIDE_EFFECT_TOOLS return ok immediately."""
        result = run_precheck("search_documents", {"query": "test"}, agent=None)
        assert result.passed is True

    def test_side_effect_tool_without_registered_check_passes(self):
        """Side-effect tool with no registered check returns ok."""
        # 'code_execution' is in _SIDE_EFFECT_TOOLS but has no @register_precheck
        result = run_precheck("code_execution", {"code": "print(1)"}, agent=None)
        assert result.passed is True

    def test_write_file_path_traversal_warns(self):
        result = run_precheck("write_file", {"path": "../../../etc/passwd"}, agent=None)
        assert result.passed is False
        assert "Path traversal" in result.warning
        assert result.block is False  # fail-open

    def test_write_file_safe_path_passes(self):
        result = run_precheck("write_file", {"path": "/tmp/test.txt"}, agent=None)
        assert result.passed is True

    def test_write_file_sensitive_path_warns(self):
        result = run_precheck("write_file", {"path": "~/.ssh/id_rsa"}, agent=None)
        assert result.passed is False
        assert "sensitive" in result.warning

    def test_edit_file_path_traversal_warns(self):
        result = run_precheck("edit_file", {"path": "../../.env"}, agent=None)
        assert result.passed is False

    def test_delete_file_sensitive_path_blocks(self):
        """delete_file on .ssh or .env is blocked (deny, not just warn)."""
        result = run_precheck("delete_file", {"path": "~/.env"}, agent=None)
        assert result.passed is False
        assert result.block is True
        assert "sensitive" in result.warning

    def test_delete_file_safe_path_passes(self):
        result = run_precheck("delete_file", {"path": "/tmp/scratch.txt"}, agent=None)
        assert result.passed is True

    def test_terminal_tool_destructive_command_blocks(self):
        result = run_precheck("terminal_tool", {"command": "rm -rf /"}, agent=None)
        assert result.passed is False
        assert result.block is True
        assert "Destructive" in result.warning

    def test_terminal_tool_safe_command_passes(self):
        result = run_precheck("terminal_tool", {"command": "ls -la"}, agent=None)
        assert result.passed is True

    def test_terminal_tool_fork_bomb_blocks(self):
        result = run_precheck("terminal_tool", {"command": ":(){:|:&};:"}, agent=None)
        assert result.passed is False
        assert result.block is True

    def test_execute_command_destructive_blocks(self):
        result = run_precheck("execute_command", {"command": "mkfs.ext4 /dev/sda1"}, agent=None)
        assert result.passed is False
        assert result.block is True

    def test_browser_navigate_http_warns(self):
        result = run_precheck("browser_navigate", {"url": "http://example.com"}, agent=None)
        assert result.passed is False
        assert "non-HTTPS" in result.warning
        assert result.block is False  # warn only

    def test_browser_navigate_https_passes(self):
        result = run_precheck("browser_navigate", {"url": "https://example.com"}, agent=None)
        assert result.passed is True

    def test_browser_navigate_localhost_http_passes(self):
        result = run_precheck("browser_navigate", {"url": "http://localhost:8080"}, agent=None)
        assert result.passed is True

    def test_precheck_never_raises(self):
        """Even with bad args, run_precheck must not raise — fail-open at meta level."""
        # None args, missing keys, wrong types — all should be handled gracefully.
        result = run_precheck("write_file", None, agent=None)
        assert result.passed is True  # None args → ok (can't check)

        result = run_precheck("terminal_tool", {}, agent=None)
        assert result.passed is True  # empty args → ok

    def test_unknown_side_effect_tool_passes(self):
        """A tool in _SIDE_EFFECT_TOOLS but without a registered check passes."""
        # 'python_exec' is in the set but has no registered precheck.
        result = run_precheck("python_exec", {"code": "import os; os.system('rm -rf /')"}, agent=None)
        assert result.passed is True  # no check registered → ok


class TestHasPrecheck:
    def test_registered_tool_has_precheck(self):
        assert has_precheck("write_file") is True
        assert has_precheck("terminal_tool") is True

    def test_unregistered_tool_has_no_precheck(self):
        assert has_precheck("search_documents") is False
        assert has_precheck("code_execution") is False  # in side-effect set but no check

    def test_side_effect_tools_set_not_empty(self):
        assert len(_SIDE_EFFECT_TOOLS) > 0
        assert "write_file" in _SIDE_EFFECT_TOOLS
        assert "terminal_tool" in _SIDE_EFFECT_TOOLS
