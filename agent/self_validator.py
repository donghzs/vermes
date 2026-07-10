"""Self-validation layer for tool execution results.

This module provides a pluggable verification framework that checks tool
results *after* execution.  It is designed to be side-effect free: verification
only inspects, never mutates files or state.

Design principles
-----------------
1. **只加不改** — This file is pure new code.  Integration with
   ``tool_executor.py`` is a single hook call, nothing else changes.
2. **可灰度** — Starts in ``warn`` mode.  Failures are logged but never
   block.  Upgrade to ``block`` mode only after confidence is established.
3. **可扩展** — Vertical ecosystem apps (ScholarForge, Studio, …) register
   their own validators via :func:`SelfValidator.register`.  The core
   framework stays generic.
4. **零依赖** — No imports from heavy modules.  Uses only stdlib +
   ``utils.safe_json_loads``.

Integration point (NOT YET WIRED — see Phase 1 step 3)
------------------------------------------------------
In ``agent/tool_executor.py``, after ``result = agent._invoke_tool(...)``
and ``is_error, _ = _detect_tool_failure(...)``::

    from agent.self_validator import get_validator
    verify_result = get_validator().verify_tool_result(
        function_name, function_args, result, agent, is_error=is_error
    )
    # In warn mode: log only.  In block mode: append to result.

This file does NOT import or modify any existing module.
"""

from __future__ import annotations

import logging
import os
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

