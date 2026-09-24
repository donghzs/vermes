"""Environment variable passthrough registry.

Skills that declare ``required_environment_variables`` in their frontmatter
need those vars available in sandboxed execution environments (execute_code,
terminal).  By default both sandboxes strip secrets from the child process
environment for security.  This module provides a session-scoped allowlist
so skill-declared vars (and user-configured overrides) pass through.

Two sources feed the allowlist:

1. **Skill declarations** — when a skill is loaded via ``skill_view``, its
   ``required_environment_variables`` are registered here automatically.
2. **User config** — ``terminal.env_passthrough`` in config.yaml lets users
   explicitly allowlist vars for non-skill use cases.

Both ``code_execution_tool.py`` and ``tools/environments/local.py`` consult
:func:`is_env_passthrough` before stripping a variable.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Iterable
from vermes_cli.config import cfg_get
from tools.session_env_registry import get_session_env_registry

logger = logging.getLogger(__name__)

# Session-scoped set of env var names that should pass through to sandboxes.
# Backed by ContextVar to prevent cross-session data bleed in the gateway pipeline.
# Kept for backward compatibility; SessionEnvRegistry is the canonical store.
_allowed_env_vars_var: ContextVar[set[str]] = ContextVar("_allowed_env_vars")


def _get_allowed() -> set[str]:
    """Get or create the allowed env vars set for the current context/session.

    Delegates to SessionEnvRegistry; also mirrors into the legacy ContextVar
    so any code reading ``_allowed_env_vars_var`` directly continues to work.
    """
    registry = get_session_env_registry()
    # Return the live set from registry so mutations persist
    return registry._allowed_env_vars


# Cache for the config-based allowlist (loaded once per process).
_config_passthrough: frozenset[str] | None = None


def _is_vermes_provider_credential(name: str) -> bool:
    """True if ``name`` is a Vermes-managed provider credential (API key,
    token, or similar) per ``_vermes_PROVIDER_ENV_BLOCKLIST``.

    Skill-declared ``required_environment_variables`` frontmatter must
    not be able to override this list — that was the bypass in
    GHSA-rhgp-j443-p4rf where a malicious skill registered
    ``ANTHROPIC_TOKEN`` / ``OPENAI_API_KEY`` as passthrough and received
    the credential in the ``execute_code`` child process, defeating the
    sandbox's scrubbing guarantee.

    Non-Vermes API keys (TENOR_API_KEY, NOTION_TOKEN, etc.) are NOT
    in the blocklist and remain legitimately registerable — skills that
    wrap third-party APIs still work.
    """
    try:
        from tools.environments.local import _vermes_PROVIDER_ENV_BLOCKLIST
    except Exception:
        return False
    return _is_env_blocklisted(name, _vermes_PROVIDER_ENV_BLOCKLIST)


def _is_env_blocklisted(name: str, blocklist: frozenset[str]) -> bool:
    """Case-insensitive blocklist membership check.

    Windows environment blocks resolve names case-insensitively, so a
    case-variant registration (e.g. ``openai_api_key``) would be accepted
    by a case-sensitive ``in`` check but resolve to the real
    ``OPENAI_API_KEY`` via ``os.getenv()``, tunnelling the credential
    into SSH/Docker children (GHSA-rhgp-j443-p4rf primitive, upstream
    fix b534f4b8c8)."""
    if name in blocklist:
        return True
    folded = name.casefold()
    return any(folded == n.casefold() for n in blocklist)


# Authorization gates: the env names platform adapters read to decide WHO may
# talk to the agent (allow/deny lists, allow-all opt-ins, bot policy, channel
# scoping). They are NOT credentials — the secret scrub blocklist only names a
# handful of them (SLACK_ALLOWED_USERS, GATEWAY_ALLOWED_USERS ...) — and a
# child spawned FOR profile B from a process that loaded profile A's gates
# (a gateway, the kanban dispatcher, the post-update per-profile gateway
# restart) would enforce A's channel/user/role list as its own. Matched by
# shape so a gate added to any adapter is covered without a second edit;
# ``VERMES_*`` never counts (``VERMES_ALLOW_PRIVATE_URLS`` is a process
# setting, not an adapter gate). Mirrors upstream #113270.
_PROFILE_GATE_ENV_MARKERS = (
    "_ALLOWED_", "_ALLOW_ALL_", "_ALLOW_FROM", "_ALLOW_BOTS", "_ALLOW_PUBLIC_",
    "_IGNORED_CHANNELS", "_NO_THREAD_CHANNELS", "_FREE_RESPONSE_CHANNELS",
    "_BACKFILL_CHANNELS", "_GROUP_ALLOWED",
)


def is_profile_gate_env(name: str) -> bool:
    """True for a platform authorization gate (``DISCORD_ALLOWED_CHANNELS``,
    ``TELEGRAM_ALLOW_ALL_USERS``, ``GATEWAY_ALLOWED_USERS``,
    ``WHATSAPP_GROUP_ALLOW_FROM`` ...) — profile-scoped policy a child acting
    for ANOTHER profile must never inherit."""
    upper = name.upper()
    if upper.startswith("VERMES_") or upper.startswith("_"):
        return False
    return any(marker in upper for marker in _PROFILE_GATE_ENV_MARKERS)


def strip_profile_gate_env(env: dict) -> dict:
    """Drop every authorization gate from *env* in place (see
    :func:`is_profile_gate_env`). Returns *env* for chaining."""
    for key in [k for k in env if is_profile_gate_env(k)]:
        del env[key]
    return env



def register_env_passthrough(var_names: Iterable[str]) -> None:
    """Register environment variable names as allowed in sandboxed environments.

    Typically called when a skill declares ``required_environment_variables``.

    Variables that are Vermes-managed provider credentials (from
    ``_vermes_PROVIDER_ENV_BLOCKLIST``) are rejected here to preserve
    the ``execute_code`` sandbox's credential-scrubbing guarantee per
    GHSA-rhgp-j443-p4rf. A skill that needs to talk to a Vermes-managed
    provider should do so via the agent's main-process tools (web_search,
    web_extract, etc.) where the credential remains safely in the main
    process.

    Non-Vermes third-party API keys (TENOR_API_KEY, NOTION_TOKEN, etc.)
    pass through normally — they were never in the sandbox scrub list.
    """
    for name in var_names:
        name = name.strip()
        if not name:
            continue
        if _is_vermes_provider_credential(name):
            logger.warning(
                "env passthrough: refusing to register Vermes provider "
                "credential %r (blocked by _vermes_PROVIDER_ENV_BLOCKLIST). "
                "Skills must not override the execute_code sandbox's "
                "credential scrubbing; see GHSA-rhgp-j443-p4rf.",
                name,
            )
            continue
        _get_allowed().add(name)
        # Also register with the unified SessionEnvRegistry (canonical store)
        registry = get_session_env_registry()
        registry.register_env_passthrough([name])
        logger.debug("env passthrough: registered %s", name)


def _load_config_passthrough() -> frozenset[str]:
    """Load ``tools.env_passthrough`` from config.yaml (cached)."""
    global _config_passthrough
    if _config_passthrough is not None:
        return _config_passthrough

    result: set[str] = set()
    try:
        from vermes_cli.config import read_raw_config
        cfg = read_raw_config()
        passthrough = cfg_get(cfg, "terminal", "env_passthrough")
        if isinstance(passthrough, list):
            for item in passthrough:
                if isinstance(item, str) and item.strip():
                    result.add(item.strip())
    except Exception as e:
        logger.debug("Could not read tools.env_passthrough from config: %s", e)

    _config_passthrough = frozenset(result)
    return _config_passthrough


def is_env_passthrough(var_name: str) -> bool:
    """Check whether *var_name* is allowed to pass through to sandboxes.

    Returns ``True`` if the variable was registered by a skill or listed in
    the user's ``tools.env_passthrough`` config.
    """
    if var_name in _get_allowed():
        return True
    return var_name in _load_config_passthrough()


def require_is_env_passthrough():
    """Return :func:`is_env_passthrough` **fail-closed** (L-028 / `547fff75003a`).

    Upstream dropped the ``try/except`` around the scope overlay: a failure
    there must be loud, not silently drop the declared secret again (#114209).
    Vermes had the same shape — ``except Exception: _is_passthrough = lambda _: False``
    made an import/config failure look like "nothing is declared" and stripped
    every skill-declared passthrough var from the child.
    """
    return is_env_passthrough


def source_supplied_names() -> frozenset[str]:
    """Names declared via skill ``required_environment_variables`` / config only.

    Split out of the passthrough union (L-032 / `dcdbcb8a2b14`) so callers can
    see "what did the user/skill declare" without mixing in runtime allowlist
    mutations. Same set as :func:`get_all_passthrough` today; kept as a separate
    surface so a future source-provenance loader can extend it without changing
    the union API.
    """
    return get_all_passthrough()


def scoped_passthrough_additions(present: Iterable[str]) -> dict[str, str]:
    """Declared passthrough names a profile ``.env`` supplies but *present* lacks.

    L-029 / `802a9975d283` (rewrite — Vermes has no ``agent.secret_scope``).
    A routed profile's ``.env`` never enters the process env, so a name-by-name
    filter over ``os.environ`` can only forward a declared name the LAUNCH
    profile also happens to define. Reads the **target profile home's** ``.env``
    alone (``VERMES_HOME`` / ``get_vermes_home()``); empty without a profile
    home so single-profile spawns stay byte-identical.

    Raises on reader failure (L-028 fail-closed) instead of silently dropping
    the declared secret.
    """
    present = set(present)
    names = source_supplied_names()
    if not names:
        return {}
    home = None
    try:
        from vermes_constants import get_vermes_home
        home = get_vermes_home()
    except Exception as exc:
        raise RuntimeError(
            f"scoped_passthrough_additions: cannot resolve profile home: {exc}"
        ) from exc
    if home is None:
        return {}
    env_file = home / ".env"
    if not env_file.is_file():
        return {}
    additions: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key in present or key not in names:
            continue
        additions[key] = val
    return additions


def get_all_passthrough() -> frozenset[str]:
    """Return the union of skill-registered and config-based passthrough vars."""
    # Read from the unified registry (canonical store)
    registry = get_session_env_registry()
    return frozenset(registry._allowed_env_vars) | _load_config_passthrough()


def clear_env_passthrough() -> None:
    """Reset the skill-scoped allowlist (e.g. on session reset)."""
    registry = get_session_env_registry()
    # Clear only env passthrough from the registry
    registry._allowed_env_vars.clear()
    # Also clear legacy ContextVar (already same object via _get_allowed, but explicit for safety)
    _get_allowed().clear()


