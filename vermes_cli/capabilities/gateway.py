"""P0-2 runtime capability gateway.

This is the agent-side consumer of the P0 model-capability manifest
(``vermes_cli.capabilities.manifest``). It is designed to be hooked
**INSIDE** the already-occupied ``step_callback`` (``chat.py``'s
``thinking_handler``) — NOT by replacing ``agent.step_callback``.

Per agent step it:
  * resolves the current model's capability profile from a cached
    ``models.dev`` index (O(1) dict lookup, never per-step I/O),
  * "lights up" the capabilities this step can exercise, and
  * records an ``agent:step`` capability event for observability.

Design rules (verified against current code, 2026-08-28):
  * fail-open: any error is swallowed and ``on_step`` returns ``None`` so
    the 🤔 thinking bubble is never blocked.
  * overhead target: a single ``on_step`` is pure dict lookups and must
    stay < 2 ms (real-call measured in the acceptance check).
  * no build-time tool filtering here — that is a separate concern; this
    module only observes/records capability usage per step.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .manifest import build_provider_capability_index

logger = logging.getLogger(__name__)

# Refresh the cached provider→capability index at most once per hour.
_INDEX_TTL = 3600.0


class CapabilityGateway:
    """Resolve a model's capability profile per agent step (channel-agnostic)."""

    def __init__(self) -> None:
        self._idx: Dict[str, List[str]] = {}
        self._loaded_at: float = 0.0

    def _ensure_index(self) -> None:
        now = time.time()
        if self._idx and (now - self._loaded_at) <= _INDEX_TTL:
            return
        try:
            self._idx = build_provider_capability_index()
            self._loaded_at = now
        except Exception as e:  # noqa: BLE001
            logger.debug("capability index refresh failed: %s", e)
            if not self._idx:
                self._idx = {}

    def on_step(
        self,
        iteration: int,
        prev_tools: Optional[List[Any]] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Resolve capabilities for one agent step.

        Returns a small dict (``iteration`` / ``model`` / ``provider`` /
        ``capabilities`` / ``tools_used``) or ``None`` on any failure.
        ``capabilities`` is the resolved capability-tag list for the
        current provider (``None`` when the provider is unknown/offline).
        """
        try:
            self._ensure_index()
            caps = self._idx.get(provider) if provider else None
            tools_used = [
                t.get("name")
                for t in (prev_tools or [])
                if isinstance(t, dict)
            ]
            return {
                "iteration": iteration,
                "model": model,
                "provider": provider,
                "capabilities": caps,  # light-up
                "tools_used": tools_used,  # record
            }
        except Exception as e:  # noqa: BLE001
            logger.debug("capability gateway on_step error: %s", e)
            return None


# Module-level singleton — one shared index across all channels/conversations.
CAPABILITY_GATEWAY = CapabilityGateway()
