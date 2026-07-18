"""H4.1 — Failure classification learning (persistent failure patterns).

This module persists failure patterns observed during tool execution so
that the agent can learn from recurring failures across sessions. It
builds on the existing ``classify_failure`` from ``harness/recoverable.py``
by recording each classified failure to a JSON ledger on disk.

Design principles
-----------------
* **Fail-open**: any IO exception is silently swallowed — the main tool
  execution path is never affected.
* **Atomic writes**: the ledger is written to a temp file first, then
  atomically renamed — no corruption on crash.
* **Bounded**: maximum 1000 records (FIFO eviction) to prevent unbounded
  growth.
* **Thread-safe**: all public methods are guarded by a lock.

Persistence
-----------
Records are stored in ``~/.vermes/harness/failure_patterns.json``.

Wiring
------
- ``FailureLedger.record()`` is called after each ``classify_failure``
  call in ``agent/tool_executor.py``.
- ``FailureLedger.should_warn()`` is called before H2.1 ``run_precheck``
  to append historical failure warnings.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("harness.failure_learning")


# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

_DEFAULT_LEDGER_DIR = Path.home() / ".vermes" / "harness"
_DEFAULT_LEDGER_FILE = _DEFAULT_LEDGER_DIR / "failure_patterns.json"
_MAX_RECORDS = 1000


# --------------------------------------------------------------------------- #
# Data classes                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class FailurePattern:
    """A single failure pattern record.

    Attributes
    ----------
    pattern_str
        The error type / classification (e.g. "network_error").
    tool_name
        The tool that failed.
    count
        How many times this pattern has been seen.
    last_seen
        Unix timestamp of the most recent occurrence.
    examples
        Up to 3 example error messages.
    """

    pattern_str: str
    tool_name: str
    count: int = 1
    last_seen: float = field(default_factory=time.time)
    examples: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "FailurePattern":
        return FailurePattern(
            pattern_str=data.get("pattern_str", ""),
            tool_name=data.get("tool_name", ""),
            count=data.get("count", 1),
            last_seen=data.get("last_seen", time.time()),
            examples=data.get("examples", []),
        )


# --------------------------------------------------------------------------- #
# FailureLedger                                                                #
# --------------------------------------------------------------------------- #


class FailureLedger:
    """Persistent ledger of failure patterns.

    Thread-safe. Fail-open on all IO operations.

    Usage::

        ledger = FailureLedger()
        ledger.record("web_search", RuntimeError("timeout"), {"query": "test"})
        warning = ledger.should_warn("web_search")
        if warning:
            print(warning)
    """

    def __init__(
        self,
        ledger_path: Optional[Path] = None,
        max_records: int = _MAX_RECORDS,
    ):
        self._path = ledger_path or _DEFAULT_LEDGER_FILE
        self._max_records = max_records
        self._lock = threading.Lock()
        self._patterns: Dict[str, FailurePattern] = {}
        self._record_count = 0
        self._loaded = False

    # ------------------------------------------------------------------ #
    # Persistence                                                        #
    # ------------------------------------------------------------------ #

    def _ensure_loaded(self) -> None:
        """Lazily load the ledger from disk on first access."""
        if self._loaded:
            return
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw_patterns = data.get("patterns", [])
                self._record_count = data.get("record_count", 0)
                for entry in raw_patterns:
                    if isinstance(entry, dict):
                        fp = FailurePattern.from_dict(entry)
                        key = self._key(fp.tool_name, fp.pattern_str)
                        self._patterns[key] = fp
        except Exception as exc:
            logger.debug("failed to load failure ledger (fail-open): %s", exc)
        finally:
            self._loaded = True

    def _save(self) -> None:
        """Atomically write the ledger to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "patterns": [fp.to_dict() for fp in self._patterns.values()],
                "record_count": self._record_count,
            }
            # Write to temp file first, then atomically rename.
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._path.parent),
                suffix=".tmp",
                prefix=".failure_patterns_",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as exc:
            logger.debug("failed to save failure ledger (fail-open): %s", exc)

    @staticmethod
    def _key(tool_name: str, pattern_str: str) -> str:
        return f"{tool_name}::{pattern_str}"

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def record(
        self,
        tool_name: str,
        error: BaseException | str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a failure occurrence.

        Parameters
        ----------
        tool_name
            The tool that failed.
        error
            The exception or error message.
        context
            Optional context dict (e.g. tool args) for debugging.
        """
        try:
            # Classify the failure using the existing classify_failure.
            from .recoverable import classify_failure

            if isinstance(error, BaseException):
                pattern_str, _cause = classify_failure(error)
                error_msg = str(error)
            else:
                pattern_str = "unknown"
                error_msg = str(error)

            with self._lock:
                self._ensure_loaded()
                key = self._key(tool_name, pattern_str)

                if key in self._patterns:
                    fp = self._patterns[key]
                    fp.count += 1
                    fp.last_seen = time.time()
                    if len(fp.examples) < 3 and error_msg:
                        fp.examples.append(error_msg[:200])
                else:
                    self._patterns[key] = FailurePattern(
                        pattern_str=pattern_str,
                        tool_name=tool_name,
                        count=1,
                        last_seen=time.time(),
                        examples=[error_msg[:200]] if error_msg else [],
                    )
                    self._record_count += 1

                # FIFO eviction: if over max, remove oldest patterns.
                while len(self._patterns) > self._max_records:
                    oldest_key = min(
                        self._patterns,
                        key=lambda k: self._patterns[k].last_seen,
                    )
                    del self._patterns[oldest_key]

                self._save()
        except Exception as exc:
            logger.debug("failure ledger record raised (fail-open): %s", exc)

    def get_patterns(self, tool_name: Optional[str] = None) -> List[FailurePattern]:
        """Query recorded failure patterns.

        Parameters
        ----------
        tool_name
            If provided, only return patterns for this tool.
            If None, return all patterns.

        Returns
        -------
        List[FailurePattern]
            List of failure patterns, sorted by count (descending).
        """
        try:
            with self._lock:
                self._ensure_loaded()
                patterns = list(self._patterns.values())
                if tool_name is not None:
                    patterns = [p for p in patterns if p.tool_name == tool_name]
                patterns.sort(key=lambda p: p.count, reverse=True)
                return patterns
        except Exception as exc:
            logger.debug("failure ledger get_patterns raised (fail-open): %s", exc)
            return []

    def should_warn(self, tool_name: str) -> Optional[str]:
        """Return a warning message if this tool has recurring failures.

        Returns None if no patterns exist or the pattern is infrequent.
        Returns a warning string if the tool has failed 3+ times with
        the same pattern.

        Parameters
        ----------
        tool_name
            The tool to check for historical failure patterns.
        """
        try:
            patterns = self.get_patterns(tool_name)
            if not patterns:
                return None

            # Only warn for patterns that have occurred 3+ times.
            recurring = [p for p in patterns if p.count >= 3]
            if not recurring:
                return None

            parts = []
            for p in recurring:
                parts.append(
                    f"'{p.pattern_str}' ({p.count} occurrences, "
                    f"last: {time.strftime('%Y-%m-%d', time.localtime(p.last_seen))})"
                )
            return (
                f"[harness learning] tool '{tool_name}' has recurring failures: "
                + "; ".join(parts)
                + ". Consider checking tool configuration or using an alternative."
            )
        except Exception as exc:
            logger.debug("failure ledger should_warn raised (fail-open): %s", exc)
            return None

    def clear(self) -> None:
        """Clear all recorded patterns (for testing)."""
        try:
            with self._lock:
                self._patterns.clear()
                self._record_count = 0
                self._save()
        except Exception as exc:
            logger.debug("failure ledger clear raised (fail-open): %s", exc)


# --------------------------------------------------------------------------- #
# Module-level singleton (lazy)                                                #
# --------------------------------------------------------------------------- #

_ledger: Optional[FailureLedger] = None
_ledger_lock = threading.Lock()


def get_ledger() -> FailureLedger:
    """Get the module-level FailureLedger singleton (lazy init)."""
    global _ledger
    if _ledger is None:
        with _ledger_lock:
            if _ledger is None:
                _ledger = FailureLedger()
    return _ledger


__all__ = [
    "FailurePattern",
    "FailureLedger",
    "get_ledger",
]
