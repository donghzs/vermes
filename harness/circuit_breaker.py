"""P3.5 — Circuit breaker: turn historical failure warnings into execution decisions.

Builds on the existing ``FailureLedger`` (``harness.failure_learning``). When a
tool has recurring failures (>= threshold via ``should_warn``, default 3), the
breaker:

* **skips P3 retry** — history says retries will also fail, so retrying just
  wastes blocking time (``max_attempts`` becomes 1).
* **strengthens the LLM-facing warning** — the injected hint gets a
  ``[circuit-breaker]`` prefix so the agent more seriously considers alternatives.

This is deliberately *soft* (not a hard "block"): the tool still runs once, so a
tool that has since recovered is never wrongly killed. A hard "block" action is
left for v2.

Fail-open everywhere: any error (config load, ledger access, import) degrades to
"no breaker" — the tool runs exactly as before P3.5.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("harness.circuit_breaker")

# --------------------------------------------------------------------------- #
# Config                                                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """Circuit breaker configuration.

    Attributes
    ----------
    enabled
        Master switch. When False, the breaker is a no-op (tool runs as before).
    action
        What to do when a tool is circuit-open. v1 only supports ``"skip_retry"``;
        unknown values degrade to no-op (fail-open). A future ``"block"`` action
        would skip the tool call entirely.
    """

    enabled: bool = True
    action: str = "skip_retry"


# Process-level cache: config.yaml is tiny but we avoid re-reading on every tool
# call. Cache is keyed on the file's mtime so an edit takes effect on next call.
_CONFIG: Optional[CircuitBreakerConfig] = None
_CONFIG_MTIME: float = -1.0
_CONFIG_LOCK = threading.Lock()


def load_circuit_breaker_config() -> CircuitBreakerConfig:
    """Read ``circuit_breaker:`` from ``~/.vermes/config.yaml``.

    Fail-open: on any error (missing file, bad YAML, missing deps) returns the
    default config (enabled, skip_retry).
    """
    global _CONFIG, _CONFIG_MTIME
    try:
        from pathlib import Path

        cfg_path = Path.home() / ".vermes" / "config.yaml"
        try:
            mtime = cfg_path.stat().st_mtime if cfg_path.exists() else 0.0
        except OSError:
            mtime = 0.0

        with _CONFIG_LOCK:
            if _CONFIG is not None and mtime == _CONFIG_MTIME:
                return _CONFIG

            enabled = True
            action = "skip_retry"
            try:
                import yaml

                if cfg_path.exists():
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    cb = data.get("circuit_breaker") or {}
                    if isinstance(cb, dict):
                        enabled = bool(cb.get("enabled", True))
                        action = str(cb.get("action", "skip_retry"))
            except Exception as exc:
                logger.debug("circuit_breaker config load failed (fail-open): %s", exc)

            cfg = CircuitBreakerConfig(enabled=enabled, action=action)
            _CONFIG = cfg
            _CONFIG_MTIME = mtime
            return cfg
    except Exception as exc:  # pragma: no cover — outermost guard
        logger.debug("circuit_breaker config load raised (fail-open): %s", exc)
        return CircuitBreakerConfig()


# --------------------------------------------------------------------------- #
# Breaker logic                                                                 #
# --------------------------------------------------------------------------- #

CB_PREFIX = "[circuit-breaker]"


def circuit_open(function_name: str) -> bool:
    """Return True if ``function_name`` is circuit-open (skip retry).

    A tool is circuit-open when the breaker is enabled and its historical
    failure ledger reports recurring failures (``should_warn`` returns a string).

    Fail-open: returns False on any error so a broken ledger/config never blocks
    or alters normal tool execution.
    """
    try:
        cfg = load_circuit_breaker_config()
        if not cfg.enabled or cfg.action != "skip_retry":
            return False
        from .failure_learning import get_ledger

        return bool(get_ledger().should_warn(function_name))
    except Exception as exc:
        logger.debug("circuit_open raised (fail-open): %s", exc)
        return False


def max_attempts_for(function_name: str, default: int = 2) -> int:
    """Max retry attempts for a tool call.

    1 when circuit-open (skip retry), else ``default`` (P3's 2 = 1 retry).
    """
    return 1 if circuit_open(function_name) else default


def circuit_prefix() -> str:
    """Prefix prepended to the injected warning when circuit-open."""
    return CB_PREFIX
