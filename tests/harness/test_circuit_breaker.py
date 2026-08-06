"""P3.5 — Circuit breaker focused tests.

Covers: circuit_open / max_attempts_for decision, retry-skip integration with
invoke_with_retry, threshold (<3 no open), config off, R6 builtin also opens,
fail-open on ledger error, constants. Reverse-verification (R5 antidote): this
file copied onto commit 804413cae (no harness/circuit_breaker.py) MUST fail on
import.
"""
import logging

import pytest

from harness.circuit_breaker import (
    CB_PREFIX,
    CircuitBreakerConfig,
    circuit_open,
    circuit_prefix,
    max_attempts_for,
)
from harness.failure_learning import FailureLedger, get_ledger
from harness.recoverable import _BUILTIN_NO_RETRY, invoke_with_retry


@pytest.fixture
def tmp_ledger(tmp_path, monkeypatch):
    """Isolated ledger + breaker enabled, so tests never touch the real
    ~/.vermes/harness/failure_patterns.json or the real config.yaml."""
    ledger = FailureLedger(ledger_path=tmp_path / "fp.json")
    monkeypatch.setattr("harness.failure_learning.get_ledger", lambda: ledger)
    monkeypatch.setattr(
        "harness.circuit_breaker.load_circuit_breaker_config",
        lambda: CircuitBreakerConfig(enabled=True, action="skip_retry"),
    )
    return ledger


def _record_n(ledger, tool, n=3, exc=None):
    exc = exc or ConnectionError("boom")
    for _ in range(n):
        ledger.record(tool, exc)


# --- decision ------------------------------------------------------------- #

def test_circuit_open_true_after_3_recurring(tmp_ledger):
    _record_n(tmp_ledger, "web_search", 3)
    assert circuit_open("web_search") is True
    assert max_attempts_for("web_search") == 1
    # different tool, no history -> not open
    assert circuit_open("other_tool") is False
    assert max_attempts_for("other_tool") == 2


def test_not_open_below_threshold(tmp_ledger):
    _record_n(tmp_ledger, "web_search", 2)  # < 3
    assert circuit_open("web_search") is False
    assert max_attempts_for("web_search") == 2


# --- retry-skip integration (the core P3.5 effect) ------------------------- #

def test_skip_retry_invokes_once(tmp_ledger, caplog):
    _record_n(tmp_ledger, "web_search", 3)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise ConnectionError("net down")

    with caplog.at_level(logging.WARNING, logger="harness.recoverable"):
        with pytest.raises(ConnectionError):
            invoke_with_retry(
                flaky, "web_search", max_attempts=max_attempts_for("web_search")
            )
    assert calls["n"] == 1  # circuit-open => no retry
    assert not any("retry" in r.message for r in caplog.records)


def test_no_circuit_retries_once(tmp_ledger, caplog):
    # empty ledger -> not open -> P3 retry still applies (1 retry = 2 calls)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise ConnectionError("net down")

    with caplog.at_level(logging.WARNING, logger="harness.recoverable"):
        with pytest.raises(ConnectionError):
            invoke_with_retry(
                flaky, "web_search", max_attempts=max_attempts_for("web_search")
            )
    assert calls["n"] == 2  # normal P3 retry path
    assert any("retry" in r.message for r in caplog.records)


# --- config off (fail-open to no-op) -------------------------------------- #

def test_config_disabled_no_open(tmp_ledger, monkeypatch):
    _record_n(tmp_ledger, "web_search", 3)
    monkeypatch.setattr(
        "harness.circuit_breaker.load_circuit_breaker_config",
        lambda: CircuitBreakerConfig(enabled=False),
    )
    assert circuit_open("web_search") is False
    assert max_attempts_for("web_search") == 2


# --- R6 builtin tools also open (recommended) ----------------------------- #

def test_builtin_also_opens(tmp_ledger):
    _record_n(tmp_ledger, "memory", 3)
    assert "memory" in _BUILTIN_NO_RETRY
    assert circuit_open("memory") is True  # builtin also breaker-opens


# --- fail-open on ledger error -------------------------------------------- #

def test_fail_open_on_ledger_error(tmp_ledger, monkeypatch):
    def boom(name):
        raise RuntimeError("ledger broken")

    monkeypatch.setattr(tmp_ledger, "should_warn", boom)
    assert circuit_open("web_search") is False  # never raises, degrades
    assert max_attempts_for("web_search") == 2


# --- constants ------------------------------------------------------------- #

def test_constants():
    assert CB_PREFIX == "[circuit-breaker]"
    assert circuit_prefix() == "[circuit-breaker]"


def test_default_config_enabled():
    assert CircuitBreakerConfig().enabled is True
    assert CircuitBreakerConfig().action == "skip_retry"
