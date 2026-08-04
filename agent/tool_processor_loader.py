"""Tool Processor loader (Phase 2 — 工具外置为声明式 Processor 积木).

Loads ``kind: tool`` processors from two locations:
  1. Built-in: ``vermes_cli/processors/*.yaml``  (shipped in the frozen bundle)
  2. User:     ``~/.vermes/processors/**/processor.yaml``  (hot path, user/AEGIS-editable)

A tool processor declares a tool's *schema / governance / availability /
lifecycle* in YAML.  The loader resolves ``handler.ref`` (a dotted path to a
Python callable with the registry contract signature ``(args, **kw)``) and
registers the tool into :mod:`tools.registry`, so it flows automatically into
``valid_tool_names`` downstream — the same single-source-of-truth registry that
the 80+ self-registering Python tools use.

Design notes (see vermes-phase2-tool-processor-design_20260804.md and
vermes-phase2_5-inline-tool-design_20260804.md):
  - Thin bridge (Phase 2): execution stays in Python via ``handler.ref``.
  - Declarative execution (Phase 2.5): ``handler.inline`` builds a standard
    ``(args, **kw)`` handler closure at load time (no Python file needed),
    so a brand-new tool can land from a pure YAML manifest.  The generated
    handler registers into ``tools.registry`` exactly like a Python tool —
    registry / dispatch / governance / hooks are untouched.
  - ``kind: tool`` is disjoint from the prompt loader: the prompt loader
    requires a non-empty ``content`` block and silently skips tool processors;
    this loader only keeps ``kind == "tool"`` and never requires ``content``.
  - ``handler.ref`` HARD constraint (audit 补正 08-04): must resolve to a
    ``(args, **kw)``-signature named function.  Lambda-wrapped tools
    (e.g. ``memory_tool``) and non-existent refs are rejected → error-skip,
    never silently swallowed.
  - Governance (``risk_tier``) is consumed by the EXISTING approval path
    (``tools/approval.py`` + ``is_processor_hot_path`` + ``parse_risk_tier``),
    which is kind-agnostic — no new governance layer needed.
  - Lifecycle hooks are validated against ``VALID_HOOKS`` and registered into
    the plugin manager, so the existing ``invoke_hook("post_tool_call", ...)``
    dispatch seam fires them for their tool (real, not mocked).
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import yaml

logger = logging.getLogger(__name__)

# Reuse the canonical helpers + path resolvers from the prompt loader so the
# two loaders share one source of truth for hashing / hot-path classification.
from agent.prompt_processor_loader import (  # noqa: E402
    compute_manifest_hash,
    is_processor_hot_path,
    parse_risk_tier,
    _get_builtin_dir,
    _get_user_dir,
)

# ── kind enum extension (v1 five kinds → six; tool added in Phase 2) ──────
TOOL_KIND = "tool"

# ── Cache ──────────────────────────────────────────────────────────────
_tool_processors_cache: Optional[List["ToolProcessor"]] = None
_tool_cache_lock = threading.Lock()
_tool_processors_generation: int = 0

# tool_name -> List[validated hook events] populated by register_tool_processors
TOOL_LIFECYCLE_HOOKS: Dict[str, List[str]] = {}

# Record of (tool_name, event) hook callbacks registered into the plugin
# manager, so we never stack duplicates on repeated registration.
_registered_hook_keys: Set[Tuple[str, str]] = set()

# Observability ring for tests / debugging: append-only list of hook fires.
_hook_fires: List[Dict[str, Any]] = []
_hook_fires_lock = threading.Lock()

# Per-tool recorder callbacks fire via the existing dispatch invoke_hook seam.
# We record the fire so it is directly observable/testable in thin-bridge mode
# (the hook *logic* itself is supplied by a plugin or Phase 2.5 inline).
def _make_hook_recorder(tool_name: str, event: str) -> Callable[..., Any]:
    def _recorder(**kwargs: Any) -> None:
        if kwargs.get("tool_name") != tool_name:
            return  # only act for this tool's dispatches
        with _hook_fires_lock:
            _hook_fires.append({
                "tool_name": tool_name,
                "event": event,
                "args": kwargs.get("args"),
                "result": kwargs.get("result"),
            })
    _recorder.__name__ = f"_tp_hook_{tool_name}_{event}"
    return _recorder


@dataclass
class ToolProcessor:
    """A tool declared from YAML (kind: tool).

    Mirrors the v1 PromptProcessor schema for the common fields, with
    tool-specific additions (toolset / schema / handler_ref / availability).
    """

    name: str
    kind: str = TOOL_KIND
    id: str = ""                       # override match key; empty = use name
    enabled: bool = True
    priority: int = 100               # ordering within a toolset
    toolset: str = "user"             # maps to registry toolset
    schema: Dict[str, Any] = field(default_factory=dict)   # OpenAI-format function obj
    handler_ref: Optional[str] = None  # dotted path → (args, **kw) callable
    inline_spec: Optional[Dict[str, Any]] = None  # handler.inline → declarative executor
    check_fn_ref: Optional[str] = None  # dotted path → zero-arg availability callable
    requires_env: List[str] = field(default_factory=list)
    is_async: bool = False
    emoji: str = ""
    max_result_size_chars: Optional[int] = None
    availability: Dict[str, Any] = field(default_factory=dict)
    conditions: Dict[str, Any] = field(default_factory=dict)
    lifecycle: Dict[str, Any] = field(default_factory=lambda: {"hooks": []})
    governance: Dict[str, Any] = field(default_factory=lambda: {
        "risk_tier": "L2", "replaceable": True, "mutable_by_aegis": True,
        "rollback": "enabled", "critic_guarded": False, "hash": "auto",
    })
    metadata: Dict[str, Any] = field(default_factory=lambda: {"author": "unknown", "source": "builtin"})
    validated_hooks: List[str] = field(default_factory=list)
    source_path: Optional[Path] = None
    builtin: bool = False

    @property
    def effective_id(self) -> str:
        """The override match key — `id` if set, else `name`."""
        return self.id or self.name

    @property
    def risk_tier(self) -> str:
        """Risk tier for classify_component_swap. Defaults to L2 (fail-closed)."""
        return self.governance.get("risk_tier", "L2")


# ── Reference resolution ────────────────────────────────────────────────
def _resolve_ref_generic(ref: str) -> Callable:
    """Resolve a dotted path ``module.attr`` to any attribute (no contract check).

    Used for ``check_fn_ref`` (zero-arg availability callables) where the
    registry ``(args, **kw)`` contract does not apply.
    """
    if not ref or not isinstance(ref, str):
        raise ValueError("ref must be a non-empty dotted path string")
    module_path, _, attr = ref.rpartition(".")
    if not module_path or not attr:
        raise ValueError(f"invalid ref '{ref}' (expected module.attr)")
    try:
        module = importlib.import_module(module_path)
    except Exception as e:
        raise ImportError(f"cannot import module '{module_path}': {e}")
    try:
        obj = getattr(module, attr)
    except AttributeError:
        raise AttributeError(f"module '{module_path}' has no attribute '{attr}'")
    if not callable(obj):
        raise TypeError(f"'{ref}' is not callable")
    return obj


def _resolve_handler_ref(ref: str) -> Callable:
    """Resolve ``handler.ref`` to a callable honouring the registry contract.

    The registry calls handlers as ``handler(args, **kw)``.  We HARD-enforce
    that the resolved callable accepts a positional ``args`` and a ``**kw``
    variadic keyword — this is the audit补正 (08-04): lambda-wrapped tools
    (``memory_tool``) and signature-mismatched / non-existent refs are rejected
    here rather than failing later inside the dispatch loop.

    Raises on any failure so callers can error-skip the processor (never silent).
    """
    obj = _resolve_ref_generic(ref)
    # Signature contract check (skip C-extensions/builtins that lack a signature).
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return obj
    params = list(sig.parameters.values())
    has_positional = any(
        p.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        )
        for p in params
    )
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
    if not has_positional:
        raise TypeError(
            f"'{ref}' signature does not accept a positional 'args' argument "
            f"(registry contract is handler(args, **kw))"
        )
    if not has_var_kw:
        raise TypeError(
            f"'{ref}' signature is missing **kw (registry contract is "
            f"handler(args, **kw))"
        )
    return obj


def _make_env_check(envs: List[str]) -> Callable[[], bool]:
    """Build a zero-arg availability predicate from required env vars."""
    env_list = list(envs)
    def _check() -> bool:
        return all(bool(os.environ.get(e)) for e in env_list)
    return _check


# ── Inline declarative execution (Phase 2.5) ───────────────────────────────
# ``handler.inline`` carries a declarative execution spec (shell / http).  The
# loader builds a standard ``(args, **kw)`` handler closure from it and registers
# it into ToolRegistry — identical to a Python tool, so registry / dispatch /
# governance / hooks are unchanged.  Two backends:
#
#   shell — argv LIST (never a shell string) executed with shell=False.  This is
#           RCE-equivalent, so it is gated by ``approvals.allow_inline_shell``
#           (default False) AND its risk_tier is force-clamped to L2.
#   http  — GET/POST to a URL.  Static base URL + templated query/headers/body,
#           OR a whole-URL-from-param escape hatch (``url_arg``) that is STRICT
#           scheme-whitelisted (http/https only).  Always clamped to L2.
#
# Security hardening (see vermes-phase2_5-inline-tool-design_20260804.md §2):
#   * args are interpolated per-element into argv / query / headers / body via
#     ``_subst`` — never concatenated into a shell string, so ``; rm -rf`` style
#     payloads are inert argv elements, not command separators.
#   * every executor returns a JSON string via tool_result / tool_error.
#   * invalid specs / missing shell permission → error-skip (never silent).

# Safe ``{arg}`` substitution: replace ``{name}`` tokens only, leaving any other
# braces / literal text intact (no str.format() — that would choke on literal
# braces in the value and would happily evaluate format specifiers).
_SUBST_RE = re.compile(r"\{(\w+)\}")


def _subst(token: str, args: Dict[str, Any]) -> str:
    """Replace ``{key}`` placeholders in *token* with ``args[key]`` (str()'d)."""
    if not isinstance(token, str):
        return token
    return _SUBST_RE.sub(lambda m: str(args.get(m.group(1), "")), token)


def _truncate(text: str, max_chars: Optional[int]) -> str:
    if max_chars and isinstance(max_chars, int) and len(text) > max_chars:
        return text[:max_chars]
    return text


def is_inline_processor_content(content: Optional[str]) -> bool:
    """True if a processor manifest's text declares a ``handler.inline`` block.

    Used by the approval gate (:func:`tools.approval._resolve_processor_tier`)
    to force L2 for inline (command/HTTP-executing) tools: editing the YAML
    edits what the agent runs, so an inline processor can never self-attest
    below L2 regardless of what ``governance.risk_tier`` claims.

    Parse-safe: a malformed or unreadable manifest is treated as *inline*
    (fail-closed — force L2 review is the safe default rather than letting a
    sneaky inline manifest slip through at a lower tier).  Empty / non-string
    content returns ``False`` (nothing to govern).
    """
    if not content or not isinstance(content, str):
        return False
    try:
        data = yaml.safe_load(content)
    except Exception:
        return True  # unreadable → assume risky, force L2
    if not isinstance(data, dict):
        return False
    handler = data.get("handler")
    return isinstance(handler, dict) and "inline" in handler


def _allow_inline_shell() -> bool:
    """Whether ``handler.inline.type: shell`` is permitted (approvals gate).

    Config key ``approvals.allow_inline_shell`` (default ``False``).  Fail-safe:
    any error → ``False`` (locked down).
    """
    try:
        from tools.approval import _get_approval_config
        return bool(_get_approval_config().get("allow_inline_shell", False))
    except Exception:
        return False


def _run_shell(argv: List[str], timeout: int, max_chars: Optional[int]) -> str:
    """Execute an argv list with shell=False. Returns a JSON string."""
    from tools.registry import tool_error, tool_result
    try:
        from vermes_constants import get_subprocess_home
        cwd = get_subprocess_home() or None
    except Exception:
        cwd = None
    try:
        proc = subprocess.run(
            argv,
            shell=False,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return tool_error(f"inline shell timed out after {timeout}s")
    except (OSError, subprocess.SubprocessError) as e:
        return tool_error(f"inline shell execution failed: {e}")
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        # Surface non-zero exit but still return the captured output.
        return tool_result(output=_truncate(out, max_chars), returncode=proc.returncode)
    return tool_result(output=_truncate(out, max_chars), returncode=0)


def _run_http(
    method: str,
    url: Optional[str],
    query: Dict[str, str],
    headers: Dict[str, str],
    body: Optional[str],
    timeout: int,
    max_chars: Optional[int],
) -> str:
    """Perform an HTTP request (urllib, zero extra deps). Returns a JSON string."""
    import urllib.error
    import urllib.parse
    import urllib.request
    from tools.registry import tool_error, tool_result

    if not url or not isinstance(url, str):
        return tool_error("inline http: missing or invalid url")
    # Scheme whitelist — block file://, gopher://, ftp://, etc. (SSRF guard).
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return tool_error(
            f"inline http: unsupported scheme '{parsed.scheme}' (only http/https allowed)"
        )
    if query:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(query)
    data = body.encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return tool_result(status=resp.status, body=_truncate(raw, max_chars))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        return tool_error(f"HTTP {e.code}: {_truncate(detail, 500)}", status=e.code)
    except (OSError, urllib.error.URLError) as e:
        return tool_error(f"inline http request failed: {e}")


def _make_inline_handler(spec: Dict[str, Any], max_chars: Optional[int]) -> Callable:
    """Build a ``(args, **kw)`` handler closure from an inline spec.

    Raises on an unknown backend type so the caller can error-skip the
    processor (never silently).  ``args`` is the tool's parameter dict; ``**kw``
    carries caller-injected metadata (task_id / user_task / ...) which we ignore,
    exactly like every other registry handler.
    """
    itype = spec.get("type")
    if itype == "shell":
        argv_tmpl = spec.get("command") or []
        if not isinstance(argv_tmpl, list) or not all(isinstance(t, str) for t in argv_tmpl):
            raise ValueError("shell inline 'command' must be a list of strings")
        timeout = int(spec.get("timeout", 30))
        def _run_shell_handler(args: Dict[str, Any], **kw: Any) -> str:
            argv = [_subst(tok, args) for tok in argv_tmpl]
            # Closure-level guarantee: cap the returned string at max_chars
            # (the executor already truncates the payload; this is a belt-and-
            # suspenders so ANY return path stays bounded).
            return _truncate(_run_shell(argv, timeout, max_chars), max_chars)
        _run_shell_handler.__name__ = "_inline_shell_handler"
        return _run_shell_handler

    if itype == "http":
        method = str(spec.get("method", "GET")).upper()
        url_arg = spec.get("url_arg")  # whole-URL-from-param escape hatch (scheme-checked)
        static_url = spec.get("url")
        timeout = int(spec.get("timeout", 30))
        def _run_http_handler(args: Dict[str, Any], **kw: Any) -> str:
            target = args.get(url_arg) if url_arg else static_url
            q = {k: _subst(str(v), args) for k, v in (spec.get("query") or {}).items()}
            hdrs = {k: _subst(str(v), args) for k, v in (spec.get("headers") or {}).items()}
            body = _subst(spec["body"], args) if spec.get("body") else None
            # Closure-level guarantee: cap the returned string at max_chars.
            return _truncate(_run_http(method, target, q, hdrs, body, timeout, max_chars), max_chars)
        _run_http_handler.__name__ = "_inline_http_handler"
        return _run_http_handler

    raise ValueError(f"unknown inline type '{itype}' (expected 'shell' or 'http')")


# ── Parsing ─────────────────────────────────────────────────────────────
def _parse_tool_yaml(path: Path) -> Optional[ToolProcessor]:
    """Parse a single YAML file into a ToolProcessor.

    Returns ``None`` for: unparseable files, wrong api version, or any kind
    other than ``tool`` (those are handled by the prompt loader).  Never raises.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to parse tool processor %s: %s", path, e)
        return None

    if not data or not isinstance(data, dict):
        return None

    # api version (v1 only; unknown major rejected)
    api = data.get("api", "vermes.processor/v1")
    if not api.startswith("vermes.processor/v"):
        logger.warning("Unknown api '%s' in %s, skipping", api, path)
        return None
    api_major = api.split("/v")[-1].split(".")[0]
    if api_major not in ("1",):
        logger.warning("Unsupported processor api major '%s' in %s, skipping", api_major, path)
        return None

    kind = data.get("kind", "prompt_fragment")
    if kind != TOOL_KIND:
        return None  # not a tool processor; prompt loader handles it

    name = data.get("name", path.stem)
    proc_id = data.get("id", "")
    enabled = data.get("enabled", True)
    priority = data.get("priority", 100)
    toolset = data.get("toolset", "user")

    # schema is REQUIRED (OpenAI-format function object)
    schema = data.get("schema")
    if not isinstance(schema, dict) or "name" not in schema:
        logger.error(
            "Tool processor %s: missing/invalid 'schema' (OpenAI format with "
            "'name') — SKIPPING", name,
        )
        return None

    handler = data.get("handler") or {}
    handler_ref = handler.get("ref") if isinstance(handler, dict) else None

    # ── handler.inline (Phase 2.5 declarative execution) ──
    # Parse + validate now so a malformed spec is rejected at parse time
    # (error-skip, never silent).  Resolution into a callable happens later in
    # register_tool_processors via _make_inline_handler.
    inline_spec: Optional[Dict[str, Any]] = None
    inline_raw = handler.get("inline") if isinstance(handler, dict) else None
    if inline_raw is not None:
        if not isinstance(inline_raw, dict):
            logger.error("Tool processor %s: handler.inline must be a mapping — SKIPPING", name)
            return None
        itype = inline_raw.get("type")
        if itype not in ("shell", "http"):
            logger.error(
                "Tool processor %s: handler.inline.type '%s' invalid (expected "
                "'shell' or 'http') — SKIPPING", name, itype,
            )
            return None
        if itype == "shell":
            cmd = inline_raw.get("command")
            if not isinstance(cmd, list) or not all(isinstance(t, str) for t in cmd):
                logger.error(
                    "Tool processor %s: shell inline 'command' must be a list of "
                    "strings — SKIPPING", name,
                )
                return None
        else:  # http
            if not inline_raw.get("url_arg") and not inline_raw.get("url"):
                logger.error(
                    "Tool processor %s: http inline requires 'url' (static base) "
                    "or 'url_arg' (param) — SKIPPING", name,
                )
                return None
            if inline_raw.get("url") and not str(inline_raw["url"]).startswith(("http://", "https://")):
                logger.error(
                    "Tool processor %s: http inline 'url' must be http/https — SKIPPING",
                    name,
                )
                return None
        inline_spec = dict(inline_raw)
    availability = data.get("availability") or {}
    if not isinstance(availability, dict):
        availability = {}
    check_fn_ref = availability.get("check_fn_ref")
    requires_env = availability.get("requires_env") or []
    if isinstance(requires_env, str):
        requires_env = [requires_env]
    if not isinstance(requires_env, list):
        requires_env = []

    conditions = data.get("conditions") or {}
    if not isinstance(conditions, dict):
        conditions = {}

    lifecycle = data.get("lifecycle") or {"hooks": []}
    if not isinstance(lifecycle, dict):
        lifecycle = {"hooks": []}
    hooks = lifecycle.get("hooks", []) or []
    valid_hooks: List[str] = []
    if hooks:
        from vermes_cli.plugins import VALID_HOOKS
        for h in hooks:
            event = h.split(":", 1)[0] if isinstance(h, str) else ""
            if event in VALID_HOOKS:
                if event not in valid_hooks:
                    valid_hooks.append(event)
            else:
                logger.warning(
                    "Tool processor %s has invalid lifecycle hook '%s' (valid: %s)",
                    name, h, sorted(VALID_HOOKS),
                )
    lifecycle["hooks"] = valid_hooks

    is_async = bool(data.get("is_async", False))
    emoji = data.get("emoji", "") or ""
    max_result_size_chars = data.get("max_result_size_chars")

    # governance: merge defaults (tools default L2 — fail-closed, real side effects)
    gov_defaults = {
        "risk_tier": "L2", "replaceable": True, "mutable_by_aegis": True,
        "rollback": "enabled", "critic_guarded": False, "hash": "auto",
    }
    governance = data.get("governance") or {}
    if not isinstance(governance, dict):
        governance = {}
    for k, v in gov_defaults.items():
        governance.setdefault(k, v)

    # Resolve ``hash: auto`` into the real canonical digest (same as prompt loader).
    _declared = governance.get("hash")
    _computed = compute_manifest_hash(data)
    if isinstance(_declared, str) and _declared not in ("", "auto") and _declared != _computed:
        logger.warning(
            "Tool processor %s declares hash %s but content hashes to %s (using computed)",
            name, _declared, _computed,
        )
        governance["declared_hash"] = _declared
    governance["hash"] = _computed

    # Phase 2.5 hardening: an inline handler's *definition IS its execution
    # body — editing the YAML edits what the agent runs.  That is strictly more
    # dangerous than a fixed ``handler.ref`` to an existing function, so inline
    # tools are force-clamped to L2 (per-rewrite human confirmation).  This is a
    # loader-enforced floor; it does NOT read the manifest's own declared tier
    # (that would be self-attested governance).  L0/L1 declared → bumped to L2.
    if inline_spec is not None:
        rt = governance.get("risk_tier")
        if rt in ("L0", "L1"):
            logger.warning(
                "Tool processor %s: inline handler risk_tier '%s' clamped to L2 "
                "(inline executes commands/requests — not downgradable)",
                name, rt,
            )
            governance["risk_tier"] = "L2"

    metadata = data.get("metadata", {"author": "unknown", "source": "builtin"})

    return ToolProcessor(
        name=name, kind=kind, id=proc_id, enabled=enabled, priority=priority,
        toolset=toolset, schema=schema, handler_ref=handler_ref,
        inline_spec=inline_spec,
        check_fn_ref=check_fn_ref, requires_env=list(requires_env),
        is_async=is_async, emoji=emoji,
        max_result_size_chars=max_result_size_chars,
        availability=availability, conditions=conditions, lifecycle=lifecycle,
        governance=governance, metadata=metadata, validated_hooks=valid_hooks,
        source_path=path,
    )


def load_tool_processors() -> List[ToolProcessor]:
    """Load all tool processors. User overrides built-in by id. Cached."""
    global _tool_processors_cache, _tool_processors_generation

    if _tool_processors_cache is not None:
        return _tool_processors_cache

    with _tool_cache_lock:
        if _tool_processors_cache is not None:
            return _tool_processors_cache

        by_id: Dict[str, ToolProcessor] = {}

        # 1. Built-in processors (flat dir: vermes_cli/processors/*.yaml)
        builtin_dir = _get_builtin_dir()
        if builtin_dir.exists():
            for yaml_file in sorted(builtin_dir.glob("*.yaml")):
                proc = _parse_tool_yaml(yaml_file)
                if proc:
                    proc.builtin = True
                    by_id[proc.effective_id] = proc
                    logger.debug("Loaded built-in tool processor: %s (id=%s)", proc.name, proc.effective_id)

        # 2. User processors (subdir + flat compat, same as prompt loader)
        user_dir = _get_user_dir()
        if user_dir.exists():
            user_files = []
            user_files.extend(sorted(user_dir.glob("*/processor.yaml")))
            user_files.extend(sorted(f for f in user_dir.glob("*.yaml") if f.name != "processor.yaml"))
            for yaml_file in user_files:
                proc = _parse_tool_yaml(yaml_file)
                if proc:
                    key = proc.effective_id
                    existing = by_id.get(key)
                    if existing and not existing.governance.get("replaceable", True):
                        logger.warning(
                            "User tool processor '%s' (id=%s) ignored: built-in is non-replaceable",
                            proc.name, key,
                        )
                        continue
                    proc.builtin = False
                    by_id[key] = proc
                    logger.debug("Loaded user tool processor: %s (id=%s, overrides=%s)", proc.name, key, existing is not None)

        # Deterministic sort: priority → id (no layer concept for tools).
        result = sorted(by_id.values(), key=lambda p: (p.priority, p.effective_id))
        _tool_processors_cache = result
        logger.info(
            "Loaded %d tool processors (%d built-in, %d user)",
            len(result),
            sum(1 for p in result if p.builtin),
            sum(1 for p in result if not p.builtin),
        )
        return result


# ── Registration into the tool registry ─────────────────────────────────
def register_tool_processors(reg: Any = None) -> int:
    """Register all enabled tool processors into the tool registry.

    Called once at startup, after ``discover_builtin_tools()``.  Returns the
    number of tool processors successfully registered.

    Override semantics (decision C): a user processor whose ``id`` matches a
    pre-registered tool keeps the built-in Python handler/check unless it
    explicitly specifies a different ``handler.ref`` / ``check_fn_ref``.
    """
    from tools.registry import registry as _reg
    reg = reg or _reg

    procs = load_tool_processors()
    registered = 0
    TOOL_LIFECYCLE_HOOKS.clear()
    _registered_hook_keys.clear()

    # Before re-registering, strip any hook recorders WE previously appended
    # to PluginManager._hooks.  register_tool_processors() is also called by
    # the watcher on hot-reload, and invoke_hook iterates _hooks directly — so
    # without this we would accumulate duplicate callbacks on every reload.
    try:
        from vermes_cli.plugins import get_plugin_manager
        _pm = get_plugin_manager()
        for _ev in list(_pm._hooks.keys()):
            _pm._hooks[_ev] = [
                cb for cb in _pm._hooks[_ev]
                if not getattr(cb, "_vermes_tool_hook", None)
            ]
    except Exception:
        _pm = None

    for p in procs:
        if not p.enabled or p.kind != TOOL_KIND:
            continue
        tool_name = p.effective_id
        existing = reg.get_entry(tool_name)

        # ── resolve handler (three branches: ref → inline → keep builtin) ──
        handler: Optional[Callable] = None
        if p.handler_ref:
            try:
                handler = _resolve_handler_ref(p.handler_ref)
            except Exception as e:
                logger.error(
                    "Tool processor '%s': handler.ref '%s' unresolvable (%s) — SKIPPING",
                    tool_name, p.handler_ref, type(e).__name__ + ": " + str(e),
                )
                continue
        elif p.inline_spec is not None:
            # Phase 2.5: build a declarative handler closure.  shell inline is
            # gated by approvals.allow_inline_shell (default False); http inline
            # is always allowed but was already force-clamped to L2 at parse.
            if p.inline_spec.get("type") == "shell" and not _allow_inline_shell():
                logger.error(
                    "Tool processor '%s': shell inline blocked "
                    "(approvals.allow_inline_shell=False) — SKIPPING",
                    tool_name,
                )
                continue
            try:
                handler = _make_inline_handler(p.inline_spec, p.max_result_size_chars)
            except Exception as e:
                logger.error(
                    "Tool processor '%s': inline handler build failed (%s) — SKIPPING",
                    tool_name, type(e).__name__ + ": " + str(e),
                )
                continue
        elif existing is not None:
            # Override keeps the built-in Python handler (decision C).
            handler = existing.handler
        else:
            logger.error(
                "Tool processor '%s': no handler.ref/inline and tool not pre-registered — SKIPPING",
                tool_name,
            )
            continue

        # ── resolve check_fn ──
        check_fn: Optional[Callable] = None
        if p.check_fn_ref:
            try:
                check_fn = _resolve_ref_generic(p.check_fn_ref)
            except Exception as e:
                logger.error(
                    "Tool processor '%s': check_fn_ref '%s' unresolvable (%s) — "
                    "falling back to env check", tool_name, p.check_fn_ref, e,
                )
                check_fn = None
        if check_fn is None:
            if p.requires_env:
                check_fn = _make_env_check(p.requires_env)
            elif existing is not None:
                check_fn = existing.check_fn

        # ── schema (use processor schema if present, else keep existing) ──
        schema = p.schema if p.schema else (existing.schema if existing else None)
        if not schema:
            logger.error("Tool processor '%s': no schema available — SKIPPING", tool_name)
            continue

        req_env = p.requires_env or (existing.requires_env if existing else [])
        is_async = p.is_async or (existing.is_async if existing else False)
        emoji = p.emoji or (existing.emoji if existing else "")
        max_chars = (
            p.max_result_size_chars
            if p.max_result_size_chars is not None
            else (existing.max_result_size_chars if existing else None)
        )
        toolset = p.toolset if p.toolset != "user" else (existing.toolset if existing else "user")

        try:
            reg.register(
                name=tool_name,
                toolset=toolset,
                schema=schema,
                handler=handler,
                check_fn=check_fn,
                requires_env=req_env,
                is_async=is_async,
                emoji=emoji,
                max_result_size_chars=max_chars,
                override=True,
            )
        except Exception as e:
            logger.error("Tool processor '%s': register() failed: %s — SKIPPING", tool_name, e)
            continue

        # ── lifecycle hooks: validate already done; register into plugin mgr ──
        # NOTE: PluginManager.invoke_hook reads ``self._hooks`` directly. The
        # public ``Plugin.register_hook`` writes to ``self._manager._hooks``
        # (a Plugin instance's view), so for a top-level recorder we append
        # straight to the singleton's ``_hooks`` — the exact dict invoke_hook
        # iterates. This is what makes the tool's declared hooks actually
        # FIRE during model_tools' dispatch (which calls invoke_hook).
        if p.validated_hooks:
            TOOL_LIFECYCLE_HOOKS[tool_name] = p.validated_hooks
            try:
                from vermes_cli.plugins import get_plugin_manager
                pm = get_plugin_manager()
                for ev in p.validated_hooks:
                    key = (tool_name, ev)
                    if key not in _registered_hook_keys:
                        rec = _make_hook_recorder(tool_name, ev)
                        # Tag so the watcher-driven re-register can de-dup us.
                        rec._vermes_tool_hook = (tool_name, ev)  # type: ignore[attr-defined]
                        pm._hooks.setdefault(ev, []).append(rec)
                        _registered_hook_keys.add(key)
            except Exception as e:
                logger.debug("Tool processor '%s': hook registration skipped: %s", tool_name, e)

        registered += 1
        logger.info(
            "Registered tool processor '%s' (builtin=%s, hooks=%s)",
            tool_name, p.builtin, p.validated_hooks,
        )

    return registered


# ── Introspection / cache control ───────────────────────────────────────
def get_tool_lifecycle_hooks(tool_name: str) -> List[str]:
    """Return the validated lifecycle hook events declared for *tool_name*."""
    return list(TOOL_LIFECYCLE_HOOKS.get(tool_name, []))


def get_hook_fires() -> List[Dict[str, Any]]:
    """Return the observability log of hook fires (for tests / debugging)."""
    with _hook_fires_lock:
        return list(_hook_fires)


def clear_hook_fires() -> None:
    with _hook_fires_lock:
        _hook_fires.clear()


def invalidate_cache() -> None:
    """Force reload on next access. Called by the processor watcher."""
    global _tool_processors_cache, _tool_processors_generation
    with _tool_cache_lock:
        _tool_processors_cache = None
        _tool_processors_generation += 1
        TOOL_LIFECYCLE_HOOKS.clear()
        _registered_hook_keys.clear()
        logger.info("Tool processor cache invalidated (generation=%d)", _tool_processors_generation)


def get_generation() -> int:
    return _tool_processors_generation
