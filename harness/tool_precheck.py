"""H2.1 — Tool pre-execution constraints (runtime quality gate).

Scenario-fit note
-----------------
This module turns the harness ``constraints.py`` infrastructure from a
"release-time self-check" into a **runtime quality gate** that runs
*before* each tool handler is invoked. It is the first concrete realisation
of the Agent × Harness × Task roadmap's H2.1 layer.

Design principles
-----------------
* **Fail-open** (consistent with P2.2 / P2.4 design philosophy): a pre-check
  failure logs a WARNING and appends a hint to the tool result, but does
  NOT block execution — unless the check explicitly returns ``block=True``
  for genuinely irreversible operations.
* **Additive**: if the harness module is unavailable (e.g. not packaged),
  the pre-check is a silent no-op. The agent path is byte-for-byte
  unchanged when the module can't be imported.
* **Scenario-fit**: only tools with **side effects** (file write, terminal,
  browser, code execution) get pre-checks. Pure-read tools (search, list,
  query) skip the check entirely — no performance cost for safe operations.
* **Extensible**: new pre-checks are registered via ``@register_precheck``.
  Each is a function ``(function_args, agent) -> PreCheckResult``.

Wiring
------
Called from ``agent/tool_executor.py`` at both execution paths
(concurrent L284 + sequential L844/871) immediately before the tool handler.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("harness.tool_precheck")


# --------------------------------------------------------------------------- #
# Result type                                                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PreCheckResult:
    """Outcome of a tool pre-execution check.

    Attributes
    ----------
    passed
        True if the check found no issues.
    warning
        Human-readable warning message (shown to the LLM in the tool result).
        None when ``passed`` is True.
    block
        If True, the tool call is blocked — the handler is NOT invoked and
        the warning is returned as the tool result. Reserved for genuinely
        irreversible operations (e.g. ``rm -rf /``). Default False (fail-open).
    """

    passed: bool
    warning: Optional[str] = None
    block: bool = False

    @staticmethod
    def ok() -> "PreCheckResult":
        return PreCheckResult(passed=True)

    @staticmethod
    def warn(message: str) -> "PreCheckResult":
        return PreCheckResult(passed=False, warning=message, block=False)

    @staticmethod
    def deny(message: str) -> "PreCheckResult":
        return PreCheckResult(passed=False, warning=message, block=True)


# --------------------------------------------------------------------------- #
# Registry                                                                     #
# --------------------------------------------------------------------------- #

PreCheckFn = Callable[[Dict[str, Any], Any], PreCheckResult]

_PRECHECKS: Dict[str, PreCheckFn] = {}

# Tools with side effects that should have pre-checks.
# Pure-read tools (search, list, query, get_*) are intentionally excluded.
_SIDE_EFFECT_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "create_file",
        "delete_file",
        "move_file",
        "terminal_tool",
        "execute_command",
        "run_command",
        "browser_tool",
        "browser_navigate",
        "browser_click",
        "browser_type",
        "code_execution",
        "execute_code",
        "python_exec",
    }
)


def register_precheck(tool_name: str) -> Callable[[PreCheckFn], PreCheckFn]:
    """Decorator: register a pre-check function for a tool."""

    def decorator(fn: PreCheckFn) -> PreCheckFn:
        _PRECHECKS[tool_name] = fn
        return fn

    return decorator


def has_precheck(tool_name: str) -> bool:
    """Return True if a pre-check is registered for this tool."""
    return tool_name in _PRECHECKS


# --------------------------------------------------------------------------- #
# Concrete pre-checks                                                         #
# --------------------------------------------------------------------------- #

# Path-traversal patterns — catch attempts to escape the workspace.
_PATH_TRAVERSAL_RE = re.compile(r"(?:\.\./|\.\.\\|/etc/|/sys/|/proc/|/dev/)")
# Destructive terminal commands that should be blocked, not just warned.
_DESTRUCTIVE_CMD_RE = re.compile(
    r"(?:"
    r"rm\s+-rf\s+/(?:\s|$)"  # rm -rf /
    r"|rm\s+-rf\s+~"  # rm -rf ~
    r"|mkfs\b"  # mkfs
    r"|dd\s+.*of=/dev/"  # dd to device
    r"|:\(\)\{.*\|"  # fork bomb
    r")",
    re.IGNORECASE,
)
# Sensitive file patterns that shouldn't be overwritten without warning.
_SENSITIVE_PATH_RE = re.compile(
    r"(?:"
    r"\.ssh/"  # SSH keys
    r"|\.env\b"  # Environment files
    r"|\.git/config"  # Git config
    r"|/etc/passwd"  # System files
    r"|/etc/shadow"
    r")",
    re.IGNORECASE,
)


@register_precheck("write_file")
def _check_write_file(args: Dict[str, Any], agent: Any) -> PreCheckResult:
    path = str(args.get("path", "") or args.get("filename", "") or "")
    if not path:
        return PreCheckResult.ok()
    if _PATH_TRAVERSAL_RE.search(path):
        return PreCheckResult.warn(
            f"Path traversal detected in write_file target '{path}'. "
            "This may escape the workspace — proceeding but please verify."
        )
    if _SENSITIVE_PATH_RE.search(path):
        return PreCheckResult.warn(
            f"write_file targets a sensitive path '{path}'. "
            "Overwriting SSH keys, .env, or system files may break the environment."
        )
    return PreCheckResult.ok()


@register_precheck("edit_file")
def _check_edit_file(args: Dict[str, Any], agent: Any) -> PreCheckResult:
    path = str(args.get("path", "") or args.get("filename", "") or "")
    if not path:
        return PreCheckResult.ok()
    if _PATH_TRAVERSAL_RE.search(path):
        return PreCheckResult.warn(
            f"Path traversal detected in edit_file target '{path}'."
        )
    return PreCheckResult.ok()


@register_precheck("create_file")
def _check_create_file(args: Dict[str, Any], agent: Any) -> PreCheckResult:
    path = str(args.get("path", "") or args.get("filename", "") or "")
    if not path:
        return PreCheckResult.ok()
    if _PATH_TRAVERSAL_RE.search(path):
        return PreCheckResult.warn(
            f"Path traversal detected in create_file target '{path}'."
        )
    return PreCheckResult.ok()


@register_precheck("delete_file")
def _check_delete_file(args: Dict[str, Any], agent: Any) -> PreCheckResult:
    path = str(args.get("path", "") or args.get("filename", "") or "")
    if not path:
        return PreCheckResult.ok()
    if _SENSITIVE_PATH_RE.search(path):
        return PreCheckResult.deny(
            f"delete_file targets a sensitive path '{path}'. "
            "Refusing to delete SSH keys, .env, or system files."
        )
    return PreCheckResult.ok()


@register_precheck("terminal_tool")
def _check_terminal_tool(args: Dict[str, Any], agent: Any) -> PreCheckResult:
    cmd = str(args.get("command", "") or args.get("cmd", "") or "")
    if not cmd:
        return PreCheckResult.ok()
    if _DESTRUCTIVE_CMD_RE.search(cmd):
        return PreCheckResult.deny(
            f"Destructive command blocked: '{cmd[:80]}'. "
            "This pattern (rm -rf /, mkfs, dd to device, fork bomb) is denied."
        )
    return PreCheckResult.ok()


@register_precheck("execute_command")
def _check_execute_command(args: Dict[str, Any], agent: Any) -> PreCheckResult:
    cmd = str(args.get("command", "") or args.get("cmd", "") or "")
    if not cmd:
        return PreCheckResult.ok()
    if _DESTRUCTIVE_CMD_RE.search(cmd):
        return PreCheckResult.deny(
            f"Destructive command blocked: '{cmd[:80]}'."
        )
    return PreCheckResult.ok()


@register_precheck("run_command")
def _check_run_command(args: Dict[str, Any], agent: Any) -> PreCheckResult:
    cmd = str(args.get("command", "") or "")
    if not cmd:
        return PreCheckResult.ok()
    if _DESTRUCTIVE_CMD_RE.search(cmd):
        return PreCheckResult.deny(
            f"Destructive command blocked: '{cmd[:80]}'."
        )
    return PreCheckResult.ok()


@register_precheck("browser_navigate")
def _check_browser_navigate(args: Dict[str, Any], agent: Any) -> PreCheckResult:
    url = str(args.get("url", "") or "")
    if not url:
        return PreCheckResult.ok()
    # Warn on non-HTTPS (but don't block — some internal tools use HTTP).
    if url.startswith("http://") and "localhost" not in url and "127.0.0.1" not in url:
        return PreCheckResult.warn(
            f"browser_navigate to non-HTTPS URL '{url[:80]}'. "
            "Traffic may be intercepted on unsecured networks."
        )
    return PreCheckResult.ok()


# --------------------------------------------------------------------------- #
# Public entry point                                                           #
# --------------------------------------------------------------------------- #


def run_precheck(
    function_name: str,
    function_args: Dict[str, Any],
    agent: Any,
) -> PreCheckResult:
    """Run the registered pre-check for a tool, if any.

    Returns ``PreCheckResult.ok()`` when:
    - The tool is not in the side-effect set (pure-read → no check needed).
    - No pre-check is registered for this tool name.
    - The pre-check passes.

    Returns ``PreCheckResult.warn(...)`` when the check finds a risky pattern
    but execution should proceed (fail-open).

    Returns ``PreCheckResult.deny(...)`` when the check finds an irreversible
    destructive operation — the tool handler should NOT be invoked.

    This function never raises — any exception in a pre-check is caught
    and treated as a pass (fail-open at the meta level).
    """
    if function_name not in _SIDE_EFFECT_TOOLS:
        return PreCheckResult.ok()
    check_fn = _PRECHECKS.get(function_name)
    if check_fn is None:
        return PreCheckResult.ok()
    try:
        return check_fn(function_args or {}, agent)
    except Exception as exc:
        logger.debug("pre-check for %s raised (fail-open): %s", function_name, exc)
        return PreCheckResult.ok()
