"""Prompt Processor loader.

Loads YAML prompt processors from two locations:
  1. Built-in: ``vermes_cli/processors/`` (shipped in the frozen bundle)
  2. User:     ``~/.vermes/processors/``   (hot path, user-editable, AEGIS-editable)

User processors override built-in by name.  The loader is cached on first
access; the Phase 0 watcher invalidates the cache when files change.

Design constraints (Phase 1 spec):
  - Triggers are declarative enum, NOT a DSL
  - content is plain text, no Jinja2/variable interpolation
  - replaceable=False processors cannot be overridden by user/AEGIS
  - AEGIS can only modify processors in the user hot path
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

logger = logging.getLogger(__name__)

# ── Cache ──────────────────────────────────────────────────────────────
_processors_cache: Optional[List["PromptProcessor"]] = None
_cache_lock = threading.Lock()
# Used by watcher to invalidate cache — bumped on file change
_processors_generation: int = 0


@dataclass
class PromptProcessor:
    """A single prompt building block loaded from YAML."""

    name: str
    content: str
    order: int
    triggers: Dict[str, Any]
    replaceable: bool
    version: str = "1.0.0"
    description: str = ""
    source_path: Optional[Path] = None  # for watcher / debugging
    builtin: bool = False  # True=built-in, False=user override

    def should_inject(self, agent: Any) -> bool:
        """Evaluate declarative trigger condition against agent state.

        Returns True if this processor's content should be appended
        to the system prompt.
        """
        trig_type = self.triggers.get("type", "always")

        if trig_type == "always":
            return True

        elif trig_type == "tool_present":
            tools = self.triggers.get("tools", [])
            return bool(set(tools) & set(getattr(agent, "valid_tool_names", set())))

        elif trig_type == "config_flag":
            key = self.triggers.get("key", "")
            default = self.triggers.get("default", True)
            if not key:
                return bool(default)
            # Read from agent config
            val = getattr(agent, key.replace(".", "_"), None)
            if val is None:
                # Try nested read
                from vermes_cli.config import load_config

                cfg = load_config()
                parts = key.split(".")
                val = cfg
                for p in parts:
                    if isinstance(val, dict):
                        val = val.get(p)
                    else:
                        val = None
                        break
                if val is None:
                    val = default
            # "auto" or True → inject; False → skip
            if isinstance(val, str):
                return val.lower() not in {"false", "never", "no", "off"}
            return bool(val)

        elif trig_type == "model_match":
            patterns = self.triggers.get("patterns", [])
            model = (getattr(agent, "model", "") or "").lower()
            return any(p.lower() in model for p in patterns)

        elif trig_type == "provider_match":
            expected = self.triggers.get("value", "")
            return getattr(agent, "provider", "") == expected

        elif trig_type == "env_var":
            var = self.triggers.get("var", "")
            return bool(os.environ.get(var))

        else:
            logger.warning("Unknown trigger type: %s (processor=%s)", trig_type, self.name)
            return False


def _get_builtin_dir() -> Path:
    """Return the built-in processors directory (shipped in bundle)."""
    # In frozen bundle: _internal/vermes_cli/processors/
    # In dev: vermes_cli/processors/
    return Path(__file__).resolve().parent / "vermes_cli" / "processors"


def _get_user_dir() -> Path:
    """Return the user processors directory (~/.vermes/processors/)."""
    from vermes_constants import get_vermes_home

    return get_vermes_home() / "processors"


def _parse_yaml(path: Path) -> Optional[PromptProcessor]:
    """Parse a single YAML file into a PromptProcessor."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to parse processor %s: %s", path, e)
        return None

    if not data or not isinstance(data, dict):
        return None

    name = data.get("name", path.stem)
    content = data.get("content", "")
    if not content:
        return None

    return PromptProcessor(
        name=name,
        content=content,
        order=data.get("order", 999),
        triggers=data.get("triggers", {"type": "always"}),
        replaceable=data.get("replaceable", True),
        version=data.get("version", "1.0.0"),
        description=data.get("description", ""),
        source_path=path,
    )


