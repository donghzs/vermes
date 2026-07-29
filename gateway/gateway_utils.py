"""Gateway utility functions extracted from run.py.

Module-level helper functions shared between GatewayRunner and mixins.
Module-level variables (_vermes_home, _AGENT_PENDING_SENTINEL, etc.)
remain in gateway/run.py for backward-compatible monkeypatching.
"""

import logging
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
