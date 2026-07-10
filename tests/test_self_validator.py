"""Tests for agent.self_validator — pure unit tests, no agent required.

Run: python -m pytest tests/test_self_validator.py -v
"""

import json
import os
import tempfile
import pytest

from agent.self_validator import (
    VerifyResult,
    SelfValidator,
    CheatDetector,
    WriteFileStrategy,
    TerminalStrategy,
    PatchStrategy,
    SearchStrategy,
    ImageGenStrategy,
    DefaultStrategy,
    get_validator,
    set_mode,
    MODE_WARN,
    MODE_BLOCK,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator():
    """Fresh validator for each test (don't pollute the global singleton)."""
    return SelfValidator(mode=MODE_WARN)


@pytest.fixture
def tmp_file():
    """Create a temp file, return path.  Cleanup after test."""
    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


class FakeAgent:
    """Minimal stand-in for AIAgent — tests don't need the real thing."""

    def __init__(self):
        self.log_prefix = "[test]"


# ---------------------------------------------------------------------------
# VerifyResult tests
# ---------------------------------------------------------------------------


class TestVerifyResult:
    def test_ok_result(self):
        r = VerifyResult(ok=True, tool_name="write_file", strategy_name="test")
        assert r.ok is True
        assert r.is_warning is False
        assert r.is_error is False

    def test_warn_result(self):
        r = VerifyResult(
            ok=True, tool_name="search", strategy_name="test", severity="warn"
        )
        assert r.is_warning is True
        assert r.is_error is False

    def test_error_result(self):
        r = VerifyResult(
            ok=False, tool_name="write_file", strategy_name="test", severity="error"
        )
        assert r.is_error is True
        assert r.is_warning is False

    def test_to_dict(self):
        r = VerifyResult(ok=True, tool_name="t", strategy_name="s", message="m")
        d = r.to_dict()
        assert d["ok"] is True
        assert d["tool_name"] == "t"
        assert d["message"] == "m"


# ---------------------------------------------------------------------------
# WriteFileStrategy tests
# ---------------------------------------------------------------------------


class TestWriteFileStrategy:
    def test_successful_write(self, tmp_file, validator):
        # Write content to the temp file (simulating write_file success)
        with open(tmp_file, "w") as f:
            f.write("print('hello')")
        result = json.dumps({"success": True, "path": tmp_file})
        r = validator.verify_tool_result(
            "write_file",
            {"path": tmp_file, "content": "print('hello')"},
            result,
            FakeAgent(),
        )
        assert r.ok is True
        assert "bytes" in r.message

    def test_file_not_found(self, validator):
        result = json.dumps({"success": True})
        r = validator.verify_tool_result(
            "write_file",
            {"path": "/nonexistent/path/file.py", "content": "test"},
            result,
            FakeAgent(),
        )
        assert r.ok is False
        assert "does not exist" in r.message

    def test_empty_file_with_content(self, tmp_file, validator):
        # File exists but is empty despite content being passed
        result = json.dumps({"success": True, "path": tmp_file})
        r = validator.verify_tool_result(
            "write_file",
            {"path": tmp_file, "content": "print('hello')"},
            result,
            FakeAgent(),
        )
        assert r.ok is False
        assert "empty" in r.message.lower()

    def test_skipped_on_error(self, validator):
        r = validator.verify_tool_result(
            "write_file",
            {"path": "/tmp/x.py"},
            "some error",
            FakeAgent(),
            is_error=True,
        )
        assert r.ok is True
        assert "skipped" in r.message

    def test_no_path_arg(self, validator):
        r = validator.verify_tool_result(
            "write_file",
            {},
            json.dumps({"success": True}),
            FakeAgent(),
        )
        assert r.ok is True
        assert "skip" in r.message


# ---------------------------------------------------------------------------
# TerminalStrategy tests
# ---------------------------------------------------------------------------


class TestTerminalStrategy:
    def test_successful_command(self, validator):
        result = json.dumps({"exit_code": 0, "stdout": "hello", "stderr": ""})
        r = validator.verify_tool_result(
            "terminal", {"command": "echo hello"}, result, FakeAgent()
        )
        assert r.ok is True
        assert "exit 0" in r.message

    def test_non_zero_exit(self, validator):
        result = json.dumps({"exit_code": 1, "stdout": "", "stderr": "error msg"})
        r = validator.verify_tool_result(
            "terminal", {"command": "false"}, result, FakeAgent()
        )
        assert r.ok is False
        assert "exit code 1" in r.message

    def test_large_stderr_warning(self, validator):
        result = json.dumps({
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "x" * 1000,
        })
        r = validator.verify_tool_result(
            "terminal", {"command": "cmd"}, result, FakeAgent()
        )
        assert r.ok is True
        assert r.is_warning is True

    def test_non_json_result(self, validator):
        r = validator.verify_tool_result(
            "terminal", {"command": "ls"}, "raw output", FakeAgent()
        )
        assert r.ok is True
        assert "non-JSON" in r.message


# ---------------------------------------------------------------------------
# PatchStrategy tests
# ---------------------------------------------------------------------------


class TestPatchStrategy:
    def test_successful_patch_python(self, tmp_file, validator):
        # Write valid Python
        with open(tmp_file, "w") as f:
            f.write("x = 1\n")
        result = json.dumps({"success": True})
        r = validator.verify_tool_result(
            "patch", {"path": tmp_file}, result, FakeAgent()
        )
        assert r.ok is True

    def test_syntax_error_after_patch(self, tmp_file, validator):
        # Write invalid Python
        with open(tmp_file, "w") as f:
            f.write("def broken(:\n")
        result = json.dumps({"success": True})
        r = validator.verify_tool_result(
            "patch", {"path": tmp_file}, result, FakeAgent()
        )
        assert r.ok is False
        assert "syntax error" in r.message.lower()

    def test_file_not_found(self, validator):
        result = json.dumps({"success": True})
        r = validator.verify_tool_result(
            "patch", {"path": "/nonexistent/file.py"}, result, FakeAgent()
        )
        assert r.ok is False

    def test_non_python_file_no_syntax_check(self, tmp_file, validator):
        # Non-Python file should skip syntax check
        path = tmp_file.replace(".py", ".md")
        with open(path, "w") as f:
            f.write("# Title\n")
        try:
            result = json.dumps({"success": True})
            r = validator.verify_tool_result(
                "patch", {"path": path}, result, FakeAgent()
            )
            assert r.ok is True
        finally:
            os.remove(path)


# ---------------------------------------------------------------------------
# SearchStrategy tests
# ---------------------------------------------------------------------------


class TestSearchStrategy:
    def test_successful_search(self, validator):
        r = validator.verify_tool_result(
            "web_search",
            {"query": "python asyncio"},
            "Found 10 results: ...",
            FakeAgent(),
        )
        assert r.ok is True
        assert "chars" in r.message

    def test_empty_search_warning(self, validator):
        r = validator.verify_tool_result(
            "search_files",
            {"query": "nonexistent"},
            "",
            FakeAgent(),
        )
        assert r.ok is True
        assert r.is_warning is True

    def test_very_short_result_warning(self, validator):
        r = validator.verify_tool_result(
            "web_search",
            {"query": "test"},
            "abc",
            FakeAgent(),
        )
        assert r.ok is True
        assert r.is_warning is True

    def test_no_query_skip(self, validator):
        r = validator.verify_tool_result(
            "web_search", {}, "some result", FakeAgent()
        )
        assert r.ok is True
        assert "skip" in r.message


# ---------------------------------------------------------------------------
# ImageGenStrategy tests
# ---------------------------------------------------------------------------


class TestImageGenStrategy:
    def test_url_result(self, validator):
        result = json.dumps({"url": "https://example.com/image.png"})
        r = validator.verify_tool_result(
            "image_generate", {"prompt": "a cat"}, result, FakeAgent()
        )
        assert r.ok is True
        assert "remote" in r.message

    def test_b64_result(self, validator):
        result = json.dumps({"b64_json": "x" * 2000})
        r = validator.verify_tool_result(
            "image_generate", {"prompt": "a cat"}, result, FakeAgent()
        )
        assert r.ok is True
        assert "b64" in r.message

    def test_error_result(self, validator):
        result = json.dumps({"success": False, "error": "rate limited"})
        r = validator.verify_tool_result(
            "image_generate", {"prompt": "a cat"}, result, FakeAgent()
        )
        assert r.ok is False
        assert "rate limited" in r.message

    def test_no_url_no_b64(self, validator):
        result = json.dumps({"status": "ok"})
        r = validator.verify_tool_result(
            "image_generate", {"prompt": "a cat"}, result, FakeAgent()
        )
        assert r.ok is False

    def test_local_file_too_small(self, tmp_file, validator):
        with open(tmp_file, "w") as f:
            f.write("x")  # 1 byte
        result = json.dumps({"url": tmp_file})
        r = validator.verify_tool_result(
            "image_generate", {"prompt": "a cat"}, result, FakeAgent()
        )
        assert r.ok is False
        assert "too small" in r.message

    def test_local_file_ok(self, tmp_file, validator):
        with open(tmp_file, "wb") as f:
            f.write(b"x" * 5000)  # 5KB
        result = json.dumps({"url": tmp_file})
        r = validator.verify_tool_result(
            "image_generate", {"prompt": "a cat"}, result, FakeAgent()
        )
        assert r.ok is True


# ---------------------------------------------------------------------------
# DefaultStrategy tests
# ---------------------------------------------------------------------------


class TestDefaultStrategy:
    def test_non_empty_result(self, validator):
        r = validator.verify_tool_result(
            "some_unknown_tool", {}, "some result", FakeAgent()
        )
        assert r.ok is True

    def test_empty_result_warning(self, validator):
        r = validator.verify_tool_result(
            "some_unknown_tool", {}, "", FakeAgent()
        )
        assert r.ok is True
        assert r.is_warning is True


# ---------------------------------------------------------------------------
# CheatDetector tests
# ---------------------------------------------------------------------------


class TestCheatDetector:
    def test_delete_test_files(self):
        detector = CheatDetector()
        alerts = detector.check("terminal", {"command": "rm -rf tests/"}, "")
        assert len(alerts) >= 1
        assert "deleting test" in alerts[0]["description"]

    def test_trivial_assertion(self):
        detector = CheatDetector()
        alerts = detector.check(
            "write_file",
            {"content": "assert True\n"},
            "",
        )
        assert any("trivial assertion" in a["description"] for a in alerts)

    def test_skip_test(self):
        detector = CheatDetector()
        alerts = detector.check(
            "patch",
            {"content": "@pytest.mark.skip\ndef test_foo(): pass\n"},
            "",
        )
        assert any("skipping" in a["description"] for a in alerts)

    def test_clean_code_no_alerts(self):
        detector = CheatDetector()
        alerts = detector.check(
            "write_file",
            {"content": "def add(a, b):\n    return a + b\n"},
            "",
        )
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# SelfValidator controller tests
# ---------------------------------------------------------------------------


class TestSelfValidator:
    def test_register_custom_strategy(self, validator):
        class MyStrategy:
            name = "custom"

            def verify(self, tool_name, args, result, agent, *, is_error=False):
                return VerifyResult(
                    ok=True, tool_name=tool_name, strategy_name="custom"
                )

        validator.register("my_tool", MyStrategy())
        r = validator.verify_tool_result(
            "my_tool", {}, "result", FakeAgent()
        )
        assert r.strategy_name == "custom"

    def test_stats_tracking(self, validator):
        # One pass
        validator.verify_tool_result(
            "terminal",
            {"command": "echo hi"},
            json.dumps({"exit_code": 0, "stdout": "hi", "stderr": ""}),
            FakeAgent(),
        )
        # One failure
        validator.verify_tool_result(
            "terminal",
            {"command": "false"},
            json.dumps({"exit_code": 1, "stdout": "", "stderr": "err"}),
            FakeAgent(),
        )
        stats = validator.stats
        assert stats["total_checks"] == 2
        assert stats["passed"] == 1
        assert stats["failures"] == 1

    def test_exception_in_strategy_does_not_crash(self, validator):
        class BoomStrategy:
            name = "boom"

            def verify(self, *args, **kwargs):
                raise RuntimeError("boom")

        validator.register("boom_tool", BoomStrategy())
        r = validator.verify_tool_result(
            "boom_tool", {}, "result", FakeAgent()
        )
        assert r.ok is True  # fallback to ok on exception
        assert "validator error" in r.message

    def test_warn_mode_does_not_format(self, validator):
        r = VerifyResult(
            ok=False,
            tool_name="write_file",
            strategy_name="test",
            message="failed",
            severity="error",
        )
        # In warn mode, even errors get formatted (for visibility)
        formatted = validator.format_for_result(r)
        assert "Verification failed" in formatted

    def test_warn_mode_skips_warnings_in_format(self, validator):
        r = VerifyResult(
            ok=True,
            tool_name="search",
            strategy_name="test",
            message="empty",
            severity="warn",
        )
        formatted = validator.format_for_result(r)
        assert formatted == ""  # warnings not formatted in warn mode

    def test_block_mode_formats_warnings(self, validator):
        validator.mode = MODE_BLOCK
        r = VerifyResult(
            ok=True,
            tool_name="search",
            strategy_name="test",
            message="empty",
            severity="warn",
        )
        formatted = validator.format_for_result(r)
        assert "warning" in formatted.lower()

    def test_reset_stats(self, validator):
        validator.verify_tool_result("terminal", {}, '{"exit_code": 0}', FakeAgent())
        validator.reset_stats()
        assert validator.stats["total_checks"] == 0


# ---------------------------------------------------------------------------
# Singleton tests
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_validator_returns_same_instance(self):
        v1 = get_validator()
        v2 = get_validator()
        assert v1 is v2

    def test_set_mode(self):
        original = get_validator().mode
        set_mode(MODE_BLOCK)
        assert get_validator().mode == MODE_BLOCK
        set_mode(original)  # restore
