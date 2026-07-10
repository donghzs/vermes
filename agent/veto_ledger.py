"""Veto ledger — consecutive failure tracking with auto-pause.

Tracks consecutive vetoes (rejections) per tool+args signature. When
the same tool with the same arguments is vetoed ``THRESHOLD`` times in
a row, the ledger signals a pause condition.

Design principles
-----------------
1. **纯数据** — This module only records and reports. It does NOT
   pause the agent itself. The caller (tool_guardrails or
   conversation_loop) decides what to do with the signal.
2. **成功即重置** — A single success resets the counter for that
   tool+args signature.
3. **可序列化** — Internal state is JSON-serializable for checkpointing.
4. **零依赖** — Stdlib only.

Integration point
-----------------
In ``tool_guardrails.py``'s ``after_call()`` method::

    from agent.veto_ledger import get_veto_ledger
    ledger = get_veto_ledger()

    if failed:
        veto = ledger.record_veto(tool_name, signature)
        if veto.should_pause:
            # return a halt decision
    else:
        ledger.record_success(tool_name, signature)
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("vermes.veto_ledger")

# Default threshold: 3 consecutive vetoes → pause signal
DEFAULT_VETO_THRESHOLD = 3

# Maximum entries to prevent unbounded memory growth
MAX_ENTRIES = 200


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VetoEntry:
    """A single veto record."""

    tool_name: str
    args_hash: str  # canonical args hash
    count: int
    last_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "args_hash": self.args_hash,
            "count": self.count,
            "last_message": self.last_message,
        }


@dataclass(frozen=True)
class VetoDecision:
    """Result of recording a veto."""

    should_pause: bool
    tool_name: str
    count: int
    threshold: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_pause": self.should_pause,
            "tool_name": self.tool_name,
            "count": self.count,
            "threshold": self.threshold,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# VetoLedger
# ---------------------------------------------------------------------------


class VetoLedger:
    """Tracks consecutive vetoes per tool+args signature.

    Thread-safe. Singleton via :func:`get_veto_ledger`.
    """

    def __init__(
        self,
        threshold: int = DEFAULT_VETO_THRESHOLD,
        max_entries: int = MAX_ENTRIES,
    ) -> None:
        self._threshold = threshold
        self._max_entries = max_entries
        self._vetoes: dict[str, VetoEntry] = {}
        self._lock = threading.Lock()

    @property
    def threshold(self) -> int:
        return self._threshold

    def _make_key(self, tool_name: str, args_hash: str) -> str:
        return f"{tool_name}:{args_hash}"

    def record_veto(
        self,
        tool_name: str,
        args_hash: str,
        message: str = "",
    ) -> VetoDecision:
        """Record a veto (failure/rejection) for a tool call.

        Parameters
        ----------
        tool_name : str
            Name of the tool that was vetoed.
        args_hash : str
            Canonical hash of the tool arguments (from
            ``ToolCallSignature.args_hash``).
        message : str
            Optional error message from the veto.

        Returns
        -------
        VetoDecision
            Decision indicating whether the agent should pause.
        """
        key = self._make_key(tool_name, args_hash)

        with self._lock:
            existing = self._vetoes.get(key)
            count = (existing.count + 1) if existing else 1

            entry = VetoEntry(
                tool_name=tool_name,
                args_hash=args_hash,
                count=count,
                last_message=message,
            )
            self._vetoes[key] = entry

            # Evict oldest entries if over limit
            if len(self._vetoes) > self._max_entries:
                # Remove entry with lowest count (least likely to be a problem)
                min_key = min(self._vetoes, key=lambda k: self._vetoes[k].count)
                if min_key != key:
                    del self._vetoes[min_key]

            should_pause = count >= self._threshold
            pause_msg = ""
            if should_pause:
                pause_msg = (
                    f"Tool '{tool_name}' has been vetoed {count} consecutive "
                    f"times with the same arguments. Consider changing strategy "
                    f"or explaining the blocker."
                )
                logger.warning("veto threshold reached: %s (%d/%d)", key, count, self._threshold)

            return VetoDecision(
                should_pause=should_pause,
                tool_name=tool_name,
                count=count,
                threshold=self._threshold,
                message=pause_msg,
            )

    def record_success(self, tool_name: str, args_hash: str) -> None:
        """Record a successful execution, resetting the veto counter.

        Parameters
        ----------
        tool_name : str
            Name of the tool that succeeded.
        args_hash : str
            Canonical hash of the tool arguments.
        """
        key = self._make_key(tool_name, args_hash)

        with self._lock:
            self._vetoes.pop(key, None)

    def reset(self, tool_name: str | None = None) -> None:
        """Reset veto counters.

        Parameters
        ----------
        tool_name : str, optional
            If provided, only reset counters for this tool.
            If None, reset all counters.
        """
        with self._lock:
            if tool_name is None:
                self._vetoes.clear()
            else:
                keys_to_remove = [
                    k for k in self._vetoes if self._vetoes[k].tool_name == tool_name
                ]
                for k in keys_to_remove:
                    del self._vetoes[k]

    def get_count(self, tool_name: str, args_hash: str) -> int:
        """Get the current consecutive veto count for a signature."""
        key = self._make_key(tool_name, args_hash)
        with self._lock:
            entry = self._vetoes.get(key)
            return entry.count if entry else 0

    def get_all_vetoes(self) -> list[VetoEntry]:
        """Return all active veto entries (snapshot)."""
        with self._lock:
            return list(self._vetoes.values())

    def to_dict(self) -> dict[str, Any]:
        """Serialize state for checkpointing."""
        with self._lock:
            return {
                "threshold": self._threshold,
                "max_entries": self._max_entries,
                "vetoes": {k: v.to_dict() for k, v in self._vetoes.items()},
            }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VetoLedger":
        """Deserialize state from a checkpoint."""
        ledger = cls(
            threshold=data.get("threshold", DEFAULT_VETO_THRESHOLD),
            max_entries=data.get("max_entries", MAX_ENTRIES),
        )
        for key, entry_data in data.get("vetoes", {}).items():
            ledger._vetoes[key] = VetoEntry(
                tool_name=entry_data["tool_name"],
                args_hash=entry_data["args_hash"],
                count=entry_data["count"],
                last_message=entry_data.get("last_message", ""),
            )
        return ledger


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_ledger: VetoLedger | None = None
_ledger_lock = threading.Lock()


def get_veto_ledger() -> VetoLedger:
    """Get the global VetoLedger singleton."""
    global _ledger
    if _ledger is None:
        with _ledger_lock:
            if _ledger is None:
                _ledger = VetoLedger()
    return _ledger


def reset_veto_ledger() -> None:
    """Reset the global singleton (mainly for testing)."""
    global _ledger
    with _ledger_lock:
        _ledger = None
