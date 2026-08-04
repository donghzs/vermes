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

import copy
import hashlib
import logging
import os
import re
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

# ── Layer ordering (prefix-cache protection) ───────────────────────────
# The system prompt is concatenated layer-first so that the *stable* prefix
# stays byte-identical across turns.  If a volatile fragment could sort ahead
# of a stable one (priority alone allows that), every turn would emit a
# different prefix and the provider prompt cache would miss on the WHOLE
# prompt — a direct token cost, not a cosmetic issue.
#
#   stable   — identity, guidance, platform hints. Same for the whole session.
#   context  — per-session context (context files, skills). Stable within a session.
#   volatile — per-turn injections (memory recall, steer, reminders).
_LAYER_ORDER: Dict[str, int] = {"stable": 0, "context": 1, "volatile": 2}
_DEFAULT_LAYER = "stable"


@dataclass
class PromptProcessor:
    """A single prompt building block loaded from YAML.

    Supports both v0 (simple triggers dict) and v1 (full schema with
    kind/layer/model_affinity/conditions/render/governance/lifecycle/metadata)
    YAML formats. v0 files are auto-upgraded on load.
    """

    # Required
    name: str  # v0: display+match key; v1: same as `id`
    content: str

    # v0 fields (still read for backward compat)
    order: int = 999
    triggers: Dict[str, Any] = field(default_factory=lambda: {"type": "always"})
    replaceable: bool = True
    version: str = "1.0.0"
    description: str = ""
    source_path: Optional[Path] = None
    builtin: bool = False

    # v1 fields (defaults = v0-equivalent behavior)
    api: str = "vermes.processor/v1"
    kind: str = "prompt_fragment"  # prompt_fragment|injection|strategy_dial|behavior_rule(RESERVED)|lifecycle_hook(RESERVED)
    id: str = ""  # override match key; empty = use name
    enabled: bool = True
    priority: Optional[int] = None  # v1 name for order; None = use order
    layer: str = "stable"  # stable|context|volatile
    model_affinity: Dict[str, Any] = field(default_factory=lambda: {"operator": "any_of", "match": []})
    conditions: Dict[str, Any] = field(default_factory=dict)
    render: Dict[str, Any] = field(default_factory=lambda: {"engine": "none", "on_missing": "keep", "inputs": {}})
    governance: Dict[str, Any] = field(default_factory=lambda: {"risk_tier": "L2", "replaceable": True, "mutable_by_aegis": True, "rollback": "enabled", "critic_guarded": False, "hash": "auto"})
    lifecycle: Dict[str, Any] = field(default_factory=lambda: {"hooks": []})
    metadata: Dict[str, Any] = field(default_factory=lambda: {"author": "unknown", "source": "builtin"})

    @property
    def effective_id(self) -> str:
        """The override match key — `id` if set, else `name`."""
        return self.id or self.name

    @property
    def effective_priority(self) -> int:
        """Priority for ordering — `priority` if set, else `order` (v0 compat)."""
        return self.priority if self.priority is not None else self.order

    @property
    def effective_replaceable(self) -> bool:
        """Replaceable — v1 governance takes precedence over v0 top-level."""
        return self.governance.get("replaceable", self.replaceable)

    @property
    def risk_tier(self) -> str:
        """Risk tier for classify_component_swap. Defaults to L2 (fail-closed)."""
        return self.governance.get("risk_tier", "L2")

    @property
    def layer_rank(self) -> int:
        """Numeric layer rank for ordering. Unknown layers sort as stable."""
        return _LAYER_ORDER.get(self.layer, _LAYER_ORDER[_DEFAULT_LAYER])

    @property
    def content_hash(self) -> str:
        """Canonical sha256 of this manifest (``governance.hash``).

        Resolved at parse time; ``"auto"`` only survives for objects built
        directly in code rather than loaded from YAML.
        """
        return self.governance.get("hash", "auto")

    def should_inject(self, agent: Any) -> bool:
        """Evaluate whether this processor should be injected into the prompt.

        Supports both v0 triggers dict and v1 conditions/model_affinity.
        v1 takes precedence when conditions is non-empty.
        """
        if not self.enabled:
            return False

        # v1 path: conditions + model_affinity
        if self.conditions:
            return self._eval_conditions(agent)

        # v0 path: triggers dict
        return self._eval_triggers(agent)

    def _eval_conditions(self, agent: Any) -> bool:
        """Evaluate v1 conditions dict."""
        conds = self.conditions

        # require_tools
        req_tools = conds.get("require_tools", [])
        if req_tools:
            if not set(req_tools) & set(getattr(agent, "valid_tool_names", set())):
                return False

        # require_capabilities
        req_caps = conds.get("require_capabilities", [])
        if req_caps:
            # Not yet implemented — skip (fail-open, don't block)
            pass

        # platform
        platforms = conds.get("platform", ["*"])
        if platforms != ["*"]:
            agent_platform = getattr(agent, "platform", "") or ""
            if agent_platform not in platforms and "*" not in platforms:
                return False

        # config_flag
        cfg_flag = conds.get("config_flag", {})
        if cfg_flag and cfg_flag.get("key"):
            key = cfg_flag["key"]
            default = cfg_flag.get("default", True)
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
            if isinstance(val, str):
                if val.lower() in {"false", "never", "no", "off"}:
                    return False
            elif not val:
                return False

        # model_affinity
        affinity = self.model_affinity
        match_list = affinity.get("match", [])
        if match_list:
            operator = affinity.get("operator", "any_of")
            model_lower = (getattr(agent, "model", "") or "").lower()
            matches = [m.lower() in model_lower for m in match_list]
            if operator == "any_of" and not any(matches):
                return False
            elif operator == "all_of" and not all(matches):
                return False
            elif operator == "none_of" and any(matches):
                return False

        return True

    def _eval_triggers(self, agent: Any) -> bool:
        """Evaluate v0 triggers dict (backward compat)."""
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

    def render_content(self, context: Dict[str, Any]) -> str:
        """Render content with mustache-like {{var}} substitution.

        Only active when render.engine == 'mustache'.
        Default engine='none' returns content as-is.
        """
        engine = self.render.get("engine", "none")
        if engine == "none":
            return self.content

        if engine == "mustache":
            return _mustache_render(self.content, self.render.get("inputs", {}), context, self.render.get("on_missing", "keep"))

        logger.warning("Unknown render engine: %s (processor=%s)", engine, self.name)
        return self.content


