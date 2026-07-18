"""H3.1 — Tool result structure validation (runtime quality gate).

Companion to ``tool_precheck.py`` (H2.1). While H2.1 checks *before* a tool
runs, H3.1 checks *after* — validating that the result is structurally sound
before it's appended to the conversation history and shown to the LLM.

Design mirrors H2.1:
* **Fail-open**: validation issues are appended as a warning suffix to the
  result (the LLM sees the warning and can self-correct), but the result is
  never blocked or replaced.
* **Additive**: if the harness module is unavailable, validation is a no-op.
* **Never raises**: any exception in a validator is caught and treated as
  a pass (fail-open at the meta level).
* **Complementary to self_validator**: the existing ``_get_self_validator()``
  runs first (L348-357 in tool_executor.py); H3.1 runs after and catches
  structural issues the self-validator doesn't cover (empty results, type
  mismatches, known error patterns that bypassed ``is_error`` detection).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("harness.result_validator")


# --------------------------------------------------------------------------- #
# Patterns that indicate a tool returned an error disguised as a "success"    #
# result — ``_detect_tool_failure`` already catches some of these, but H3.1   #
# adds a second net for patterns that slip through.                           #
# --------------------------------------------------------------------------- #

_UNCAUGHT_ERROR_PATTERNS = [
    re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE),
    re.compile(r"^[A-Za-z_]+Error:", re.MULTILINE),  # ValueError: ... at line start
    re.compile(r"segmentation fault|segfault", re.IGNORECASE),
    re.compile(r"core dumped", re.IGNORECASE),
]

# Tools whose results are expected to be non-empty strings.
_NON_EMPTY_RESULT_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "create_file",
        "terminal_tool",
        "execute_command",
        "run_command",
        "browser_tool",
        "browser_navigate",
        "web_search",
        "search_documents",
    }
)


def validate_result(
    function_name: str,
    result: Any,
    function_args: Dict[str, Any],
    is_error: bool,
) -> Optional[str]:
    """Validate a tool result's structure. Returns None if valid, warning str if not.

    This function never raises — all exceptions are caught and treated as a
    pass (fail-open at the meta level). The caller appends the returned
    warning to the result string so the LLM can see it and self-correct.

    Parameters
    ----------
    function_name
        The tool that produced this result.
    result
        The tool's return value (typically a string, but could be dict/list).
    function_args
        The arguments that were passed to the tool (for context in warnings).
    is_error
        Whether ``_detect_tool_failure`` already flagged this as an error.
        If True, we skip duplicate checks — the error path is already handled.

    Returns
    -------
    Optional[str]
        None if the result is structurally valid.
        A warning message string if the result has structural issues.
    """
    # If the error path already caught it, don't double-report.
    if is_error:
        return None

    try:
        # 1. None / empty result check for tools that should produce output.
        if function_name in _NON_EMPTY_RESULT_TOOLS:
            if result is None:
                return f"[harness] tool '{function_name}' returned None — expected a non-empty result."
            if isinstance(result, str) and len(result.strip()) == 0:
                return f"[harness] tool '{function_name}' returned an empty string — expected output."

        # 2. Type sanity: result should be str or dict-like (JSON-serializable).
        #    Bytes, sets, or custom objects indicate a serialization bug.
        if isinstance(result, (bytes, bytearray)):
            return (
                f"[harness] tool '{function_name}' returned raw bytes "
                f"({len(result)} bytes) — results should be decoded to str."
            )
        if isinstance(result, set):
            return (
                f"[harness] tool '{function_name}' returned a set — "
                "results should be list/dict/str for JSON serialization."
            )

        # 3. Uncaught error pattern detection — errors that bypassed is_error.
        if isinstance(result, str):
            for pattern in _UNCAUGHT_ERROR_PATTERNS:
                if pattern.search(result):
                    return (
                        f"[harness] tool '{function_name}' result contains an "
                        f"uncaught error pattern ({pattern.pattern[:40]}). "
                        "The tool may have failed silently — please verify."
                    )

        # 4. Truncated result warning — very short results from tools that
        #    typically produce longer output may indicate a truncated response.
        if (
            isinstance(result, str)
            and len(result) > 0
            and len(result) < 5
            and function_name in _NON_EMPTY_RESULT_TOOLS
        ):
            return (
                f"[harness] tool '{function_name}' returned a very short result "
                f"({len(result)} chars) — output may be truncated."
            )

        return None  # All checks passed.

    except Exception as exc:
        logger.debug("result validator for %s raised (fail-open): %s", function_name, exc)
        return None