def load_all_processors() -> List[PromptProcessor]:
    """Load all prompt processors, user overrides built-in.

    Returns a list sorted by ``order`` ascending.
    Cached; call :func:`invalidate_cache` to force reload.
    """
    global _processors_cache, _processors_generation

    if _processors_cache is not None:
        return _processors_cache

    with _cache_lock:
        if _processors_cache is not None:
            return _processors_cache

        by_name: Dict[str, PromptProcessor] = {}

        # 1. Load built-in processors
        builtin_dir = _get_builtin_dir()
        if builtin_dir.exists():
            for yaml_file in sorted(builtin_dir.glob("*.yaml")):
                proc = _parse_yaml(yaml_file)
                if proc:
                    proc.builtin = True
                    by_name[proc.name] = proc
                    logger.debug("Loaded built-in processor: %s", proc.name)

        # 2. Load user processors (override built-in by name)
        user_dir = _get_user_dir()
        if user_dir.exists():
            for yaml_file in sorted(user_dir.glob("*.yaml")):
                proc = _parse_yaml(yaml_file)
                if proc:
                    existing = by_name.get(proc.name)
                    if existing and not existing.replaceable:
                        logger.warning(
                            "User processor '%s' ignored: built-in is non-replaceable",
                            proc.name,
                        )
                        continue
                    proc.builtin = False
                    by_name[proc.name] = proc
                    logger.debug("Loaded user processor: %s (overrides built-in)", proc.name)

        # 3. Sort by order
        result = sorted(by_name.values(), key=lambda p: p.order)
        _processors_cache = result
        logger.info("Loaded %d prompt processors (%d built-in, %d user)",
                     len(result),
                     sum(1 for p in result if p.builtin),
                     sum(1 for p in result if not p.builtin))
        return result


def invalidate_cache() -> None:
    """Force reload on next access. Called by watcher."""
    global _processors_cache, _processors_generation
    with _cache_lock:
        _processors_cache = None
        _processors_generation += 1
        logger.info("Prompt processor cache invalidated (generation=%d)", _processors_generation)


def get_generation() -> int:
    """Return current generation (bumped on each invalidation)."""
    return _processors_generation


# ── Processor file watcher ─────────────────────────────────────────────
# Lightweight polling watcher for ~/.vermes/processors/ directory.
# Invalidates cache on file change; does NOT reload individual files
# (load_all_processors() handles that on next access).
_processor_watcher: Optional[threading.Thread] = None
_processor_watcher_stop = threading.Event()


def start_processor_watcher(poll_interval: float = 1.0) -> None:
    """Start a lightweight watcher for ~/.vermes/processors/ changes.

    Uses directory mtime polling (not per-file). On change, invalidates
    the processor cache so the next build_system_prompt picks up new content.
    """
    global _processor_watcher
    if _processor_watcher is not None and _processor_watcher.is_alive():
        return

    _processor_watcher_stop.clear()
    last_mtime: Dict[str, float] = {}
    _first_scan = [True]  # mutable closure flag

    def _poll() -> None:
        while not _processor_watcher_stop.is_set():
            try:
                try:
                    user_dir = _get_user_dir()
                except Exception:
                    _processor_watcher_stop.wait(poll_interval)
                    continue
                if user_dir.exists():
                    current = {}
                    for p in user_dir.glob("*.yaml"):
                        try:
                            current[str(p)] = p.stat().st_mtime
                        except OSError:
                            continue
                    if _first_scan[0]:
                        # First scan: record state, don't trigger
                        _first_scan[0] = False
                    elif current != last_mtime:
                        logger.info("[ProcessorWatcher] change detected, invalidating cache")
                        invalidate_cache()
                    last_mtime.clear()
                    last_mtime.update(current)
            except Exception as e:
                logger.debug("[ProcessorWatcher] poll error: %s", e)
            _processor_watcher_stop.wait(poll_interval)

    t = threading.Thread(target=_poll, name="processor-watcher", daemon=True)
    t.start()
    _processor_watcher = t
    try:
        watch_dir = _get_user_dir()
    except Exception:
        watch_dir = Path("~/.vermes/processors")
    logger.info("[ProcessorWatcher] started, watching %s (poll=%ss)",
                watch_dir, poll_interval)


def stop_processor_watcher() -> None:
    """Stop the processor watcher (for clean shutdown / testing)."""
    global _processor_watcher
    _processor_watcher_stop.set()
    if _processor_watcher is not None:
        _processor_watcher.join(timeout=2.0)
        _processor_watcher = None
