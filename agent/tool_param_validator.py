"""Tool parameter validation — lightweight type checking before execution.

Validates tool arguments against a schema dictionary before the tool
is executed. Catches type errors, missing required fields, and common
mistakes (e.g., path must be a string, not a dict).

Design principles
-----------------
1. **纯函数** — No classes, no side effects. Each validator returns
   (ok, message). If ok is True, message is empty.
2. **核心工具优先** — Only validates the ~20 most common tools.
   Unknown tools pass through with no validation.
3. **warn 模式** — Returns warnings, never blocks execution.
   The caller decides whether to proceed.
4. **零依赖** — Stdlib only. No Pydantic, no marshmallow.

Integration point
-----------------
In ``tool_executor.py``, before executing the tool::

    from agent.tool_param_validator import validate_tool_params
    ok, msg = validate_tool_params(tool_name, args)
    if not ok:
        # log warning, but still execute (warn mode)
        logger.warning("param validation: %s", msg)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

logger = logging.getLogger("vermes.tool_param_validator")

# ---------------------------------------------------------------------------
# Parameter type definitions
# ---------------------------------------------------------------------------

# Each tool's expected parameters: {param_name: (type, required, description)}
TOOL_SCHEMAS: dict[str, dict[str, tuple[type, bool, str]]] = {
    "write_file": {
        "path": (str, True, "File path to write"),
        "content": (str, True, "Content to write"),
    },
    "read_file": {
        "path": (str, True, "File path to read"),
        "offset": (int, False, "Line to start reading from"),
        "limit": (int, False, "Maximum lines to read"),
    },
    "patch": {
        "path": (str, True, "File path to patch"),
        "content": (str, True, "Patch content"),
    },
    "terminal": {
        "command": (str, True, "Command to execute"),
        "workdir": (str, False, "Working directory"),
        "timeout": (int, False, "Timeout in seconds"),
    },
    "search_files": {
        "path": (str, True, "Directory to search in"),
        "pattern": (str, True, "Search pattern"),
    },
    "web_search": {
        "query": (str, True, "Search query"),
    },
    "web_extract": {
        "url": (str, True, "URL to extract content from"),
    },
    "execute_code": {
        "code": (str, True, "Python code to execute"),
    },
    "browser_navigate": {
        "url": (str, True, "URL to navigate to"),
    },
    "browser_click": {
        "selector": (str, True, "CSS selector to click"),
    },
    "browser_type": {
        "selector": (str, True, "CSS selector for input field"),
        "text": (str, True, "Text to type"),
    },
    "send_message": {
        "target": (str, True, "Message recipient"),
        "message": (str, True, "Message content"),
    },
    "cronjob": {
        "schedule": (str, True, "Cron schedule expression"),
        "action": (str, True, "Action to perform"),
    },
    "todo": {
        "action": (str, True, "Todo action (add/update/list/remove)"),
    },
    "memory": {
        "action": (str, True, "Memory action"),
        "content": (str, False, "Memory content"),
    },
    "delegate_task": {
        "task": (str, True, "Task description for the delegate"),
    },
    "skill_manage": {
        "action": (str, True, "Skill management action"),
    },
    "process": {
        "action": (str, True, "Process action"),
    },
}


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------


def validate_tool_params(
    tool_name: str,
    args: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    """Validate tool parameters against the schema.

    Parameters
    ----------
    tool_name : str
        Name of the tool to validate.
    args : Mapping or None
        Tool arguments.

    Returns
    -------
    tuple[bool, str]
        (ok, message). If ok is True, message is empty.
        If ok is False, message describes the validation error.
    """
    if tool_name not in TOOL_SCHEMAS:
        # Unknown tool — no validation, pass through
        return True, ""

    if args is None:
        args = {}

    if not isinstance(args, Mapping):
        return False, f"args must be a dict/mapping, got {type(args).__name__}"

    schema = TOOL_SCHEMAS[tool_name]

    # Check required parameters
    for param_name, (expected_type, required, desc) in schema.items():
        if param_name not in args:
            if required:
                return False, f"missing required parameter '{param_name}' ({desc})"
            continue

        value = args[param_name]

        # Type check (allow None for optional params)
        if value is None and not required:
            continue

        if not isinstance(value, expected_type):
            actual_type = type(value).__name__
            return False, (
                f"parameter '{param_name}' expects {expected_type.__name__}, "
                f"got {actual_type}"
            )

    # Check for unexpected parameters (warn only, don't fail)
    extra = set(args.keys()) - set(schema.keys())
    if extra:
        # Don't fail — just log a debug message
        logger.debug("tool '%s' got unexpected params: %s", tool_name, extra)

    # Tool-specific validations
    return _tool_specific_validation(tool_name, args)


def _tool_specific_validation(tool_name: str, args: Mapping[str, Any]) -> tuple[bool, str]:
    """Run tool-specific validation checks."""
    path_keys = ("path", "file_path", "workdir")

    for key in path_keys:
        if key in args and isinstance(args[key], str):
            path_val = args[key]
            # Check for obviously wrong path values
            if path_val.startswith("http"):
                return False, (
                    f"parameter '{key}' looks like a URL, not a file path "
                    f"(got: {path_val[:80]})"
                )
            # Check for empty string
            if not path_val.strip():
                return False, f"parameter '{key}' is empty"

    # URL validation
    for key in ("url",):
        if key in args and isinstance(args[key], str):
            url_val = args[key]
            if not url_val.startswith(("http://", "https://", "file://")):
                return False, (
                    f"parameter '{key}' should be a URL starting with "
                    f"http://, https://, or file:// (got: {url_val[:80]})"
                )

    # Command validation for terminal
    if tool_name == "terminal" and "command" in args:
        cmd = args["command"]
        if isinstance(cmd, str) and not cmd.strip():
            return False, "parameter 'command' is empty"

    # Code validation for execute_code
    if tool_name == "execute_code" and "code" in args:
        code = args["code"]
        if isinstance(code, str) and not code.strip():
            return False, "parameter 'code' is empty"

    return True, ""


def get_tool_schema(tool_name: str) -> dict[str, tuple[type, bool, str]] | None:
    """Get the parameter schema for a tool.

    Returns None if the tool is not in the schema registry.
    """
    return TOOL_SCHEMAS.get(tool_name)


def list_validated_tools() -> list[str]:
    """Return a list of tool names that have parameter schemas."""
    return sorted(TOOL_SCHEMAS.keys())
