"""Gateway utility functions extracted from run.py.

Module-level helper functions shared between GatewayRunner and mixins.
Module-level variables (_vermes_home, _AGENT_PENDING_SENTINEL, etc.)
remain in gateway/run.py for backward-compatible monkeypatching.
"""

import logging
import os
import re
import sys
from typing import Any, Optional

from gateway.config import Platform

logger = logging.getLogger(__name__)

# Lazy-access helpers for run.py module-level variables.
# These are resolved at call time to support test monkeypatching.


def _get_vermes_home():
    """Return the current _vermes_home from gateway.run (monkeypatchable)."""
    from gateway.run import _vermes_home
    return _vermes_home


def _telegramize_command_mentions(text: str, platform: Any) -> str:
    """Rewrite slash-command mentions to Telegram-valid command names."""
    platform_value = getattr(platform, "value", platform)
    if platform_value != "telegram":
        return text

    from vermes_cli.commands import _sanitize_telegram_name

    _TELEGRAM_COMMAND_MENTION_RE = re.compile(r"(?<![\w:/])/([A-Za-z0-9][A-Za-z0-9_-]*)")

    def _replace(match: re.Match[str]) -> str:
        sanitized = _sanitize_telegram_name(match.group(1))
        return f"/{sanitized}" if sanitized else match.group(0)

    return _TELEGRAM_COMMAND_MENTION_RE.sub(_replace, text)


def _home_target_env_var(platform_name: str) -> str:
    """Return the configured home-target env var for a platform."""
    from cron.scheduler import _resolve_home_env_var

    resolved = _resolve_home_env_var(platform_name)
    if resolved:
        return resolved
    return f"{platform_name.upper()}_HOME_CHANNEL"


def _home_thread_env_var(platform_name: str) -> str:
    """Return the optional thread/topic env var for a platform home target."""
    return f"{_home_target_env_var(platform_name)}_THREAD_ID"


# Legacy env var names accepted when the primary home-channel env var is unset.
# Renames that predate the stabilised platform key (qq → qqbot).
_LEGACY_HOME_TARGET_ENV_VARS = {
    "QQBOT_HOME_CHANNEL": "QQ_HOME_CHANNEL",
}


def config_home_channel_chat_id(platform_name: str, config: Any = None) -> str:
    """Read ``home_channel.chat_id`` for a platform from config alone (no env).

    Accepts either an in-process ``GatewayConfig`` (``/sethome`` keeps it in
    sync via ``platform_config.home_channel``) or a raw ``config.yaml`` dict.
    Returns "" when the platform has no home channel.

    Why this exists: ``/sethome`` persists to ``~/.vermes/.env``, but a
    long-running gateway process does not re-read .env — its ``os.environ``
    predates the write. The in-process config is the only fresh view, so any
    consumer that reads env alone will keep reporting "no home channel".
    """
    getter = getattr(config, "get_home_channel", None) if config is not None else None
    if callable(getter):
        try:
            home = getter(Platform(platform_name))
        except Exception:
            home = None
        chat_id = getattr(home, "chat_id", None)
        return str(chat_id) if chat_id else ""

    cfg = config if isinstance(config, dict) else _load_gateway_config()
    if not isinstance(cfg, dict):
        return ""
    platforms = cfg.get("platforms") or {}
    if not isinstance(platforms, dict):
        return ""
    entry = platforms.get((platform_name or "").lower()) or {}
    if not isinstance(entry, dict):
        return ""
    home = entry.get("home_channel") or {}
    if isinstance(home, dict):
        chat_id = home.get("chat_id")
        return str(chat_id) if chat_id else ""
    return ""


def env_home_channel_chat_id(platform_name: str) -> str:
    """The env (and legacy env) leg of home-channel resolution, nothing else.

    Exists purely so hot paths can answer "already configured?" without
    touching config.yaml. Calling ``resolve_home_channel_chat_id`` for every
    inbound message would make A3's prompt / A7's auto-set parse the whole
    config file once per message (``_load_gateway_config`` is disk IO). Env is
    the highest-priority leg, so a non-empty result here is semantically
    identical to a non-empty ``resolve_home_channel_chat_id`` — callers may
    treat "env hit" as "fully resolved" and stop before any disk read.

    Do not let this drift from the resolver below: it is literally the first
    half of it, extracted.
    """
    name = (platform_name or "").lower()
    env_var = _home_target_env_var(name)
    if not env_var:
        return ""
    value = (os.getenv(env_var) or "").strip()
    if not value:
        legacy = _LEGACY_HOME_TARGET_ENV_VARS.get(env_var)
        if legacy:
            value = (os.getenv(legacy) or "").strip()
    return value


