"""Tests for agent.tool_param_validator — lightweight parameter validation.

Run: python -m pytest tests/test_tool_param_validator.py -v
"""

import pytest

from agent.tool_param_validator import (
    validate_tool_params,
    get_tool_schema,
    list_validated_tools,
    TOOL_SCHEMAS,
)


# ---------------------------------------------------------------------------
# Basic validation tests
# ---------------------------------------------------------------------------


class TestValidateToolParams:
    def test_unknown_tool_passes_through(self):
        ok, msg = validate_tool_params("unknown_tool", {"foo": "bar"})
        assert ok is True
        assert msg == ""

    def test_none_args_accepted(self):
        ok, msg = validate_tool_params("write_file", None)
        # write_file requires path and content, so None args should fail
        assert ok is False
        assert "missing" in msg.lower()

    def test_non_mapping_args_rejected(self):
        ok, msg = validate_tool_params("write_file", "not a dict")
        assert ok is False
        assert "dict" in msg.lower()

    def test_extra_params_allowed(self):
        ok, msg = validate_tool_params("write_file", {
            "path": "/tmp/test.py",
            "content": "print('hi')",
            "extra_param": "ignored",
        })
        assert ok is True


# ---------------------------------------------------------------------------
# Required parameter tests
# ---------------------------------------------------------------------------


class TestRequiredParams:
    def test_missing_required_param(self):
        ok, msg = validate_tool_params("write_file", {"path": "/tmp/test.py"})
        assert ok is False
        assert "content" in msg
        assert "missing" in msg.lower()

    def test_all_required_present(self):
        ok, msg = validate_tool_params("write_file", {
            "path": "/tmp/test.py",
            "content": "print('hi')",
        })
        assert ok is True

    def test_optional_param_missing_ok(self):
        ok, msg = validate_tool_params("read_file", {"path": "/tmp/test.py"})
        assert ok is True

    def test_optional_param_none_ok(self):
        ok, msg = validate_tool_params("read_file", {
            "path": "/tmp/test.py",
            "offset": None,
            "limit": None,
        })
        assert ok is True


# ---------------------------------------------------------------------------
# Type checking tests
# ---------------------------------------------------------------------------


class TestTypeChecking:
    def test_wrong_type_string_expected(self):
        ok, msg = validate_tool_params("write_file", {
            "path": 123,
            "content": "hello",
        })
        assert ok is False
        assert "str" in msg.lower()
        assert "int" in msg.lower()

    def test_wrong_type_int_expected(self):
        ok, msg = validate_tool_params("read_file", {
            "path": "/tmp/test.py",
            "offset": "not_an_int",
        })
        assert ok is False
        assert "int" in msg.lower()

    def test_correct_types(self):
        ok, msg = validate_tool_params("read_file", {
            "path": "/tmp/test.py",
            "offset": 10,
            "limit": 100,
        })
        assert ok is True

    def test_terminal_timeout_must_be_int(self):
        ok, msg = validate_tool_params("terminal", {
            "command": "ls",
            "timeout": "30",
        })
        assert ok is False
        assert "int" in msg.lower()


# ---------------------------------------------------------------------------
# Tool-specific validation tests
# ---------------------------------------------------------------------------


