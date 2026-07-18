"""Tests for agent.llm_concurrency — opt-in global LLM in-flight cap (P2.2).

The limiter is disabled by default (no-op passthrough) so the single-agent
path is unchanged; when enabled it caps process-wide concurrent completions
and fails open on acquisition timeout so a turn is never blocked forever.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from agent import llm_concurrency as lc


@pytest.fixture(autouse=True)
def _reset_limiter():
    """Each test starts (and leaves) the module singleton disabled."""
    lc.configure(limit=0)
    yield
    lc.configure(limit=0)


def test_disabled_by_default_is_noop():
    limiter = lc.LLMConcurrencyLimiter(limit=0)
    assert limiter.enabled is False
    assert limiter.limit == 0
    with limiter.slot() as held:
        # Disabled → yields False (no slot held) but still enters the block.
        assert held is False


def test_negative_limit_treated_as_disabled():
    limiter = lc.LLMConcurrencyLimiter(limit=-5)
    assert limiter.enabled is False
    with limiter.slot() as held:
        assert held is False


def test_enabled_limiter_holds_and_releases_slot():
    limiter = lc.LLMConcurrencyLimiter(limit=2)
    assert limiter.enabled is True
    with limiter.slot() as held:
        assert held is True
    # After the block the slot is released — we can re-enter limit times again.
    with limiter.slot() as held:
        assert held is True


def test_slot_is_reentrant_after_release():
    limiter = lc.LLMConcurrencyLimiter(limit=1)
    for _ in range(5):
        with limiter.slot() as held:
            assert held is True


def test_cap_blocks_third_concurrent_holder():
    """With limit=2, a 3rd concurrent slot must wait until one is released."""
    limiter = lc.LLMConcurrencyLimiter(limit=2, acquire_timeout=5.0)
    started = threading.Event()
    release_first = threading.Event()
    third_acquired = threading.Event()
    holders = []

    def _hold(idx, wait_for_release):
        with limiter.slot() as held:
            holders.append((idx, held))
            if idx == 3:
                third_acquired.set()
            started.set()
            if wait_for_release:
                release_first.wait(timeout=5.0)

    t1 = threading.Thread(target=_hold, args=(1, True))
    t2 = threading.Thread(target=_hold, args=(2, True))
    t1.start()
    t2.start()
    # Give the two holders time to grab both slots.
    time.sleep(0.2)

    t3 = threading.Thread(target=_hold, args=(3, False))
    t3.start()
    # The third holder must NOT have acquired yet — both slots are taken.
    assert not third_acquired.wait(timeout=0.4)

    # Release the two holders; now the third can proceed.
    release_first.set()
    assert third_acquired.wait(timeout=3.0)

    for t in (t1, t2, t3):
        t.join(timeout=3.0)
    # All three eventually held a real slot.
    assert sorted(h[0] for h in holders) == [1, 2, 3]
    assert all(h[1] is True for h in holders)


def test_fail_open_on_acquire_timeout(caplog):
    """When all slots are busy past the timeout, slot() yields False and the
    caller still proceeds (fail-open) rather than blocking forever."""
    limiter = lc.LLMConcurrencyLimiter(limit=1, acquire_timeout=0.2)
    hold_open = threading.Event()

    def _occupy():
        with limiter.slot() as held:
            assert held is True
            hold_open.wait(timeout=5.0)

    occupier = threading.Thread(target=_occupy)
    occupier.start()
    time.sleep(0.1)  # let the occupier grab the only slot

    caplog.set_level(logging.WARNING, logger="agent.llm_concurrency")
    entered = False
    with limiter.slot() as held:
        entered = True
        assert held is False  # timed out → fail-open
    assert entered is True
    assert any(
        "could not acquire a slot" in r.getMessage() for r in caplog.records
    )

    hold_open.set()
    occupier.join(timeout=3.0)


def test_exception_in_block_still_releases_slot():
    limiter = lc.LLMConcurrencyLimiter(limit=1)
    with pytest.raises(RuntimeError):
        with limiter.slot() as held:
            assert held is True
            raise RuntimeError("boom")
    # Slot must have been released despite the exception.
    with limiter.slot() as held:
        assert held is True


def test_configure_installs_singleton():
    lc.configure(limit=4, acquire_timeout=12.0)
    limiter = lc.get_limiter()
    assert limiter.enabled is True
    assert limiter.limit == 4


def test_configure_zero_disables_singleton():
    lc.configure(limit=3)
    assert lc.get_limiter().enabled is True
    lc.configure(limit=0)
    assert lc.get_limiter().enabled is False


def test_configure_logs_when_enabled(caplog):
    caplog.set_level(logging.INFO, logger="agent.llm_concurrency")
    lc.configure(limit=2, acquire_timeout=7.0)
    assert any(
        "global completion cap enabled" in r.getMessage() for r in caplog.records
    )
