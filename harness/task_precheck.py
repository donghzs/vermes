"""H1.1 — Task pre-execution constraints (conversation loop entry gate).

Scenario-fit note
-----------------
This module implements the first layer of the Agent × Harness × Task
roadmap's H1.1: **task-level** pre-execution constraints that run at the
``run_conversation`` entry point — *before* any tool is dispatched, before
the model is even called.

It complements H2.1 (tool-level pre-checks) by catching task-level issues:
prompt-bomb messages, exhausted iteration budgets, disabled toolsets, etc.

Design principles
-----------------
* **Fail-open** (consistent with P2.2 / P2.4 design philosophy): any
  exception returns ``ok()`` — this module never raises.
* **Never blocks**: all results are ``warning`` level at most. The
  conversation proceeds regardless. Warnings are surfaced via
  ``agent._emit_status()`` for visibility, but execution is never denied.
* **Informational**: disabled-toolset detection is purely informational —
  it warns the user but doesn't prevent the turn.
* **Additive**: if this module is unavailable, the conversation loop is
  byte-for-byte unchanged.

Wiring
------
Called from ``agent/conversation_loop.py`` at the top of ``run_conversation``,
after ``agent._ensure_db_session()`` and before ``set_session_context``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional

logger = logging.getLogger("harness.task_precheck")


# --------------------------------------------------------------------------- #
# Result type                                                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TaskPreCheckResult:
    """Outcome of a task-level pre-execution check.

    Attributes
    ----------
    passed
        True if no issues were found.
    warning
        Human-readable warning message(s), if any. None when ``passed``
        is True. Multiple warnings are joined with ``\\n``.
    detail
        Machine-readable detail dict for logging / debugging.
    """

    passed: bool
    warning: Optional[str] = None
    detail: dict = field(default_factory=dict)

    @staticmethod
    def ok() -> "TaskPreCheckResult":
        return TaskPreCheckResult(passed=True)

    @staticmethod
    def warn(message: str, detail: Optional[dict] = None) -> "TaskPreCheckResult":
        return TaskPreCheckResult(passed=False, warning=message, detail=detail or {})


# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

# Maximum user message length — prevents prompt-bomb attacks.
_MAX_MESSAGE_LENGTH = 100_000


# --------------------------------------------------------------------------- #
# Individual checks                                                            #
# --------------------------------------------------------------------------- #


def _check_message_length(user_message: str) -> Optional[tuple[str, dict]]:
    """Check that the user message is non-empty and not excessively long."""
    if not user_message or not user_message.strip():
        return (
            "User message is empty — the agent will receive no task content.",
            {"check": "message_length", "issue": "empty"},
        )
    if len(user_message) > _MAX_MESSAGE_LENGTH:
        return (
            f"User message is very long ({len(user_message)} chars, "
            f"max {_MAX_MESSAGE_LENGTH}) — this may cause performance issues "
            f"or exceed context limits.",
            {
                "check": "message_length",
                "issue": "too_long",
                "length": len(user_message),
                "max": _MAX_MESSAGE_LENGTH,
            },
        )
    return None


def _check_iteration_budget(agent: Any) -> Optional[tuple[str, dict]]:
    """Check if the agent's iteration budget is already exhausted."""
    budget = getattr(agent, "iteration_budget", None)
    if budget is None:
        return None  # No budget tracking — skip
    try:
        remaining = budget.remaining
        if remaining <= 0:
            return (
                f"Iteration budget is exhausted ({budget.used}/{budget.max_total} "
                "used) — the agent may not be able to complete any tool calls.",
                {
                    "check": "iteration_budget",
                    "issue": "exhausted",
                    "used": budget.used,
                    "max": budget.max_total,
                },
            )
    except Exception:
        # Budget object doesn't have expected interface — skip silently.
        pass
    return None


def _check_disabled_toolsets(agent: Any) -> Optional[tuple[str, dict]]:
    """Informational check: report if any toolsets are disabled."""
    disabled = getattr(agent, "disabled_toolsets", None)
    if disabled:
        # ``disabled`` could be a list or a string; normalise to list.
        if isinstance(disabled, str):
            disabled_list: List[str] = [disabled]
        else:
            try:
                disabled_list = list(disabled)
            except Exception:
                disabled_list = []
        if disabled_list:
            return (
                f"Toolsets disabled for this session: {', '.join(disabled_list)}. "
                "Some tools may be unavailable.",
                {
                    "check": "disabled_toolsets",
                    "issue": "has_disabled",
                    "toolsets": disabled_list,
                },
            )
    return None


# --------------------------------------------------------------------------- #
# Public entry point                                                           #
# --------------------------------------------------------------------------- #


def check_task_constraints(user_message: str, agent: Any) -> TaskPreCheckResult:
    """Run all task-level pre-execution checks.

    Returns ``TaskPreCheckResult.ok()`` when everything is fine.
    Returns ``TaskPreCheckResult.warn(...)`` when one or more checks
    find issues — warnings are joined with ``\\n``.

    This function **never raises** — any exception is caught and treated
    as a pass (fail-open at the meta level). It also **never blocks**:
    the result is always advisory, never a deny.
    """
    try:
        warnings: List[str] = []
        details: dict = {}

        # a. Message length constraint (prompt-bomb defence)
        msg_result = _check_message_length(user_message or "")
        if msg_result is not None:
            warnings.append(msg_result[0])
            details[msg_result[1]["check"]] = msg_result[1]

        # b. Iteration budget constraint
        budget_result = _check_iteration_budget(agent)
        if budget_result is not None:
            warnings.append(budget_result[0])
            details[budget_result[1]["check"]] = budget_result[1]

        # c. Disabled toolsets check (informational)
        toolset_result = _check_disabled_toolsets(agent)
        if toolset_result is not None:
            warnings.append(toolset_result[0])
            details[toolset_result[1]["check"]] = toolset_result[1]

        if warnings:
            return TaskPreCheckResult.warn(
                "\n".join(warnings),
                detail=details,
            )
        return TaskPreCheckResult.ok()

    except Exception as exc:
        logger.debug("task pre-check raised (fail-open): %s", exc)
        return TaskPreCheckResult.ok()


__all__ = ["TaskPreCheckResult", "check_task_constraints"]