# ── Canonical manifest hash ────────────────────────────────────────────
# ``governance.hash`` is the identity of a processor *version*.  Phase 2
# (variant isolation) and Phase 3 (evolution history) key off it, so the
# serialization MUST be canonical: the same manifest has to hash identically
# on every machine, every Python version, every YAML round-trip.
#
# Canonical form:
#   1. drop ``governance.hash`` itself (a hash cannot contain itself)
#   2. recursively sort dict keys (dict iteration order is insertion order)
#   3. yaml.safe_dump(sort_keys=True, allow_unicode=True, default_flow_style=False)
#   4. normalize CRLF/CR → LF, strip trailing newlines
#   5. sha256 of the UTF-8 bytes, prefixed "sha256:"

_HASH_PREFIX = "sha256:"


def _canonicalize(obj: Any) -> Any:
    """Recursively sort dict keys for stable serialization."""
    if isinstance(obj, dict):
        return {k: _canonicalize(obj[k]) for k in sorted(obj.keys(), key=str)}
    if isinstance(obj, (list, tuple)):
        return [_canonicalize(x) for x in obj]
    return obj


def compute_manifest_hash(data: Dict[str, Any]) -> str:
    """Compute the canonical sha256 of a processor manifest dict.

    ``governance.hash`` is excluded so the value is self-consistent.
    Returns ``"sha256:<hex>"``.
    """
    try:
        payload = copy.deepcopy(data)
    except Exception:
        payload = dict(data)
    gov = payload.get("governance")
    if isinstance(gov, dict):
        gov.pop("hash", None)
    canon = _canonicalize(payload)
    text = yaml.safe_dump(
        canon,
        sort_keys=True,
        allow_unicode=True,
        default_flow_style=False,
    )
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return _HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_risk_tier(text: str) -> Optional[str]:
    """Extract ``governance.risk_tier`` from raw manifest YAML text.

    Returns ``"L0"``/``"L1"``/``"L2"``, or ``None`` when the text is not a
    parseable manifest or declares no tier.  Callers decide the fail-closed
    default — this function never invents one.
    """
    try:
        data = yaml.safe_load(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    gov = data.get("governance")
    if not isinstance(gov, dict):
        return None
    tier = gov.get("risk_tier")
    if isinstance(tier, str) and tier.upper() in ("L0", "L1", "L2"):
        return tier.upper()
    return None


def _get_builtin_dir() -> Path:
    """Return the built-in processors directory (shipped in bundle)."""
    # In frozen bundle: _internal/vermes_cli/processors/
    # In dev: vermes_cli/processors/
    # __file__ = <repo>/agent/prompt_processor_loader.py
    # .parent = <repo>/agent/
    # .parent.parent = <repo>/  (dev) or <bundle>/_internal/ (frozen)
    return Path(__file__).resolve().parent.parent / "vermes_cli" / "processors"


def _get_user_dir() -> Path:
    """Return the user processors directory (~/.vermes/processors/)."""
    from vermes_constants import get_vermes_home

    return get_vermes_home() / "processors"


def _parse_yaml(path: Path) -> Optional[PromptProcessor]:
    """Parse a single YAML file into a PromptProcessor.

    Supports both v0 (simple triggers dict) and v1 (full schema with
    kind/layer/model_affinity/conditions/render/governance/lifecycle/metadata).
    v0 files are auto-upgraded: missing fields get v0-equivalent defaults.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to parse processor %s: %s", path, e)
        return None

    if not data or not isinstance(data, dict):
        return None

    # Check api version (v1 only; unknown major version rejected)
    api = data.get("api", "vermes.processor/v1")
    if not api.startswith("vermes.processor/v"):
        logger.warning("Unknown api '%s' in %s, skipping", api, path)
        return None
    api_major = api.split("/v")[-1].split(".")[0]
    if api_major not in ("1",):
        logger.warning("Unsupported processor api major version '%s' in %s, skipping", api, path)
        return None

    # v1: id takes precedence; v0: use name
    proc_id = data.get("id", "")
    name = data.get("name", path.stem)
    content = data.get("content", "")
    if not content:
        return None

    # v0 fields (still read for compat)
    order = data.get("order", 999)
    triggers = data.get("triggers", {"type": "always"})
    replaceable_v0 = data.get("replaceable", True)
    version = data.get("version", "1.0.0")
    description = data.get("description", "")

    # v1 fields
    kind = data.get("kind", "prompt_fragment")
    # RESERVED kinds: behavior_rule, lifecycle_hook — warn and skip for now
    if kind in ("behavior_rule", "lifecycle_hook"):
        logger.info("Processor kind '%s' is RESERVED (Phase 2), skipping %s", kind, path)
        return None

    enabled = data.get("enabled", True)
    priority = data.get("priority")  # None = fall back to v0 `order`

    # layer drives prefix-cache-safe ordering — an unknown value must not
    # silently sort ahead of everything, so fall back to the stable layer.
    layer = data.get("layer", _DEFAULT_LAYER)
    if layer not in _LAYER_ORDER:
        logger.warning(
            "Processor %s declares unknown layer '%s' (valid: %s), falling back to '%s'",
            name, layer, sorted(_LAYER_ORDER), _DEFAULT_LAYER,
        )
        layer = _DEFAULT_LAYER

    model_affinity = data.get("model_affinity") or {"operator": "any_of", "match": []}
    conditions = data.get("conditions") or {}
    render = data.get("render") or {"engine": "none", "on_missing": "keep", "inputs": {}}
    # A YAML key present but null (``render:``) yields None, and a scalar
    # yields a str — both would blow up on ``.get``.  Coerce to the default.
    if not isinstance(model_affinity, dict):
        model_affinity = {"operator": "any_of", "match": []}
    if not isinstance(conditions, dict):
        conditions = {}
    if not isinstance(render, dict):
        render = {"engine": "none", "on_missing": "keep", "inputs": {}}

    # governance: merge v0 replaceable into v1 governance.replaceable
    gov_defaults = {"risk_tier": "L2", "replaceable": replaceable_v0, "mutable_by_aegis": True, "rollback": "enabled", "critic_guarded": False, "hash": "auto"}
    governance = data.get("governance") or {}
    if not isinstance(governance, dict):
        governance = {}
    # Fill missing governance keys with defaults (v0 replaceable → governance.replaceable)
    for k, v in gov_defaults.items():
        governance.setdefault(k, v)

    # Resolve ``hash: auto`` into the real canonical digest.  An explicitly
    # declared digest that disagrees is a tamper/staleness signal — log it,
    # but always trust the computed value (a manifest cannot vouch for itself).
    _declared = governance.get("hash")
    _computed = compute_manifest_hash(data)
    if isinstance(_declared, str) and _declared not in ("", "auto") and _declared != _computed:
        logger.warning(
            "Processor %s declares hash %s but content hashes to %s (using computed)",
            name, _declared, _computed,
        )
        governance["declared_hash"] = _declared
    governance["hash"] = _computed

    lifecycle = data.get("lifecycle") or {"hooks": []}
    if not isinstance(lifecycle, dict):
        lifecycle = {"hooks": []}
    # Validate hooks against VALID_HOOKS
    hooks = lifecycle.get("hooks", [])
    if hooks:
        from vermes_cli.plugins import VALID_HOOKS
        valid_hooks = [h for h in hooks if h in VALID_HOOKS]
        invalid = set(hooks) - VALID_HOOKS
        if invalid:
            logger.warning("Processor %s has invalid lifecycle hooks: %s (valid: %s)", name, invalid, VALID_HOOKS)
        lifecycle["hooks"] = valid_hooks

    metadata = data.get("metadata", {"author": "unknown", "source": "builtin"})

    return PromptProcessor(
        name=name,
        content=content,
        order=order,
        triggers=triggers,
        replaceable=replaceable_v0,
        version=version,
        description=description,
        source_path=path,
        api=api,
        kind=kind,
        id=proc_id,
        enabled=enabled,
        priority=priority,
        layer=layer,
        model_affinity=model_affinity,
        conditions=conditions,
        render=render,
        governance=governance,
        lifecycle=lifecycle,
        metadata=metadata,
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

        # 1. Load built-in processors (flat dir: vermes_cli/processors/*.yaml)
        builtin_dir = _get_builtin_dir()
        if builtin_dir.exists():
            for yaml_file in sorted(builtin_dir.glob("*.yaml")):
                proc = _parse_yaml(yaml_file)
                if proc:
                    proc.builtin = True
                    key = proc.effective_id
                    by_name[key] = proc
                    logger.debug("Loaded built-in processor: %s (id=%s)", proc.name, key)

        # 2. Load user processors (subdir: ~/.vermes/processors/<id>/processor.yaml
        #    + flat compat: ~/.vermes/processors/*.yaml)
        user_dir = _get_user_dir()
        if user_dir.exists():
            user_files = []
            # Subdirectory pattern: <id>/processor.yaml
            user_files.extend(sorted(user_dir.glob("*/processor.yaml")))
            # Flat compat: *.yaml (not processor.yaml itself)
            user_files.extend(sorted(f for f in user_dir.glob("*.yaml") if f.name != "processor.yaml"))
            for yaml_file in user_files:
                proc = _parse_yaml(yaml_file)
                if proc:
                    key = proc.effective_id
                    existing = by_name.get(key)
                    if existing and not existing.effective_replaceable:
                        logger.warning(
                            "User processor '%s' (id=%s) ignored: built-in is non-replaceable",
                            proc.name, key,
                        )
                        continue
                    proc.builtin = False
                    by_name[key] = proc
                    logger.debug("Loaded user processor: %s (id=%s, overrides=%s)", proc.name, key, existing is not None)

        # 3. Deterministic three-level sort: layer → priority → id.
        #    layer FIRST keeps the stable prefix byte-identical (prompt cache);
        #    id LAST removes any dependence on glob/dict iteration order, which
        #    would otherwise make the prompt prefix jitter between runs.
        result = sorted(
            by_name.values(),
            key=lambda p: (p.layer_rank, p.effective_priority, p.effective_id),
        )
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


# ── Minimal mustache renderer (no third-party dep) ─────────────────────
# Supports only {{var}} substitution (no {{{raw}}}, no {{#if}}, no {{#each}}).
# No HTML escaping (prompts are not HTML).
# This is deliberately a subset of Mustache spec — see design doc §5.

_MUSTACHE_RE = re.compile(r"\{\{(\w+(?:\.\w+)*)\}\}")


def _mustache_render(content: str, inputs: Dict[str, Any], context: Dict[str, Any], on_missing: str = "keep") -> str:
    """Render {{var}} placeholders in content.

    inputs: mapping from template var name → context path (e.g. {"memory_budget": "context.memory_budget"})
    context: the actual context dict (e.g. {"context": {"memory_budget": 1000}, ...})
    on_missing: 'keep' (default, leave {{var}} as-is), 'empty' (replace with ''), 'error' (raise)
    """
    def _resolve(path: str) -> Any:
        parts = path.split(".")
        val: Any = context
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                val = getattr(val, p, None)
            if val is None:
                return None
        return val

    def _replace(m: re.Match) -> str:
        var = m.group(1)
        # Check if this var is in the inputs mapping
        if var in inputs:
            ctx_path = inputs[var]
            if isinstance(ctx_path, str):
                val = _resolve(ctx_path)
            else:
                val = ctx_path
            if val is not None:
                return str(val)
            if on_missing == "empty":
                return ""
            elif on_missing == "error":
                raise KeyError(f"Mustache variable '{var}' not found in context at '{ctx_path}'")
            else:
                return m.group(0)  # keep original
        else:
            # Try direct context resolution
            val = _resolve(var)
            if val is not None:
                return str(val)
            if on_missing == "empty":
                return ""
            elif on_missing == "error":
                raise KeyError(f"Mustache variable '{var}' not found in context")
            else:
                return m.group(0)

    return _MUSTACHE_RE.sub(_replace, content)


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
                current = {}
                if user_dir.exists():
                    # Subdirectory pattern: <id>/processor.yaml
                    # Phase 3: variant archives live in <id>/variants/<hash>.yaml
                    # — the glob `*/processor.yaml` naturally excludes them
                    # (variant files are named sha256_<hash>.yaml, not processor.yaml,
                    # and are one level deeper).  No explicit skip needed.
                    for p in user_dir.glob("*/processor.yaml"):
                        try:
                            current[str(p)] = p.stat().st_mtime
                        except OSError:
                            continue
                    # Flat compat: *.yaml (not processor.yaml itself)
                    for p in user_dir.glob("*.yaml"):
                        if p.name == "processor.yaml":
                            continue
                        try:
                            current[str(p)] = p.stat().st_mtime
                        except OSError:
                            continue
                # Always flip _first_scan, even if dir doesn't exist yet
                if _first_scan[0]:
                    _first_scan[0] = False
                elif current != last_mtime:
                    logger.info("[ProcessorWatcher] change detected, invalidating cache")
                    invalidate_cache()
                    # Phase 2 audit P1: the tool-processor loader keeps its own
                    # cache; without this, editing a tool processor YAML would
                    # not hot-reload.  Lazy import avoids a circular import
                    # (tool_processor_loader imports this module at load time).
                    try:
                        from agent.tool_processor_loader import (
                            invalidate_cache as _tpl_invalidate,
                            register_tool_processors as _tpl_register,
                        )
                        _tpl_invalidate()
                        # Re-populate ToolRegistry so the user-visible tool set
                        # reflects the edited YAML WITHOUT a restart — this is
                        # the half that actually closes the "needs restart" gap.
                        _tpl_register()
                    except Exception as _e:  # pragma: no cover - defensive
                        logger.debug("[ProcessorWatcher] tool hot-reload skipped: %s", _e)
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


def is_processor_hot_path(target_path: str) -> bool:
    """Check if a path is inside the user processors hot path.

    Returns True for:
    - ~/.vermes/processors/<id>/processor.yaml
    - ~/.vermes/processors/*.yaml (flat compat)
    """
    try:
        tp = Path(target_path).resolve()
        user_dir = _get_user_dir().resolve()
        return tp.is_relative_to(user_dir)
    except Exception:
        return False
