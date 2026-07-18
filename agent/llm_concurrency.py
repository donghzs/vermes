"""Opt-in global in-flight limiter for LLM completion calls (P2.2).

Scenario-fit note (why this module is narrow on purpose)
--------------------------------------------------------
P2.2 originally scoped three LLM-path optimisations. Two were rejected after
auditing the real call path, because they do not fit this codebase's
constraints:

* **Connection-pool reuse across turns** — rejected. The per-request OpenAI
  client built by ``VermesAgent._create_request_openai_client`` is *deliberately*
  short-lived: it is torn down in the ``finally`` of every request
  (``chat_completion_helpers._close_request_client_once``). That teardown guards
  three separate production incidents — #10933 (a reused, already-closed httpx
  transport raising "Cannot send a request, as the client has been closed"),
  #10324 (dead-peer sockets stuck in CLOSE-WAIT, fixed with injected TCP
  keepalives), and #29507 (cross-thread ``client.close()`` corrupting unrelated
  FDs). TCP keepalive already gives us socket reuse *within* a client's life, and
  a fresh client costs sub-millisecond next to a multi-second completion. Forcing
  cross-turn pooling would re-open those crash classes for no meaningful latency
  win.
* **Identical-request response caching** — rejected. Chat completions are
  non-deterministic (temperature), stateful (tool-call loops), and streamed;
  an LRU on request payloads would return stale/incorrect turns. Provider-side
  prompt caching (Anthropic ``cache_control``, OpenRouter cache) is already wired
  and is the correct form of caching here.

What *does* fit: a **global concurrency cap**. The credential pool already caps
concurrency *per credential* (``credential_pool._max_concurrent``), but nothing
caps the *total* number of simultaneous completions when many agents fan out at
once (kanban dispatch, multi-agent gateway). That fan-out is what triggers 429
storms against a single provider. This module adds an **opt-in, process-wide**
in-flight cap for exactly that scenario.

Design
------
* **Default off.** ``limit`` of ``0`` / ``None`` → a no-op passthrough, so the
  single-interactive-agent path (the common case) is byte-for-byte unchanged.
* **Fail-open.** If the semaphore can't be acquired within ``acquire_timeout``
  seconds we log a warning and proceed anyway. An agent turn must never be
  *blocked forever* by a throttle bug — throttling is a courtesy to the
  provider, not a correctness gate.
* **Thread-based.** The completion hot path is synchronous/threaded (see
  ``chat_completion_helpers``), so this uses ``threading.BoundedSemaphore``, not
  ``asyncio``.
* **Wired at one safe choke point** — around the synchronous
  ``_interruptible_api_call`` / ``_interruptible_streaming_api_call`` invocation
  in ``agent.conversation_loop``. That site runs in the agent's own worker
  thread, *outside* the interrupt-check and stale-detector helper threads, so a
  limiter here can never nest inside them and deadlock.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger("agent.llm_concurrency")


class LLMConcurrencyLimiter:
    """A process-wide cap on concurrent LLM completions.

    ``limit <= 0`` disables the cap entirely (the acquire becomes a no-op).
    """

    def __init__(self, limit: int = 0, acquire_timeout: float = 30.0) -> None:
        self._limit = max(0, int(limit))
        self._acquire_timeout = float(acquire_timeout)
        self._sem: Optional[threading.BoundedSemaphore] = (
            threading.BoundedSemaphore(self._limit) if self._limit > 0 else None
        )

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def enabled(self) -> bool:
        return self._sem is not None

    @contextmanager
    def slot(self) -> Iterator[bool]:
        """Hold one in-flight slot for the duration of the ``with`` block.

        Yields ``True`` when a slot was actually held, ``False`` when the
        limiter is disabled or acquisition timed out (fail-open). Callers do
        not need to branch on the value — the completion proceeds either way —
        but tests and diagnostics can use it.
        """
        if self._sem is None:
            yield False
            return

        acquired = self._sem.acquire(timeout=self._acquire_timeout)
        if not acquired:
            logger.warning(
                "[LLM-CONCURRENCY] could not acquire a slot within %.1fs "
                "(limit=%d fully in use) — proceeding anyway (fail-open).",
                self._acquire_timeout,
                self._limit,
            )
            yield False
            return

        try:
            yield True
        finally:
            try:
                self._sem.release()
            except ValueError:
                # BoundedSemaphore raises if released more times than acquired;
                # never let a bookkeeping slip crash a completion.
                logger.debug("[LLM-CONCURRENCY] spurious release ignored.")


# --------------------------------------------------------------------------- #
# Module-level singleton — configured once at gateway/agent start.            #
# --------------------------------------------------------------------------- #

_limiter: LLMConcurrencyLimiter = LLMConcurrencyLimiter(limit=0)
_config_lock = threading.Lock()


def configure(limit: int = 0, acquire_timeout: float = 30.0) -> LLMConcurrencyLimiter:
    """Install the process-wide limiter. Idempotent; last call wins.

    Called from ``gateway.run`` / agent start after config is loaded. Passing
    ``limit=0`` (the default) keeps the limiter disabled.
    """
    global _limiter
    with _config_lock:
        _limiter = LLMConcurrencyLimiter(limit=limit, acquire_timeout=acquire_timeout)
        if _limiter.enabled:
            logger.info(
                "[LLM-CONCURRENCY] global completion cap enabled: limit=%d "
                "acquire_timeout=%.1fs",
                _limiter.limit,
                acquire_timeout,
            )
        return _limiter


def get_limiter() -> LLMConcurrencyLimiter:
    """Return the current process-wide limiter (disabled no-op by default)."""
    return _limiter