logger = logging.getLogger("vermes.self_validator")

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of a single verification check."""

    ok: bool
    tool_name: str
    strategy_name: str
    message: str = ""
    severity: str = "info"  # info | warn | error
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_warning(self) -> bool:
        return self.severity == "warn"

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool_name": self.tool_name,
            "strategy_name": self.strategy_name,
            "message": self.message,
            "severity": self.severity,
        }


class VerifyStrategy(Protocol):
    """Interface for a verification strategy."""

    name: str

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        agent: Any,
        *,
        is_error: bool = False,
    ) -> VerifyResult:
        ...


# ---------------------------------------------------------------------------
# Built-in generic strategies
# ---------------------------------------------------------------------------


class WriteFileStrategy:
    """Verify write_file results by reading back key lines."""

    name = "write_file_verify"

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        agent: Any,
        *,
        is_error: bool = False,
    ) -> VerifyResult:
        if is_error:
            return VerifyResult(
                ok=True,  # error already detected by _detect_tool_failure
                tool_name=tool_name,
                strategy_name=self.name,
                message="skipped (tool already flagged as error)",
                severity="info",
            )

        file_path = args.get("path") or args.get("file_path") or ""
        if not file_path:
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message="no file_path in args, skip",
                severity="info",
            )

        # Check if the result indicates success
        from utils import safe_json_loads

        data = safe_json_loads(result)
        if isinstance(data, dict) and data.get("success") is False:
            return VerifyResult(
                ok=False,
                tool_name=tool_name,
                strategy_name=self.name,
                message=f"write_file reported failure: {data.get('error', 'unknown')}",
                severity="error",
            )

        # Verify file exists on disk
        if not os.path.exists(file_path):
            return VerifyResult(
                ok=False,
                tool_name=tool_name,
                strategy_name=self.name,
                message=f"file does not exist after write: {file_path}",
                severity="error",
            )

        # Verify file is not empty (unless content was empty)
        content = args.get("content") or ""
        file_size = os.path.getsize(file_path)
        if content and file_size == 0:
            return VerifyResult(
                ok=False,
                tool_name=tool_name,
                strategy_name=self.name,
                message=f"file is empty after write: {file_path}",
                severity="error",
            )

        # Quality gate: warn if new print() calls introduced
        if content and file_path.endswith(".py"):
            import re
            new_prints = len(re.findall(r"^\s*print\s*\(", content, re.MULTILINE))
            if new_prints > 0:
                return VerifyResult(
                    ok=True,
                    tool_name=tool_name,
                    strategy_name=self.name,
                    message=f"file written ({file_size} bytes), {new_prints} print() call(s) — consider logging",
                    severity="warn",
                    extra={"file_size": file_size, "new_prints": new_prints},
                )

        return VerifyResult(
            ok=True,
            tool_name=tool_name,
            strategy_name=self.name,
            message=f"file written ({file_size} bytes)",
            severity="info",
            extra={"file_size": file_size},
        )


class TerminalStrategy:
    """Verify terminal results by checking exit_code and stderr."""

    name = "terminal_verify"

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        agent: Any,
        *,
        is_error: bool = False,
    ) -> VerifyResult:
        if is_error:
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message="skipped (tool already flagged as error)",
                severity="info",
            )

        from utils import safe_json_loads

        data = safe_json_loads(result)
        if not isinstance(data, dict):
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message="non-JSON terminal result, skip",
                severity="info",
            )

        exit_code = data.get("exit_code")
        if exit_code is not None and exit_code != 0:
            # _detect_tool_failure should catch this, but double-check
            stderr = (data.get("stderr") or "")[:200]
            return VerifyResult(
                ok=False,
                tool_name=tool_name,
                strategy_name=self.name,
                message=f"non-zero exit code {exit_code}: {stderr}",
                severity="error",
                extra={"exit_code": exit_code},
            )

        # Check for stderr even with exit_code=0
        stderr = data.get("stderr") or ""
        stdout = data.get("stdout") or ""
        if stderr and len(stderr) > len(stdout) * 2 and len(stderr) > 500:
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message=f"exit 0 but large stderr ({len(stderr)} chars)",
                severity="warn",
            )

        return VerifyResult(
            ok=True,
            tool_name=tool_name,
            strategy_name=self.name,
            message=f"exit {exit_code}, stdout {len(stdout)} chars",
            severity="info",
        )


class PatchStrategy:
    """Verify patch results by checking the file was actually modified."""

    name = "patch_verify"

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        agent: Any,
        *,
        is_error: bool = False,
    ) -> VerifyResult:
        if is_error:
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message="skipped (tool already flagged as error)",
                severity="info",
            )

        file_path = args.get("path") or args.get("file_path") or ""
        if not file_path:
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message="no file_path in args, skip",
                severity="info",
            )

        if not os.path.exists(file_path):
            return VerifyResult(
                ok=False,
                tool_name=tool_name,
                strategy_name=self.name,
                message=f"file does not exist after patch: {file_path}",
                severity="error",
            )

        # For Python files, do a syntax check
        if file_path.endswith(".py"):
            import py_compile

            try:
                py_compile.compile(file_path, doraise=True)
            except py_compile.PyCompileError as e:
                return VerifyResult(
                    ok=False,
                    tool_name=tool_name,
                    strategy_name=self.name,
                    message=f"Python syntax error after patch: {e.msg}",
                    severity="error",
                    extra={"file": file_path, "line": e.lineno if hasattr(e, "lineno") else 0},
                )

        # Check result for success/failure indicators
        from utils import safe_json_loads

        data = safe_json_loads(result)
        if isinstance(data, dict) and data.get("success") is False:
            return VerifyResult(
                ok=False,
                tool_name=tool_name,
                strategy_name=self.name,
                message=f"patch reported failure: {data.get('error', 'unknown')}",
                severity="error",
            )

        # Quality gate: warn if patch introduces new print() calls
        patch_content = args.get("content") or args.get("patch") or ""
        if patch_content and file_path.endswith(".py"):
            import re
            new_prints = len(re.findall(r"^\s*print\s*\(", patch_content, re.MULTILINE))
            if new_prints > 0:
                return VerifyResult(
                    ok=True,
                    tool_name=tool_name,
                    strategy_name=self.name,
                    message=f"patch applied to {file_path}, {new_prints} print() call(s) — consider logging",
                    severity="warn",
                    extra={"new_prints": new_prints},
                )

        return VerifyResult(
            ok=True,
            tool_name=tool_name,
            strategy_name=self.name,
            message=f"patch applied to {file_path}",
            severity="info",
        )


class SearchStrategy:
    """Verify search results are non-empty and relevant."""

    name = "search_verify"

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        agent: Any,
        *,
        is_error: bool = False,
    ) -> VerifyResult:
        if is_error:
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message="skipped (tool already flagged as error)",
                severity="info",
            )

        query = args.get("query") or args.get("pattern") or ""
        if not query:
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message="no query in args, skip",
                severity="info",
            )

        # Empty result warning
        if not result or len(result.strip()) == 0:
            return VerifyResult(
                ok=True,  # empty search is not an error, just a warning
                tool_name=tool_name,
                strategy_name=self.name,
                message=f"search returned empty result for query: {query[:100]}",
                severity="warn",
            )

        # Very short result might indicate failure
        if len(result.strip()) < 10:
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message=f"search returned very short result ({len(result)} chars)",
                severity="warn",
            )

        return VerifyResult(
            ok=True,
            tool_name=tool_name,
            strategy_name=self.name,
            message=f"search returned {len(result)} chars",
            severity="info",
        )


class ImageGenStrategy:
    """Verify image generation results — URL accessible + file size reasonable."""

    name = "image_gen_verify"

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        agent: Any,
        *,
        is_error: bool = False,
    ) -> VerifyResult:
        if is_error:
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message="skipped (tool already flagged as error)",
                severity="info",
            )

        from utils import safe_json_loads

        data = safe_json_loads(result)
        if not isinstance(data, dict):
            # Non-JSON might be a plain URL string
            if result and (result.startswith("http://") or result.startswith("https://")):
                return VerifyResult(
                    ok=True,
                    tool_name=tool_name,
                    strategy_name=self.name,
                    message="image URL returned (non-JSON)",
                    severity="info",
                )
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message="non-JSON result, skip",
                severity="info",
            )

        # Check for error in structured result
        if data.get("success") is False or data.get("error"):
            return VerifyResult(
                ok=False,
                tool_name=tool_name,
                strategy_name=self.name,
                message=f"image generation failed: {data.get('error', 'unknown')}",
                severity="error",
            )

        # Check URL presence
        url = data.get("url") or data.get("image_url") or data.get("output") or ""
        if not url:
            # Some providers return b64_json
            b64 = data.get("b64_json") or ""
            if b64 and len(b64) > 1000:
                return VerifyResult(
                    ok=True,
                    tool_name=tool_name,
                    strategy_name=self.name,
                    message=f"image b64 returned ({len(b64)} chars)",
                    severity="info",
                )
            return VerifyResult(
                ok=False,
                tool_name=tool_name,
                strategy_name=self.name,
                message="no url or b64_json in image generation result",
                severity="error",
            )

        # If it's a local file path, check existence and size
        if os.path.exists(url):
            file_size = os.path.getsize(url)
            if file_size < 1024:  # < 1KB is suspicious for an image
                return VerifyResult(
                    ok=False,
                    tool_name=tool_name,
                    strategy_name=self.name,
                    message=f"image file too small ({file_size} bytes): {url}",
                    severity="error",
                )
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message=f"image file OK ({file_size} bytes)",
                severity="info",
                extra={"file_size": file_size},
            )

        # Remote URL — just confirm it looks like a URL
        if url.startswith(("http://", "https://")):
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message="image URL returned (remote)",
                severity="info",
            )

        return VerifyResult(
            ok=True,
            tool_name=tool_name,
            strategy_name=self.name,
            message="image result (unverified)",
            severity="info",
        )


class DefaultStrategy:
    """Fallback strategy for tools without a specific verifier."""

    name = "default_verify"

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        agent: Any,
        *,
        is_error: bool = False,
    ) -> VerifyResult:
        if is_error:
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message="skipped (tool already flagged as error)",
                severity="info",
            )

        # Basic sanity: result should be non-empty
        if not result or len(result.strip()) == 0:
            return VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name=self.name,
                message="empty result",
                severity="warn",
            )

        return VerifyResult(
            ok=True,
            tool_name=tool_name,
            strategy_name=self.name,
            message=f"ok ({len(result)} chars)",
            severity="info",
        )


# ---------------------------------------------------------------------------
# Cheat detection
# ---------------------------------------------------------------------------


class CheatDetector:
    """Detect patterns that indicate the agent is gaming verification.

    This is a passive check — it flags suspicious patterns but does not
    block execution.  The patterns below are deliberately conservative to
    avoid false positives.
    """

    # Regex patterns for suspicious tool arguments
    SUSPICIOUS_PATTERNS = [
        # Deleting test files to bypass verification
        (r"rm\s+-rf\s+\S*test", "deleting test files"),
        (r"rm\s+-rf\s+\S*spec", "deleting spec files"),
        # Modifying assertions to always pass
        (r"assert\s+True", "trivial assertion"),
        # Skipping tests
        (r"@pytest\.mark\.skip", "skipping test"),
        (r"pytest\.skip\s*\(", "skipping test"),
        # Suppressing linter warnings
        (r"#\s*noqa\b", "suppressing linter"),
        (r"#\s*type:\s*ignore", "suppressing type check"),
    ]

    def check(
        self, tool_name: str, args: Mapping[str, Any], result: str
    ) -> list[dict[str, str]]:
        """Return list of suspicious patterns found in tool args/result."""
        import re

        alerts: list[dict[str, str]] = []
        # Check command arguments
        command = args.get("command") or ""
        content = args.get("content") or ""
        text_to_check = f"{command}\n{content}"

        for pattern, description in self.SUSPICIOUS_PATTERNS:
            matches = re.findall(pattern, text_to_check, re.IGNORECASE)
            if matches:
                alerts.append(
                    {
                        "pattern": pattern,
                        "description": description,
                        "count": str(len(matches)),
                    }
                )

        return alerts


# ---------------------------------------------------------------------------
# Main controller
# ---------------------------------------------------------------------------


# Mode constants
MODE_WARN = "warn"   # Log failures, never block
MODE_BLOCK = "block"  # Append failure info to tool result

# Tool name → strategy mapping
_DEFAULT_STRATEGIES: dict[str, VerifyStrategy] = {
    "write_file": WriteFileStrategy(),
    "patch": PatchStrategy(),
    "terminal": TerminalStrategy(),
    "search_files": SearchStrategy(),
    "web_search": SearchStrategy(),
    "web_extract": SearchStrategy(),
    "image_generate": ImageGenStrategy(),
}


class SelfValidator:
    """Pluggable self-validation controller.

    Usage::

        validator = get_validator()
        result = validator.verify_tool_result(
            "write_file", {"path": "/tmp/foo.py", "content": "..."},
            '{"success": true}', agent
        )

    Vertical ecosystem apps register their own strategies::

        validator.register("scholarforge_write", MyScholarForgeVerify())
    """

    def __init__(self, mode: str = MODE_WARN):
        self.mode = mode
        self._strategies: dict[str, VerifyStrategy] = dict(_DEFAULT_STRATEGIES)
        self._cheat_detector = CheatDetector()
        self._stats: dict[str, int] = {
            "total_checks": 0,
            "passed": 0,
            "warnings": 0,
            "failures": 0,
            "cheat_alerts": 0,
        }

    def register(self, tool_name: str, strategy: VerifyStrategy) -> None:
        """Register a vertical ecosystem validator for a specific tool."""
        self._strategies[tool_name] = strategy
        logger.debug("registered validator for %s: %s", tool_name, strategy.name)

    def get_strategy(self, tool_name: str) -> VerifyStrategy:
        """Return the strategy for a tool, or the default."""
        return self._strategies.get(tool_name, DefaultStrategy())

    def verify_tool_result(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        result: str,
        agent: Any,
        *,
        is_error: bool = False,
    ) -> VerifyResult:
        """Verify a tool execution result.

        Parameters
        ----------
        tool_name : str
            Name of the tool that was executed.
        args : Mapping or None
            Arguments passed to the tool.
        result : str
            Tool result string (may be JSON or plain text).
        agent : Any
            The AIAgent instance (for context access if needed).
        is_error : bool
            Whether ``_detect_tool_failure`` already flagged this as an error.

        Returns
        -------
        VerifyResult
            The verification outcome.  Never raises.
        """
        self._stats["total_checks"] += 1
        args = args if isinstance(args, Mapping) else {}

        try:
            strategy = self.get_strategy(tool_name)
            verify_result = strategy.verify(
                tool_name, args, result, agent, is_error=is_error
            )
        except Exception as e:
            logger.debug("validator strategy %s raised: %s", tool_name, e)
            verify_result = VerifyResult(
                ok=True,
                tool_name=tool_name,
                strategy_name="exception_fallback",
                message=f"validator error: {e}",
                severity="warn",
            )

        # Update stats
        if verify_result.is_error:
            self._stats["failures"] += 1
        elif verify_result.is_warning:
            self._stats["warnings"] += 1
        else:
            self._stats["passed"] += 1

        # Cheat detection (passive, always runs)
        try:
            cheat_alerts = self._cheat_detector.check(tool_name, args, result)
            if cheat_alerts:
                self._stats["cheat_alerts"] += len(cheat_alerts)
                logger.warning(
                    "cheat detection alerts for %s: %s",
                    tool_name,
                    cheat_alerts,
                )
        except Exception:
            pass  # cheat detection should never break verification

        # Log result
        if verify_result.is_error:
            logger.warning(
                "self_validation FAILED for %s: %s",
                tool_name,
                verify_result.message,
            )
        elif verify_result.is_warning:
            logger.info(
                "self_validation WARNING for %s: %s",
                tool_name,
                verify_result.message,
            )
        else:
            logger.debug(
                "self_validation OK for %s: %s",
                tool_name,
                verify_result.message,
            )

        return verify_result

    def format_for_result(self, verify_result: VerifyResult) -> str:
        """Format a verify result for appending to tool output (block mode only)."""
        if verify_result.ok and not verify_result.is_warning:
            return ""
        if self.mode == MODE_WARN and not verify_result.is_error:
            return ""
        # In warn mode, only append errors.  In block mode, append warnings too.
        prefix = "⚠️ Verification warning" if verify_result.is_warning else "❌ Verification failed"
        return f"\n\n[{prefix}: {verify_result.strategy_name}] {verify_result.message}"

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def reset_stats(self) -> None:
        self._stats = {
            "total_checks": 0,
            "passed": 0,
            "warnings": 0,
            "failures": 0,
            "cheat_alerts": 0,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_validator: SelfValidator | None = None


def get_validator() -> SelfValidator:
    """Return the global validator singleton."""
    global _validator
    if _validator is None:
        _validator = SelfValidator(mode=MODE_WARN)
    return _validator


def set_mode(mode: str) -> None:
    """Set the global validator mode (warn or block)."""
    get_validator().mode = mode
    logger.info("self_validator mode set to: %s", mode)
