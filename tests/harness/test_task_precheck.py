"""Tests for harness.task_precheck — H1.1 task-level pre-execution constraints."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from harness.task_precheck import (
    TaskPreCheckResult,
    check_task_constraints,
    _check_message_length,
    _check_iteration_budget,
    _check_disabled_toolsets,
)


# --------------------------------------------------------------------------- #
# Test doubles                                                                 #
# --------------------------------------------------------------------------- #


class FakeBudget:
    """Minimal IterationBudget stub."""

    def __init__(self, used: int, max_total: int):
        self.used = used
        self.max_total = max_total

    @property
    def remaining(self):
        return max(0, self.max_total - self.used)


class FakeAgent:
    """Minimal agent stub for task precheck tests."""

    def __init__(
        self,
        iteration_budget=None,
        disabled_toolsets=None,
    ):
        self.iteration_budget = iteration_budget
        self.disabled_toolsets = disabled_toolsets


# --------------------------------------------------------------------------- #
# TaskPreCheckResult dataclass                                                 #
# --------------------------------------------------------------------------- #


class TestTaskPreCheckResult:
    def test_ok_factory(self):
        r = TaskPreCheckResult.ok()
        assert r.passed is True
        assert r.warning is None
        assert r.detail == {}

    def test_warn_factory(self):
        r = TaskPreCheckResult.warn("be careful")
        assert r.passed is False
        assert r.warning == "be careful"
        assert r.detail == {}

    def test_warn_factory_with_detail(self):
        detail = {"check": "msg", "issue": "empty"}
        r = TaskPreCheckResult.warn("empty msg", detail=detail)
        assert r.passed is False
        assert r.warning == "empty msg"
        assert r.detail == detail

    def test_frozen(self):
        r = TaskPreCheckResult.ok()
        with pytest.raises(Exception):
            r.passed = False  # frozen dataclass


# --------------------------------------------------------------------------- #
# Individual checks                                                            #
# --------------------------------------------------------------------------- #


class TestCheckMessageLength:
    def test_normal_message_passes(self):
        assert _check_message_length("Hello, agent!") is None

    def test_empty_message_warns(self):
        result = _check_message_length("")
        assert result is not None
        assert "empty" in result[0].lower()

    def test_whitespace_only_message_warns(self):
        result = _check_message_length("   \n\t  ")
        assert result is not None
        assert "empty" in result[0].lower()

    def test_very_long_message_warns(self):
        result = _check_message_length("x" * 100_001)
        assert result is not None
        assert "long" in result[0].lower()
        assert result[1]["length"] == 100_001

    def test_message_at_limit_passes(self):
        assert _check_message_length("x" * 100_000) is None


class TestCheckIterationBudget:
    def test_no_budget_skips(self):
        agent = FakeAgent(iteration_budget=None)
        assert _check_iteration_budget(agent) is None

    def test_budget_remaining_passes(self):
        agent = FakeAgent(iteration_budget=FakeBudget(5, 90))
        assert _check_iteration_budget(agent) is None

    def test_budget_exhausted_warns(self):
        agent = FakeAgent(iteration_budget=FakeBudget(90, 90))
        result = _check_iteration_budget(agent)
        assert result is not None
        assert "exhausted" in result[0].lower()

    def test_budget_over_limit_warns(self):
        agent = FakeAgent(iteration_budget=FakeBudget(100, 90))
        result = _check_iteration_budget(agent)
        assert result is not None


class TestCheckDisabledToolsets:
    def test_no_disabled_toolsets_passes(self):
        agent = FakeAgent(disabled_toolsets=None)
        assert _check_disabled_toolsets(agent) is None

    def test_empty_list_passes(self):
        agent = FakeAgent(disabled_toolsets=[])
        assert _check_disabled_toolsets(agent) is None

    def test_disabled_list_warns(self):
        agent = FakeAgent(disabled_toolsets=["web_search", "browser_tool"])
        result = _check_disabled_toolsets(agent)
        assert result is not None
        assert "web_search" in result[0]
        assert "browser_tool" in result[0]

    def test_disabled_string_warns(self):
        agent = FakeAgent(disabled_toolsets="web_search")
        result = _check_disabled_toolsets(agent)
        assert result is not None
        assert "web_search" in result[0]


# --------------------------------------------------------------------------- #
# check_task_constraints (integration)                                         #
# --------------------------------------------------------------------------- #


class TestCheckTaskConstraints:
    def test_normal_message_normal_agent_passes(self):
        agent = FakeAgent(
            iteration_budget=FakeBudget(0, 90),
            disabled_toolsets=None,
        )
        result = check_task_constraints("Do something useful", agent)
        assert result.passed is True

    def test_empty_message_warns(self):
        agent = FakeAgent()
        result = check_task_constraints("", agent)
        assert result.passed is False
        assert "empty" in result.warning.lower()

    def test_very_long_message_warns(self):
        agent = FakeAgent()
        result = check_task_constraints("x" * 200_000, agent)
        assert result.passed is False
        assert "long" in result.warning.lower()

    def test_exhausted_budget_warns(self):
        agent = FakeAgent(iteration_budget=FakeBudget(90, 90))
        result = check_task_constraints("hello", agent)
        assert result.passed is False
        assert "exhausted" in result.warning.lower()

    def test_disabled_toolsets_warns(self):
        agent = FakeAgent(disabled_toolsets=["terminal_tool"])
        result = check_task_constraints("hello", agent)
        assert result.passed is False
        assert "terminal_tool" in result.warning

    def test_multiple_warnings_joined(self):
        agent = FakeAgent(
            iteration_budget=FakeBudget(90, 90),
            disabled_toolsets=["web_search"],
        )
        result = check_task_constraints("", agent)
        assert result.passed is False
        assert "empty" in result.warning.lower()
        assert "exhausted" in result.warning.lower()
        assert "web_search" in result.warning
        # Multiple warnings are newline-joined
        assert "\n" in result.warning

    def test_never_raises_on_bad_agent(self):
        """If agent is None or broken, should return ok, not raise."""
        result = check_task_constraints("hello", None)
        assert result.passed is True

    def test_never_raises_on_exception(self):
        """If any check raises, should return ok (fail-open at meta level)."""

        class BadAgent:
            @property
            def iteration_budget(self):
                raise RuntimeError("boom")

        result = check_task_constraints("hello", BadAgent())
        assert result.passed is True

    def test_detail_dict_populated(self):
        agent = FakeAgent(disabled_toolsets=["web_search"])
        result = check_task_constraints("", agent)
        assert not result.passed
        assert "message_length" in result.detail
        assert "disabled_toolsets" in result.detail

    def test_none_user_message(self):
        agent = FakeAgent()
        result = check_task_constraints(None, agent)  # type: ignore
        assert result.passed is False
        assert "empty" in result.warning.lower()

    def test_agent_without_iteration_budget(self):
        """Agent with no iteration_budget attribute should skip that check."""

        class MinimalAgent:
            pass

        result = check_task_constraints("hello", MinimalAgent())
        assert result.passed is True

    def test_agent_without_disabled_toolsets(self):
        """Agent with no disabled_toolsets attribute should skip that check."""

        class MinimalAgent:
            pass

        result = check_task_constraints("hello", MinimalAgent())
        assert result.passed is True