def resolve_home_channel_chat_id(platform_name: str, config: Any = None) -> str:
    """Single source of truth for "is a home channel configured for <platform>".

    Order: env var → legacy env var → config (GatewayConfig or config.yaml).
    The env leg stays first so an explicit operator override keeps winning; the
    config leg is the fallback that makes a config-only home channel visible to
    the two call sites that historically read ``os.getenv`` only
    (the new-session notice prompt and cron delivery).
    """
    env_hit = env_home_channel_chat_id(platform_name)
    if env_hit:
        return env_hit
    return config_home_channel_chat_id((platform_name or "").lower(), config)


AUTO_SET_HOME_CHANNEL_ENV = "VERMES_AUTO_SET_HOME_CHANNEL"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def auto_set_home_channel_enabled(platform_name: str = "") -> bool:
    """Kill switch for A7 (first-DM auto home channel). Default **on**.

    Silent design decision — why this is env-only and does NOT read
    ``config.yaml``:
      * Nothing renders a ``gateway.auto_set_home_channel`` field today — not
        the desktop GUI (its Settings panel only covers credentials and the
        home-channel picker added in M4), and not any slash command. A config
        leg would therefore be reachable only by hand-editing YAML, which is
        the same audience as this env var.
      * Defaulting to **on** is deliberate: by the time a message reaches the
        A7 decision point it has already passed ``_is_user_authorized``
        (``message_handler_mixin.py:257-302``), so the sender is an explicitly
        allow-listed operator, and the caller further restricts itself to DMs.
        The residual risk is bounded ("whichever authorized user DMs first
        wins the cron delivery target") and reversible — ``/sethome`` in
        another chat moves it. Being off by default would defeat A7 itself,
        whose entire point is that GUI-only users never learn ``/sethome``.
      * To add a config leg later: extend this function with
        ``_load_gateway_config().get("gateway", {}).get("auto_set_home_channel")``
        **after** the env check, and give it a GUI field in the same change.
    """
    raw = (os.getenv(AUTO_SET_HOME_CHANNEL_ENV) or "").strip().lower()
    if not raw:
        return True
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    # Unparseable value: fail **closed** rather than guessing — a mistyped
    # "flase" should disable auto-writing the user's config, not enable it.
    logger.warning(
        "[home-channel] %s=%r unrecognized, treating as disabled",
        AUTO_SET_HOME_CHANNEL_ENV,
        raw,
    )
    return False


def _platform_config_key(platform: "Platform") -> str:
    """Map a Platform enum to its config.yaml key."""
    return "cli" if platform == Platform.LOCAL else platform.value


def _load_gateway_config() -> dict:
    """Load and parse ~/.vermes/config.yaml, returning {} on any error."""
    _vermes_home = _get_vermes_home()
    config_path = _vermes_home / 'config.yaml'
    try:
        from vermes_cli.config import get_config_path, read_raw_config
        if config_path == get_config_path():
            return read_raw_config()
    except Exception:
        pass

    try:
        if config_path.exists():
            import yaml
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
    except Exception:
        logger.debug("Could not load gateway config from %s", config_path)
    return {}


def _resolve_gateway_model(config: dict | None = None) -> str:
    """Read model from config.yaml."""
    cfg = config if config is not None else _load_gateway_config()
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, str):
        return model_cfg
    elif isinstance(model_cfg, dict):
        return model_cfg.get("default") or model_cfg.get("model") or ""
    return ""


def _resolve_vermes_bin() -> Optional[list[str]]:
    """Resolve the Vermes update command as argv parts."""
    import shutil

    VERMES_bin = shutil.which("Vermes")
    if VERMES_bin:
        return [VERMES_bin]

    try:
        import importlib.util

        if importlib.util.find_spec("vermes_cli") is not None:
            return [sys.executable, "-m", "vermes_cli.main"]
    except Exception:
        pass

    return None