class TestToolSpecificValidation:
    def test_path_looks_like_url(self):
        ok, msg = validate_tool_params("write_file", {
            "path": "https://example.com/file.py",
            "content": "code",
        })
        assert ok is False
        assert "url" in msg.lower()

    def test_empty_path_rejected(self):
        ok, msg = validate_tool_params("write_file", {
            "path": "  ",
            "content": "code",
        })
        assert ok is False
        assert "empty" in msg.lower()

    def test_url_must_start_with_http(self):
        ok, msg = validate_tool_params("web_extract", {
            "url": "not-a-url",
        })
        assert ok is False
        assert "url" in msg.lower()

    def test_valid_url_accepted(self):
        ok, msg = validate_tool_params("web_extract", {
            "url": "https://example.com",
        })
        assert ok is True

    def test_file_url_accepted(self):
        ok, msg = validate_tool_params("web_extract", {
            "url": "file:///tmp/test.html",
        })
        assert ok is True

    def test_empty_command_rejected(self):
        ok, msg = validate_tool_params("terminal", {
            "command": "  ",
        })
        assert ok is False
        assert "empty" in msg.lower()

    def test_empty_code_rejected(self):
        ok, msg = validate_tool_params("execute_code", {
            "code": "  ",
        })
        assert ok is False
        assert "empty" in msg.lower()

    def test_workdir_url_rejected(self):
        ok, msg = validate_tool_params("terminal", {
            "command": "ls",
            "workdir": "https://example.com",
        })
        assert ok is False
        assert "url" in msg.lower()


# ---------------------------------------------------------------------------
# Schema inspection tests
# ---------------------------------------------------------------------------


class TestSchemaInspection:
    def test_get_tool_schema_known(self):
        schema = get_tool_schema("write_file")
        assert schema is not None
        assert "path" in schema
        assert "content" in schema

    def test_get_tool_schema_unknown(self):
        assert get_tool_schema("nonexistent") is None

    def test_list_validated_tools(self):
        tools = list_validated_tools()
        assert "write_file" in tools
        assert "terminal" in tools
        assert "read_file" in tools

    def test_all_schemas_have_required_info(self):
        """Every schema entry should have (type, required, description)."""
        for tool_name, params in TOOL_SCHEMAS.items():
            for param_name, spec in params.items():
                assert len(spec) == 3, f"{tool_name}.{param_name} has wrong spec length"
                expected_type, required, desc = spec
                assert isinstance(expected_type, type), f"{tool_name}.{param_name} type not a type"
                assert isinstance(required, bool), f"{tool_name}.{param_name} required not bool"
                assert isinstance(desc, str) and desc, f"{tool_name}.{param_name} desc empty"

    def test_validated_tool_count(self):
        """Should have at least 15 tools with schemas."""
        assert len(TOOL_SCHEMAS) >= 15


# ---------------------------------------------------------------------------
# Integration-style tests
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_all_core_tools_validated(self):
        """Core tools should all be in the schema."""
        core_tools = [
            "write_file", "read_file", "patch", "terminal",
            "search_files", "web_search", "web_extract",
            "execute_code", "browser_navigate",
        ]
        for tool in core_tools:
            assert tool in TOOL_SCHEMAS, f"missing schema for core tool: {tool}"

    def test_round_trip_validation(self):
        """Valid arguments for each tool should pass."""
        valid_args = {
            "write_file": {"path": "/tmp/test.py", "content": "print('hi')"},
            "read_file": {"path": "/tmp/test.py", "offset": 1, "limit": 10},
            "patch": {"path": "/tmp/test.py", "content": "+new line"},
            "terminal": {"command": "ls -la", "timeout": 30},
            "search_files": {"path": "/tmp", "pattern": "*.py"},
            "web_search": {"query": "python tutorial"},
            "web_extract": {"url": "https://example.com"},
            "execute_code": {"code": "print('hello')"},
            "browser_navigate": {"url": "https://example.com"},
            "browser_click": {"selector": "#button"},
            "browser_type": {"selector": "#input", "text": "hello"},
            "send_message": {"target": "user", "message": "hi"},
            "todo": {"action": "add"},
            "memory": {"action": "save", "content": "fact"},
            "delegate_task": {"task": "do something"},
            "skill_manage": {"action": "list"},
            "process": {"action": "list"},
            "cronjob": {"schedule": "0 9 * * *", "action": "create"},
        }
        for tool_name, args in valid_args.items():
            ok, msg = validate_tool_params(tool_name, args)
            assert ok, f"{tool_name} failed with valid args: {msg}"
