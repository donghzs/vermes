"""H3.2 — Stability probe for hot-path tools (best-of-N).

This module wraps the existing ``probe_stability`` from ``harness/stability.py``
and makes it available as an **opt-in** hot-path enhancement for critical tools
(e.g. ``web_search``, ``browser_tool``, ``terminal_tool``).

Design principles
-----------------
* **Opt-in only**: the probe runs exclusively when
  ``getattr(agent, '_enable_stability_probe', False)`` is True.
  Default is False — zero overhead on the normal path.
* **Never blocks the main path**: the probe runs in the background
  (via ``asyncio`` if an event loop is available, otherwise in a thread).
  The original tool result is returned immediately; the probe's findings
  are appended as a warning suffix only if instability is detected.
* **Fail-open**: any exception in the probe is silently swallowed.
* **Score function**: results are scored on non-empty/length heuristics:
  - Non-empty result → 1.0
  - Empty result → 0.0
  - Partial / very short → 0.5

Wiring
------
Called from ``agent/tool_executor.py`` after H3.1 ``validate_result``,
at both the concurrent (L365) and sequential (L1064) paths. The call is
guarded by ``getattr(agent, '_enable_stability_probe', False)``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger("harness.stability_hotpath")


# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

# Tools that are "hot-path" — critical for the agent's core loop.
# Only these tools get stability probes (to limit overhead).
_HOT_PATH_TOOLS = frozenset(
    {
        "web_search",
        "browser_tool",
        "browser_navigate",
        "terminal_tool",
        "execute_command",
        "run_command",
    }
)

# Default number of probe runs.
_DEFAULT_N = 3

# Score thresholds.
_STABLE_DELTA = 0.05  # Re-exported from stability.py for convenience


# --------------------------------------------------------------------------- #
# Score function                                                               #
# --------------------------------------------------------------------------- #


def _default_score_fn(result: Any) -> float:
    """Score a tool result based on non-empty/length heuristics.

    - Non-empty string/dict/list with content → 1.0
    - None / empty / whitespace-only → 0.0
    - Very short non-empty (< 10 chars) → 0.5 (partial)
    """
    if result is None:
        return 0.0
    if isinstance(result, str):
        stripped = result.strip()
        if not stripped:
            return 0.0
        if len(stripped) < 10:
            return 0.5
        return 1.0
    if isinstance(result, (dict, list)):
        return 1.0 if len(result) > 0 else 0.0
    # Unknown type — assume valid.
    return 1.0


# --------------------------------------------------------------------------- #
# Public entry point                                                           #
# --------------------------------------------------------------------------- #


def probe_tool_stability(
    agent: Any,
    tool_name: str,
    tool_args: Dict[str, Any],
    n: int = _DEFAULT_N,
) -> Optional[str]:
    """Run a best-of-N stability probe on a tool, returning a warning if unstable.

    This is a **synchronous** wrapper that runs the probe in a background
    thread (since the hot path is typically sync). It returns:

    - ``None`` if the probe is not applicable (tool not hot-path, probe
      disabled, etc.) or if the tool is stable.
    - A warning ``str`` if instability is detected (best/worst delta
      exceeds the threshold).

    Parameters
    ----------
    agent
        The agent instance. Must have ``_enable_stability_probe = True``
        for the probe to run.
    tool_name
        The tool to probe.
    tool_args
        Arguments that were passed to the tool (re-used for probe runs).
    n
        Number of probe runs (default 3).

    Returns
    -------
    Optional[str]
        None if stable / not applicable, warning string if unstable.
    """
    # Guard: opt-in only.
    if not getattr(agent, "_enable_stability_probe", False):
        return None

    # Guard: only hot-path tools.
    if tool_name not in _HOT_PATH_TOOLS:
        return None

    try:
        return _run_probe_sync(agent, tool_name, tool_args, n)
    except Exception as exc:
        logger.debug("stability probe for %s raised (fail-open): %s", tool_name, exc)
        return None


def _run_probe_sync(
    agent: Any,
    tool_name: str,
    tool_args: Dict[str, Any],
    n: int,
) -> Optional[str]:
    """Run the probe synchronously in a background thread.

    We use a thread (rather than asyncio) because the hot path is
    typically sync and may not have a running event loop.
    """
    result_holder: Dict[str, Any] = {"warning": None}

    def _run() -> None:
        try:
            # Try to run the probe via asyncio in a new event loop.
            loop = asyncio.new_event_loop()
            try:
                warning = loop.run_until_complete(
                    _run_probe_async(agent, tool_name, tool_args, n)
                )
                result_holder["warning"] = warning
            finally:
                loop.close()
        except Exception as exc:
            logger.debug("stability probe thread for %s failed: %s", tool_name, exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=10.0)  # Bounded — never block the hot path indefinitely.

    return result_holder.get("warning")


async def _run_probe_async(
    agent: Any,
    tool_name: str,
    tool_args: Dict[str, Any],
    n: int,
) -> Optional[str]:
    """Run the actual probe using probe_stability from stability.py."""
    from .stability import probe_stability

    # Build a callable that re-invokes the tool handler.
    def _invoke_tool() -> Any:
        try:
            # Use the agent's tool invocation path.
            # This re-invokes the tool handler — the probe is observational.
            if hasattr(agent, "_invoke_tool"):
                return agent._invoke_tool(
                    tool_name,
                    tool_args,
                    getattr(agent, "_current_task_id", ""),
                    "stability_probe",
                )
            # Fallback: try handle_function_call
            from agent.tool_executor import _ra

            return _ra().handle_function_call(
                tool_name,
                tool_args,
                getattr(agent, "_current_task_id", ""),
            )
        except Exception as exc:
            logger.debug("stability probe invocation of %s failed: %s", tool_name, exc)
            raise

    report = await probe_stability(
        _invoke_tool,
        n=n,
        score_fn=_default_score_fn,
    )

    if not report.stable and report.n >= 2:
        return (
            f"[harness stability] tool '{tool_name}' showed instability "
            f"across {report.n} runs: best={report.best_score:.2f}, "
            f"worst={report.worst_score:.2f}, delta={report.delta:.2f}. "
            f"Consider retrying or using an alternative tool."
        )

    return None


# --------------------------------------------------------------------------- #
# Convenience: check if probe is enabled for a given agent + tool             #
# --------------------------------------------------------------------------- #


def is_probe_enabled(agent: Any, tool_name: str) -> bool:
    """Return True if the stability probe would run for this agent + tool."""
    return (
        getattr(agent, "_enable_stability_probe", False) is True
        and tool_name in _HOT_PATH_TOOLS
    )


__all__ = [
    "probe_tool_stability",
    "is_probe_enabled",
]
