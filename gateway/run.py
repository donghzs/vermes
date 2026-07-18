"""
Gateway runner - entry point for messaging platform integrations.

This module provides:
- start_gateway(): Start all configured platform adapters
- GatewayRunner: Main class managing the gateway lifecycle

Usage:
    # Start the gateway
    python -m gateway.run
    
    # Or from CLI
    python cli.py --gateway
"""

# IMPORTANT: hermes_bootstrap must be the very first import — UTF-8 stdio
# on Windows.  No-op on POSIX.  See hermes_bootstrap.py for full rationale.
try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    # Graceful fallback when hermes_bootstrap isn't registered in the venv
    # yet — happens during partial ``hermes update`` where git-reset landed
    # new code but ``uv pip install -e .`` didn't finish.  Missing bootstrap
    # means UTF-8 stdio setup is skipped on Windows; POSIX is unaffected.
    pass

import asyncio
import dataclasses
import inspect
import json
import logging
import os
import re
import shlex
import sys
import signal
import tempfile
import threading
import time
import sqlite3
from collections import OrderedDict
from contextvars import copy_context
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Any, List, Union

# P2.1 audit (2026-07-18): the ``agent.account_usage`` import
# (fetch_account_usage, render_account_usage_lines) previously lived here at
# module top, guarded by a comment claiming tests patched
# ``gateway.run.fetch_account_usage``. That constraint is STALE —
# tests/gateway/test_usage_command.py now patches
# ``gateway.slash_handlers.system_handlers.fetch_account_usage`` instead, and
# nothing in this module references either symbol. The import was pure dead
# weight that pulled the OpenAI SDK chain (~66–230 ms) at every gateway boot
# for no benefit. Removed; /usage resolves the symbol through the slash
# handler path which imports it directly where needed.
from agent.async_utils import safe_schedule_threadsafe
from agent.i18n import t
from hermes_cli.config import cfg_get

# --- Agent cache tuning ---------------------------------------------------
# Bounds the per-session AIAgent cache to prevent unbounded growth in
# long-lived gateways (each AIAgent holds LLM clients, tool schemas,
# memory providers, etc.).  LRU order + idle TTL eviction are enforced
# from _enforce_agent_cache_cap() and _session_expiry_watcher() below.
_AGENT_CACHE_MAX_SIZE = 128
_AGENT_CACHE_IDLE_TTL_SECS = 3600.0  # evict agents idle for >1h
_PLATFORM_CONNECT_TIMEOUT_SECS_DEFAULT = 30.0
_ADAPTER_DISCONNECT_TIMEOUT_SECS_DEFAULT = 5.0
_TELEGRAM_COMMAND_MENTION_RE = re.compile(r"(?<![\w:/])/([A-Za-z0-9][A-Za-z0-9_-]*)")

_TELEGRAM_NOISY_STATUS_RE = re.compile(
    r"("  # transient/auxiliary status that should stay in logs, not Telegram chat
    r"auxiliary\s+.+\s+failed"
    r"|compression\s+summary\s+failed"
    r"|fallback\s+context\s+marker"
    r"|configured\s+compression\s+model\s+.+\s+failed"
    r"|no\s+auxiliary\s+llm\s+provider\s+configured"
    r"|auto-lowered\s+compression\s+threshold"
    r"|preflight\s+compression"
    r"|rate\s+limited\.\s+waiting\s+\d"
    r"|retrying\s+in\s+\d"
    r"|max\s+retries\s+\(\d+\).*(?:trying\s+fallback|exhausted|invalid\s+responses)"
    r"|stream\s+(?:drop|drop\s+mid\s+tool-call).+retry\s+\d"
    r"|stale\s+connections\s+from\s+a\s+previous\s+provider\s+issue"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_GATEWAY_PROVIDER_ERROR_RE = re.compile(
    r"("  # infrastructure/provider error preambles, not ordinary assistant prose
    r"api\s+(?:call\s+)?failed"
    r"|provider\s+authentication\s+failed"
    r"|non-retryable\s+error"
    r"|rate\s+limited\s+after\s+\d+\s+retries"
    r"|error\s+code\s*:"
    r"|\bhttp\s*\d{3}\b"
    r"|incorrect\s+api\s+key"
    r"|invalid\s+api\s+key"
    r")",
    re.IGNORECASE,
)

_GATEWAY_PROVIDER_POLICY_RE = re.compile(
    r"("  # raw provider policy/safety bodies are noisy and may be sensitive
    r"cybersecurity\s+risk"
    r"|security\s+policy"
    r"|safety\s+policy"
    r"|policy\s+violation"
    r"|violat(?:e|es|ed|ion)"
    r"|blocked\s+(?:because|by|under)"
    r"|request\s+(?:was\s+)?(?:blocked|rejected)"
    r"|disallowed"
    r"|moderation"
    r")",
    re.IGNORECASE,
)

_GATEWAY_AUTH_ERROR_RE = re.compile(
    r"(provider\s+authentication\s+failed|incorrect\s+api\s+key|invalid\s+api\s+key|\b401\b)",
    re.IGNORECASE,
)

_GATEWAY_RATE_LIMIT_RE = re.compile(
    r"(rate\s+limit|rate-limited|\b429\b|quota|usage\s+limit)",
    re.IGNORECASE,
)

_GATEWAY_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._\-]{20,}\b"),
)


def _gateway_platform_value(platform: Any) -> str:
    """Return a normalized gateway platform value for enums or raw strings."""
    return str(getattr(platform, "value", platform) or "").strip().lower()


def _redact_gateway_user_facing_secrets(text: str) -> str:
    """Best-effort secret redaction before text can leave the gateway."""
    redacted = str(text or "")
    for pattern in _GATEWAY_SECRET_PATTERNS:
        redacted = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", redacted)
    return redacted


def _gateway_provider_error_reply(text: str) -> str:
    """Map raw provider/API errors to a short user-safe Telegram reply."""
    if _GATEWAY_AUTH_ERROR_RE.search(text):
        return (
            "⚠️ Provider authentication failed. Check the configured credentials; "
            "raw provider details are in the gateway logs."
        )
    if _GATEWAY_PROVIDER_POLICY_RE.search(text):
        return (
            "⚠️ The model provider rejected the request. I kept the raw provider "
            "error out of chat; check gateway logs for details or try rephrasing."
        )
    if _GATEWAY_RATE_LIMIT_RE.search(text):
        return "⏱️ The model provider is rate-limiting requests. Please wait a moment and try again."
    return (
        "⚠️ The model provider failed after retries. I kept raw provider details "
        "out of chat; check gateway logs for diagnostics."
    )


_GATEWAY_PROVIDER_ERROR_SHAPE_RE = re.compile(
    r"^\s*(\W*\s*)?("
    r"api\s+(?:call\s+)?failed"
    r"|provider\s+authentication\s+failed"
    r"|non-retryable\s+error"
    r"|rate\s+limited\s+after\s+\d+\s+retries"
    r"|error\s+code\s*:"
    r"|http\s*\d{3}\b"
    r"|incorrect\s+api\s+key"
    r"|invalid\s+api\s+key"
    r")",
    re.IGNORECASE,
)


def _looks_like_gateway_provider_error(text: str) -> bool:
    """True when text is infrastructure/provider failure, not normal content.

    Two heuristics combined so the rewrite only fires on actual provider
    error envelopes, not on assistant prose that happens to mention an
    HTTP status code:

    1. The text is short — real provider errors are 1–3 lines of envelope
       text; assistant answers are usually longer.
    2. AND the error marker appears at the start of the message (optionally
       behind a punctuation/symbol prefix), not buried mid-paragraph in an
       explanation like "HTTP 404 means 'not found' — ...".
    """
    if not text:
        return False
    body = str(text).strip()
    # Provider failure envelopes are short. Assistant answers that happen
    # to mention HTTP status codes ("HTTP 404 means...") tend to be longer.
    if len(body) > 400 or body.count("\n") > 4:
        return False
    return bool(_GATEWAY_PROVIDER_ERROR_SHAPE_RE.search(body))


def _sanitize_gateway_final_response(platform: Any, text: str) -> str:
    """Sanitize final gateway replies before sending them to high-noise chats.

    Telegram is Bob's mobile inbox, so it should receive concise, safe provider
    failure categories instead of raw HTTP bodies, request IDs, or policy text.
    Other platforms keep the existing behaviour for now.
    """
    if not text:
        return text
    if _gateway_platform_value(platform) != "telegram":
        return text

    redacted = _redact_gateway_user_facing_secrets(str(text))
    if _looks_like_gateway_provider_error(redacted):
        return _gateway_provider_error_reply(redacted)
    return redacted


def _prepare_gateway_status_message(platform: Any, event_type: str, message: str) -> Optional[str]:
    """Filter/sanitize agent status callbacks before platform delivery."""
    text = str(message or "").strip()
    if not text:
        return None
    if _gateway_platform_value(platform) != "telegram":
        return text

    text = _redact_gateway_user_facing_secrets(text)
    if _TELEGRAM_NOISY_STATUS_RE.search(text):
        return None
    if _looks_like_gateway_provider_error(text):
        return _gateway_provider_error_reply(text)
    return text




# Only auto-continue interrupted gateway turns while the interruption is fresh.
# Stale tool-tail/resume markers can otherwise revive an unrelated old task
# after a gateway restart when the user's next message starts new work.
#
# The freshness signal is the timestamp of the last transcript row, which
# ``hermes_state.get_messages`` carries on every persisted message.  This
# handles the two auto-continue cases uniformly:
#   * resume_pending (gateway restart/shutdown watchdog marked the session)
#   * tool-tail     (last persisted message is a tool result the agent
#                    never got to reply to)
# In both cases "when did we last do anything on this transcript" is the
# correct freshness question, so one signal replaces two divergent ones.
#
# Default window: 1 hour.  This comfortably covers ``agent.gateway_timeout``
# (30 min default) plus runtime slack — a legitimate long-running turn that
# gets interrupted near its timeout boundary and is resumed shortly after
# is still classified fresh.  Override via
# ``config.yaml`` ``agent.gateway_auto_continue_freshness``.
_AUTO_CONTINUE_FRESHNESS_SECS_DEFAULT = 60 * 60


def _coerce_gateway_timestamp(value: Any) -> Optional[float]:
    """Best-effort conversion of stored gateway timestamps to epoch seconds.

    Missing/unparseable timestamps return None so legacy transcripts keep the
    historical auto-continue behaviour instead of being silently dropped.
    Accepts: datetime, epoch seconds (int/float), epoch milliseconds (when
    the magnitude exceeds year-2286), ISO-8601 strings (with or without a
    trailing ``Z``), and numeric strings.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, bool):  # bool is a subclass of int — skip it
        return None
    if isinstance(value, (int, float)):
        # Some platform events use milliseconds; Hermes state rows use seconds.
        return float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            numeric = float(text)
            return numeric / 1000.0 if numeric > 10_000_000_000 else numeric
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _auto_continue_freshness_window() -> float:
    """Return the configured auto-continue freshness window in seconds.

    Reads ``HERMES_AUTO_CONTINUE_FRESHNESS`` (bridged from
    ``config.yaml`` ``agent.gateway_auto_continue_freshness`` at gateway
    startup, same pattern as ``HERMES_AGENT_TIMEOUT``).  Falls back to the
    module default when unset or malformed.  Non-positive values disable
    the freshness gate (restores the pre-fix "always fresh" behaviour for
    users who want to opt out).
    """
    raw = os.environ.get("HERMES_AUTO_CONTINUE_FRESHNESS")
    if raw is None or raw == "":
        return float(_AUTO_CONTINUE_FRESHNESS_SECS_DEFAULT)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(_AUTO_CONTINUE_FRESHNESS_SECS_DEFAULT)


def _float_env(name: str, default: float) -> float:
    """Read an env var as float, falling back to ``default`` on typos/empty.

    A misconfigured env var (e.g. ``HERMES_AGENT_TIMEOUT=abc``) must not
    crash the gateway or an agent turn.  Unset/empty also falls back.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _is_fresh_gateway_interruption(
    value: Any,
    *,
    now: Optional[float] = None,
    window_secs: Optional[float] = None,
) -> bool:
    """Return True when an interruption marker is fresh enough to auto-continue.

    Unknown timestamps are treated as fresh for backward compatibility with
    legacy transcripts (pre-dating timestamp persistence) and with in-memory
    test scaffolding that constructs history entries without timestamps.

    A non-positive ``window_secs`` disables the gate (always fresh), which
    restores the pre-fix behaviour for users who opt out via config.
    """
    window = (
        float(window_secs)
        if window_secs is not None
        else float(_AUTO_CONTINUE_FRESHNESS_SECS_DEFAULT)
    )
    if window <= 0:
        return True
    timestamp = _coerce_gateway_timestamp(value)
    if timestamp is None:
        return True
    current = time.time() if now is None else now
    return current - timestamp <= window


# Assistant-message fields that must survive transcript replay so multi-turn
# reasoning context, prefix-cache hits, and provider-specific echo
# requirements all behave the same on the gateway as they do in the CLI.
#
# ``reasoning`` and ``reasoning_details`` were the original three preserved
# by PR #2974 (schema v6).  ``reasoning_content``, ``codex_reasoning_items``,
# ``codex_message_items``, and ``finish_reason`` were added to the DB later
# but the gateway's replay whitelist was never expanded to match — so any
# pure-text assistant turn (no ``tool_calls``) silently dropped them on
# replay, regressing the CLI-vs-gateway behavioural parity.
#
# Why each field matters on replay:
#   * ``reasoning`` / ``reasoning_content``: provider-facing thinking text.
#     ``_copy_reasoning_content_for_api`` promotes ``reasoning`` →
#     ``reasoning_content`` at send time, but only when the strings happen to
#     match.  Carrying the original ``reasoning_content`` verbatim avoids
#     reconstruction loss for providers that return them as distinct fields
#     (DeepSeek/Kimi/Moonshot thinking modes).
#   * ``reasoning_details``: opaque structured array (signature,
#     encrypted_content) used by OpenRouter/Anthropic to maintain reasoning
#     continuity across turns.
#   * ``codex_reasoning_items``: encrypted reasoning blobs for the OpenAI
#     Codex Responses API.
#   * ``codex_message_items``: exact assistant message items with ``phase``.
#     OpenAI docs: "preserve and resend phase on all assistant messages —
#     dropping it can degrade performance."  Required for prefix cache hits.
#   * ``finish_reason``: informational; cheap to keep so transcripts replay
#     identically across CLI and gateway.
_ASSISTANT_REPLAY_FIELDS: tuple[str, ...] = (
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items",
    "finish_reason",
)


def _build_replay_entry(role: str, content: Any, msg: Dict[str, Any]) -> Dict[str, Any]:
    """Build a replay entry for a non-tool-calling message, preserving the
    assistant fields the agent's API builders rely on for multi-turn fidelity.

    Lifted out of the inline ``run_sync`` closure so the field whitelist can
    be unit-tested in isolation.  Mirrors the ``_ASSISTANT_REPLAY_FIELDS``
    contract above.

    Empty values: most fields are dropped when falsy (matching the original
    PR #2974 behaviour) since an empty list/string for those carries no
    information.  The exception is ``reasoning_content``: DeepSeek/Kimi
    thinking-mode replay treats an empty string as a meaningful sentinel
    that ``_copy_reasoning_content_for_api`` upgrades to a single space.
    Dropping it here would make the gateway send no ``reasoning_content`` at
    all on the next turn, which can cause HTTP 400 from strict thinking
    providers.
    """
    entry: Dict[str, Any] = {"role": role, "content": content}
    if role == "assistant":
        for _rkey in _ASSISTANT_REPLAY_FIELDS:
            if _rkey not in msg:
                continue
            _rval = msg.get(_rkey)
            if _rkey == "reasoning_content":
                # Preserve empty-string sentinel for thinking-mode replay.
                if _rval is None:
                    continue
            elif not _rval:
                continue
            entry[_rkey] = _rval
    return entry


def _last_transcript_timestamp(history: Optional[List[Dict[str, Any]]]) -> Any:
    """Return the ``timestamp`` of the last usable transcript row, if any.

    Skips metadata-only rows (``session_meta``, system injections) that are
    dropped before being handed to the agent.  Returns ``None`` when no
    usable row carries a timestamp — callers should treat that as "fresh"
    for backward compatibility.
    """
    if not history:
        return None
    for msg in reversed(history):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if not role or role in {"session_meta", "system"}:
            continue
        ts = msg.get("timestamp")
        if ts is not None:
            return ts
        # First non-meta row without a timestamp — legacy transcript row.
        # Returning None lets the caller fall through to the legacy-fresh path.
        return None
    return None


# ---------------------------------------------------------------------------
# SSL certificate auto-detection for NixOS and other non-standard systems.
# Must run BEFORE any HTTP library (discord, aiohttp, etc.) is imported.
# ---------------------------------------------------------------------------
def _ensure_ssl_certs() -> None:
    """Set SSL_CERT_FILE if the system doesn't expose CA certs to Python."""
    if "SSL_CERT_FILE" in os.environ:
        return  # user already configured it

    import ssl

    # 1. Python's compiled-in defaults
    paths = ssl.get_default_verify_paths()
    for candidate in (paths.cafile, paths.openssl_cafile):
        if candidate and os.path.exists(candidate):
            os.environ["SSL_CERT_FILE"] = candidate
            return

    # 2. certifi (ships its own Mozilla bundle)
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
        return
    except ImportError:
        pass

    # 3. Common distro / macOS locations
    for candidate in (
        "/etc/ssl/certs/ca-certificates.crt",               # Debian/Ubuntu/Gentoo
        "/etc/pki/tls/certs/ca-bundle.crt",                 # RHEL/CentOS 7
        "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem", # RHEL/CentOS 8+
        "/etc/ssl/ca-bundle.pem",                            # SUSE/OpenSUSE
        "/etc/ssl/cert.pem",                                 # Alpine / macOS
        "/etc/pki/tls/cert.pem",                             # Fedora
        "/usr/local/etc/openssl@1.1/cert.pem",               # macOS Homebrew Intel
        "/opt/homebrew/etc/openssl@1.1/cert.pem",            # macOS Homebrew ARM
    ):
        if os.path.exists(candidate):
            os.environ["SSL_CERT_FILE"] = candidate
            return





def _restart_notification_pending() -> bool:
    """Return True when a /restart completion marker is waiting to be delivered."""
    return (_hermes_home / ".restart_notify.json").exists()


# Mark this process as a gateway so cli.py's module-level load_cli_config()
# knows not to clobber TERMINAL_CWD if lazily imported.
os.environ["_HERMES_GATEWAY"] = "1"

_ensure_ssl_certs()

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Resolve Hermes home directory (respects HERMES_HOME override)
from hermes_constants import get_hermes_home
from utils import atomic_json_write, atomic_yaml_write, base_url_host_matches, is_truthy_value
_hermes_home = get_hermes_home()

# Load environment variables from ~/.hermes/.env first.
# User-managed env files should override stale shell exports on restart.
from dotenv import load_dotenv  # backward-compat for tests that monkeypatch this symbol
from hermes_cli.env_loader import load_hermes_dotenv
_env_path = _hermes_home / '.env'
load_hermes_dotenv(hermes_home=_hermes_home, project_env=Path(__file__).resolve().parents[1] / '.env')


def _reload_runtime_env_preserving_config_authority() -> None:
    """Reload .env for fresh credentials without letting stale .env override config.

    Gateway processes are long-lived, so per-turn code reloads ~/.hermes/.env to
    pick up rotated API keys. config.yaml remains authoritative for agent budget
    settings such as agent.max_turns; otherwise a stale HERMES_MAX_ITERATIONS in
    .env can replace the startup bridge on later turns.
    """
    load_hermes_dotenv(
        hermes_home=_hermes_home,
        project_env=Path(__file__).resolve().parents[1] / '.env',
    )

    config_path = _hermes_home / 'config.yaml'
    if not config_path.exists():
        return
    try:
        import yaml as _yaml
        with open(config_path, encoding="utf-8") as f:
            cfg = _yaml.safe_load(f) or {}
        from hermes_cli.config import _expand_env_vars
        cfg = _expand_env_vars(cfg)
    except Exception:
        return

    agent_cfg = cfg.get("agent", {})
    if isinstance(agent_cfg, dict) and "max_turns" in agent_cfg:
        os.environ["HERMES_MAX_ITERATIONS"] = str(agent_cfg["max_turns"])


_DOCKER_VOLUME_SPEC_RE = re.compile(r"^(?P<host>.+):(?P<container>/[^:]+?)(?::(?P<options>[^:]+))?$")
_DOCKER_MEDIA_OUTPUT_CONTAINER_PATHS = {"/output", "/outputs"}

# Bridge config.yaml values into the environment so os.getenv() picks them up.
# config.yaml is authoritative for terminal settings — overrides .env.
_config_path = _hermes_home / 'config.yaml'
if _config_path.exists():
    try:
        import yaml as _yaml
        with open(_config_path, encoding="utf-8") as _f:
            _cfg = _yaml.safe_load(_f) or {}
        # Expand ${ENV_VAR} references before bridging to env vars.
        from hermes_cli.config import _expand_env_vars
        _cfg = _expand_env_vars(_cfg)
        # Top-level simple values (fallback only — don't override .env)
        for _key, _val in _cfg.items():
            if isinstance(_val, (str, int, float, bool)) and _key not in os.environ:
                os.environ[_key] = str(_val)
        # Terminal config is nested — bridge to TERMINAL_* env vars.
        # config.yaml overrides .env for these since it's the documented config path.
        _terminal_cfg = _cfg.get("terminal", {})
        if _terminal_cfg and isinstance(_terminal_cfg, dict):
            _terminal_env_map = {
                "backend": "TERMINAL_ENV",
                "cwd": "TERMINAL_CWD",
                "timeout": "TERMINAL_TIMEOUT",
                "lifetime_seconds": "TERMINAL_LIFETIME_SECONDS",
                "docker_image": "TERMINAL_DOCKER_IMAGE",
                "docker_forward_env": "TERMINAL_DOCKER_FORWARD_ENV",
                "singularity_image": "TERMINAL_SINGULARITY_IMAGE",
                "modal_image": "TERMINAL_MODAL_IMAGE",
                "daytona_image": "TERMINAL_DAYTONA_IMAGE",
                "vercel_runtime": "TERMINAL_VERCEL_RUNTIME",
                "ssh_host": "TERMINAL_SSH_HOST",
                "ssh_user": "TERMINAL_SSH_USER",
                "ssh_port": "TERMINAL_SSH_PORT",
                "ssh_key": "TERMINAL_SSH_KEY",
                "container_cpu": "TERMINAL_CONTAINER_CPU",
                "container_memory": "TERMINAL_CONTAINER_MEMORY",
                "container_disk": "TERMINAL_CONTAINER_DISK",
                "container_persistent": "TERMINAL_CONTAINER_PERSISTENT",
                "docker_volumes": "TERMINAL_DOCKER_VOLUMES",
                "docker_env": "TERMINAL_DOCKER_ENV",
                "docker_mount_cwd_to_workspace": "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE",
                "docker_run_as_host_user": "TERMINAL_DOCKER_RUN_AS_HOST_USER",
                "sandbox_dir": "TERMINAL_SANDBOX_DIR",
                "persistent_shell": "TERMINAL_PERSISTENT_SHELL",
            }
            for _cfg_key, _env_var in _terminal_env_map.items():
                if _cfg_key in _terminal_cfg:
                    _val = _terminal_cfg[_cfg_key]
                    # Skip cwd placeholder values (".", "auto", "cwd") — the
                    # gateway resolves these to Path.home() later (line ~255).
                    # Writing the raw placeholder here would just be noise.
                    # Only bridge explicit absolute paths from config.yaml.
                    if _cfg_key == "cwd" and str(_val) in {".", "auto", "cwd"}:
                        continue
                    # Expand shell tilde in cwd so subprocess.Popen never
                    # receives a literal "~/" which the kernel rejects.
                    if _cfg_key == "cwd" and isinstance(_val, str):
                        _val = os.path.expanduser(_val)
                    if isinstance(_val, (list, dict)):
                        os.environ[_env_var] = json.dumps(_val)
                    else:
                        os.environ[_env_var] = str(_val)
        # Compression config is read directly from config.yaml by run_agent.py
        # and auxiliary_client.py — no env var bridging needed.
        # Auxiliary model/direct-endpoint overrides (vision, web_extract).
        # Each task has provider/model/base_url/api_key; bridge non-default values to env vars.
        _auxiliary_cfg = _cfg.get("auxiliary", {})
        if _auxiliary_cfg and isinstance(_auxiliary_cfg, dict):
            _aux_task_env = {
                "vision": {
                    "provider": "AUXILIARY_VISION_PROVIDER",
                    "model": "AUXILIARY_VISION_MODEL",
                    "base_url": "AUXILIARY_VISION_BASE_URL",
                    "api_key": "AUXILIARY_VISION_API_KEY",
                },
                "web_extract": {
                    "provider": "AUXILIARY_WEB_EXTRACT_PROVIDER",
                    "model": "AUXILIARY_WEB_EXTRACT_MODEL",
                    "base_url": "AUXILIARY_WEB_EXTRACT_BASE_URL",
                    "api_key": "AUXILIARY_WEB_EXTRACT_API_KEY",
                },
                "approval": {
                    "provider": "AUXILIARY_APPROVAL_PROVIDER",
                    "model": "AUXILIARY_APPROVAL_MODEL",
                    "base_url": "AUXILIARY_APPROVAL_BASE_URL",
                    "api_key": "AUXILIARY_APPROVAL_API_KEY",
                },
            }
            for _task_key, _env_map in _aux_task_env.items():
                _task_cfg = _auxiliary_cfg.get(_task_key, {})
                if not isinstance(_task_cfg, dict):
                    continue
                _prov = str(_task_cfg.get("provider", "")).strip()
                _model = str(_task_cfg.get("model", "")).strip()
                _base_url = str(_task_cfg.get("base_url", "")).strip()
                _api_key = str(_task_cfg.get("api_key", "")).strip()
                if _prov and _prov != "auto":
                    os.environ[_env_map["provider"]] = _prov
                if _model:
                    os.environ[_env_map["model"]] = _model
                if _base_url:
                    os.environ[_env_map["base_url"]] = _base_url
                if _api_key:
                    os.environ[_env_map["api_key"]] = _api_key
        # config.yaml is the documented, authoritative source for these
        # settings — it unconditionally wins over .env values. Previously
        # the guards below read `if X not in os.environ` and let stale
        # .env entries (e.g. HERMES_MAX_ITERATIONS=60 written by an old
        # `hermes setup` run) silently shadow the user's current config.
        # See PR #18413 / the 60-vs-500 max_turns incident.
        _agent_cfg = _cfg.get("agent", {})
        if _agent_cfg and isinstance(_agent_cfg, dict):
            if "max_turns" in _agent_cfg:
                os.environ["HERMES_MAX_ITERATIONS"] = str(_agent_cfg["max_turns"])
            if "gateway_timeout" in _agent_cfg:
                os.environ["HERMES_AGENT_TIMEOUT"] = str(_agent_cfg["gateway_timeout"])
            if "gateway_timeout_warning" in _agent_cfg:
                os.environ["HERMES_AGENT_TIMEOUT_WARNING"] = str(_agent_cfg["gateway_timeout_warning"])
            if "gateway_notify_interval" in _agent_cfg:
                os.environ["HERMES_AGENT_NOTIFY_INTERVAL"] = str(_agent_cfg["gateway_notify_interval"])
            if "restart_drain_timeout" in _agent_cfg:
                os.environ["HERMES_RESTART_DRAIN_TIMEOUT"] = str(_agent_cfg["restart_drain_timeout"])
            if "gateway_auto_continue_freshness" in _agent_cfg:
                os.environ["HERMES_AUTO_CONTINUE_FRESHNESS"] = str(
                    _agent_cfg["gateway_auto_continue_freshness"]
                )
        _display_cfg = _cfg.get("display", {})
        if _display_cfg and isinstance(_display_cfg, dict):
            if "busy_input_mode" in _display_cfg:
                os.environ["HERMES_GATEWAY_BUSY_INPUT_MODE"] = str(_display_cfg["busy_input_mode"])
            if "busy_ack_enabled" in _display_cfg:
                os.environ["HERMES_GATEWAY_BUSY_ACK_ENABLED"] = str(_display_cfg["busy_ack_enabled"])
        # Timezone: bridge config.yaml → HERMES_TIMEZONE env var.
        _tz_cfg = _cfg.get("timezone", "")
        if _tz_cfg and isinstance(_tz_cfg, str):
            os.environ["HERMES_TIMEZONE"] = _tz_cfg.strip()
        # Security settings
        _security_cfg = _cfg.get("security", {})
        if isinstance(_security_cfg, dict):
            _redact = _security_cfg.get("redact_secrets")
            if _redact is not None:
                os.environ["HERMES_REDACT_SECRETS"] = str(_redact).lower()
    except Exception as _bridge_err:
        # Previously this was silent (`except Exception: pass`), which
        # hid partial bridge failures and let .env defaults shadow
        # config.yaml values — users observed max_turns=500 in config
        # but a 60-iteration cap in practice. Surface the failure to
        # stderr so operators see it even though `logger` is not yet
        # initialized at module-import time (logger is defined further
        # down this module).
        logger.info(
            f"  Warning: config.yaml → env bridge failed: "
            f"{type(_bridge_err).__name__}: {_bridge_err}",
            file=sys.stderr,
        )
        logger.info(
            "  Gateway will fall back to .env values, which may not match "
            "your current config.yaml. Run `hermes doctor` to investigate.",
            file=sys.stderr,
        )

# Apply IPv4 preference if configured (before any HTTP clients are created).
try:
    from hermes_constants import apply_ipv4_preference
    _network_cfg = (_cfg if '_cfg' in dir() else {}).get("network", {})
    if isinstance(_network_cfg, dict) and _network_cfg.get("force_ipv4"):
        apply_ipv4_preference(force=True)
except Exception as _bootstrap_exc:
    logger.warning(f"  Warning: IPv4 preference application failed: {_bootstrap_exc}")

# Validate config structure early — log warnings so gateway operators see problems
try:
    from hermes_cli.config import print_config_warnings
    print_config_warnings()
except Exception as _bootstrap_exc:
    logger.warning(f"  Warning: config validation failed: {_bootstrap_exc}")

# Warn if user has deprecated MESSAGING_CWD / TERMINAL_CWD in .env
try:
    from hermes_cli.config import warn_deprecated_cwd_env_vars
    warn_deprecated_cwd_env_vars()
except Exception as _bootstrap_exc:
    logger.warning(f"  Warning: deprecation check failed: {_bootstrap_exc}")

# Gateway runs in quiet mode - suppress debug output and use cwd directly (no temp dirs)
os.environ["HERMES_QUIET"] = "1"

# Enable interactive exec approval for dangerous commands on messaging platforms
os.environ["HERMES_EXEC_ASK"] = "1"

# Set terminal working directory for messaging platforms.
# config.yaml terminal.cwd is the canonical source (bridged to TERMINAL_CWD
# by the config bridge above).  When it's unset or a placeholder, default
# to home directory.  MESSAGING_CWD is accepted as a backward-compat
# fallback (deprecated — the warning above tells users to migrate).
_configured_cwd = os.environ.get("TERMINAL_CWD", "")
if not _configured_cwd or _configured_cwd in {".", "auto", "cwd"}:
    _fallback = os.getenv("MESSAGING_CWD") or str(Path.home())
    os.environ["TERMINAL_CWD"] = _fallback

from gateway.config import (
    Platform,
    _BUILTIN_PLATFORM_VALUES,
    GatewayConfig,
    HomeChannel,
    PlatformConfig,
    load_gateway_config,
)
from gateway.gateway_utils import (
    _home_target_env_var,
    _home_thread_env_var,
    _load_gateway_config,
    _platform_config_key,
    _resolve_gateway_model,
    _resolve_hermes_bin,
    _telegramize_command_mentions,
)
from gateway.session import (
    SessionStore,
    SessionSource,
    SessionContext,
    build_session_context,
    build_session_context_prompt,
    build_session_key,
    is_shared_multi_user_session,
)
from gateway.delivery import DeliveryRouter
from gateway.platforms.base import (
    BasePlatformAdapter,
    EphemeralReply,
    MessageEvent,
    MessageType,
    _reply_anchor_for_event,
    merge_pending_message_event,
)
from gateway.restart import (
    DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT,
    GATEWAY_SERVICE_RESTART_EXIT_CODE,
    parse_restart_drain_timeout,
)


from gateway.whatsapp_identity import (
    canonical_whatsapp_identifier as _canonical_whatsapp_identifier,  # noqa: F401
    expand_whatsapp_aliases as _expand_whatsapp_auth_aliases,
    normalize_whatsapp_identifier as _normalize_whatsapp_identifier,
)


logger = logging.getLogger(__name__)


# Sentinel placed into _running_agents immediately when a session starts
# processing, *before* any await.  Prevents a second message for the same
# session from bypassing the "already running" guard during the async gap
# between the guard check and actual agent creation.
_AGENT_PENDING_SENTINEL = object()


def _resolve_runtime_agent_kwargs() -> dict:
    """Resolve provider credentials for gateway-created AIAgent instances.

    If the primary provider fails with an authentication error, attempt to
    resolve credentials using the fallback provider chain from config.yaml
    before giving up.
    """
    from hermes_cli.runtime_provider import (
        resolve_runtime_provider,
        format_runtime_provider_error,
    )
    from hermes_cli.auth import AuthError

    try:
        runtime = resolve_runtime_provider(
            requested=os.getenv("HERMES_INFERENCE_PROVIDER"),
        )
    except AuthError as auth_exc:
        # Primary provider auth failed (expired token, revoked key, etc.).
        # Try the fallback provider chain before raising.
        logger.warning("Primary provider auth failed: %s — trying fallback", auth_exc)
        fb_config = _try_resolve_fallback_provider()
        if fb_config is not None:
            return fb_config
        raise RuntimeError(format_runtime_provider_error(auth_exc)) from auth_exc
    except Exception as exc:
        raise RuntimeError(format_runtime_provider_error(exc)) from exc

    return {
        "api_key": runtime.get("api_key"),
        "base_url": runtime.get("base_url"),
        "provider": runtime.get("provider"),
        "api_mode": runtime.get("api_mode"),
        "command": runtime.get("command"),
        "args": list(runtime.get("args") or []),
        "credential_pool": runtime.get("credential_pool"),
    }


def _try_resolve_fallback_provider() -> dict | None:
    """Attempt to resolve credentials from the fallback_model/fallback_providers config."""
    from hermes_cli.runtime_provider import resolve_runtime_provider
    try:
        import yaml as _y
        cfg_path = _hermes_home / "config.yaml"
        if not cfg_path.exists():
            return None
        with open(cfg_path, encoding="utf-8") as _f:
            cfg = _y.safe_load(_f) or {}
        fb = cfg.get("fallback_providers") or cfg.get("fallback_model")
        if not fb:
            return None
        # Normalize to list
        fb_list = fb if isinstance(fb, list) else [fb]
        for entry in fb_list:
            if not isinstance(entry, dict):
                continue
            try:
                runtime = resolve_runtime_provider(
                    requested=entry.get("provider"),
                    explicit_base_url=entry.get("base_url"),
                    explicit_api_key=entry.get("api_key"),
                )
                logger.info(
                    "Fallback provider resolved: %s model=%s",
                    runtime.get("provider"),
                    entry.get("model"),
                )
                return {
                    "api_key": runtime.get("api_key"),
                    "base_url": runtime.get("base_url"),
                    "provider": runtime.get("provider"),
                    "api_mode": runtime.get("api_mode"),
                    "command": runtime.get("command"),
                    "args": list(runtime.get("args") or []),
                    "credential_pool": runtime.get("credential_pool"),
                    "model": entry.get("model"),
                }
            except Exception as fb_exc:
                logger.debug("Fallback entry %s failed: %s", entry.get("provider"), fb_exc)
                continue
    except Exception:
        pass
    return None


def _build_media_placeholder(event) -> str:
    """Build a text placeholder for media-only events so they aren't dropped.

    When a photo/document is queued during active processing and later
    dequeued, only .text is extracted.  If the event has no caption,
    the media would be silently lost.  This builds a placeholder that
    the vision enrichment pipeline will replace with a real description.
    """
    parts = []
    media_urls = getattr(event, "media_urls", None) or []
    media_types = getattr(event, "media_types", None) or []
    for i, url in enumerate(media_urls):
        mtype = media_types[i] if i < len(media_types) else ""
        if mtype.startswith("image/") or getattr(event, "message_type", None) == MessageType.PHOTO:
            parts.append(f"[User sent an image: {url}]")
        elif mtype.startswith("audio/"):
            parts.append(f"[User sent audio: {url}]")
        else:
            parts.append(f"[User sent a file: {url}]")
    return "\n".join(parts)


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


async def _probe_audio_duration(path: str) -> Optional[str]:
    """Best-effort duration probe. Returns formatted MM:SS / HH:MM:SS, or None on failure."""
    ext = os.path.splitext(path)[1].lower()

    if ext == ".wav":
        try:
            def _wav_duration() -> float:
                import wave
                with wave.open(path, "rb") as wf:
                    frames = wf.getnframes()
                    rate = wf.getframerate() or 1
                    return frames / float(rate)
            secs = await asyncio.to_thread(_wav_duration)
            return _format_duration(secs)
        except Exception:
            pass

    if ext in (".ogg", ".opus", ".oga"):
        try:
            def _ogg_duration() -> float:
                from mutagen.oggopus import OggOpus
                return float(OggOpus(path).info.length)
            secs = await asyncio.to_thread(_ogg_duration)
            return _format_duration(secs)
        except Exception:
            pass

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            return _format_duration(float(stdout.decode().strip()))
    except Exception:
        pass

    return None


def _dequeue_pending_event(adapter, session_key: str) -> MessageEvent | None:
    """Consume and return the full pending event for a session.

    Queued follow-ups must preserve their media metadata so they can re-enter
    the normal image/STT/document preprocessing path instead of being reduced
    to a placeholder string.
    """
    return adapter.get_pending_message(session_key)


_INTERRUPT_REASON_STOP = "Stop requested"
_INTERRUPT_REASON_RESET = "Session reset requested"
_INTERRUPT_REASON_TIMEOUT = "Execution timed out (inactivity)"
_INTERRUPT_REASON_SSE_DISCONNECT = "SSE client disconnected"
_INTERRUPT_REASON_GATEWAY_SHUTDOWN = "Gateway shutting down"
_INTERRUPT_REASON_GATEWAY_RESTART = "Gateway restarting"

_CONTROL_INTERRUPT_MESSAGES = frozenset(
    {
        _INTERRUPT_REASON_STOP.lower(),
        _INTERRUPT_REASON_RESET.lower(),
        _INTERRUPT_REASON_TIMEOUT.lower(),
        _INTERRUPT_REASON_SSE_DISCONNECT.lower(),
        _INTERRUPT_REASON_GATEWAY_SHUTDOWN.lower(),
        _INTERRUPT_REASON_GATEWAY_RESTART.lower(),
    }
)


def _is_control_interrupt_message(message: Optional[str]) -> bool:
    """Return True when an interrupt message is internal control flow."""
    if not message:
        return False
    normalized = " ".join(str(message).strip().split()).lower()
    return normalized in _CONTROL_INTERRUPT_MESSAGES


def _skill_slug_from_frontmatter(skill_md: Path) -> tuple[str | None, str | None]:
    """Derive the /command slug and declared frontmatter name from a SKILL.md.

    Matches the exact normalization used by
    :func:`agent.skill_commands.scan_skill_commands` so the slug here is the
    same string a user types after the leading ``/`` (e.g. a skill with
    frontmatter ``name: Stable Diffusion Image Generation`` resolves to
    ``stable-diffusion-image-generation`` — NOT the parent directory name,
    which is commonly shorter/different, e.g. ``stable-diffusion``).

    Using the directory name silently broke :func:`_check_unavailable_skill`
    for every skill whose directory name drifted from its frontmatter name
    (19 such skills on a standard install as of 2026-05), causing a generic
    "unknown command" response where a "disabled — enable with …" or
    "not installed — install with …" hint was expected.

    Returns ``(slug, declared_name)`` or ``(None, None)`` when the file
    can't be read or lacks a ``name:`` in its frontmatter.
    """
    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None, None
    if not content.startswith("---"):
        return None, None
    end = content.find("\n---", 3)
    if end < 0:
        return None, None
    declared_name: str | None = None
    for line in content[3:end].splitlines():
        line = line.strip()
        if line.startswith("name:"):
            raw = line.split(":", 1)[1].strip()
            # Strip YAML quote wrappers if present
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
                raw = raw[1:-1]
            declared_name = raw.strip()
            break
    if not declared_name:
        return None, None
    slug = declared_name.lower().replace(" ", "-").replace("_", "-")
    # Mirror _SKILL_INVALID_CHARS and _SKILL_MULTI_HYPHEN from skill_commands
    import re as _re
    slug = _re.sub(r"[^a-z0-9-]", "", slug)
    slug = _re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        return None, declared_name
    return slug, declared_name


def _check_unavailable_skill(command_name: str) -> str | None:
    """Check if a command matches a known-but-inactive skill.

    Returns a helpful message if the skill exists but is disabled or only
    available as an optional install. Returns None if no match found.

    The slug for each on-disk skill is derived from its frontmatter ``name:``
    (via :func:`_skill_slug_from_frontmatter`), NOT from its containing
    directory name — because the two can differ (e.g. directory
    ``stable-diffusion`` + frontmatter ``Stable Diffusion Image Generation``
    yields slug ``stable-diffusion-image-generation``). Matching on
    directory name would miss that slug entirely and fall through to the
    generic "unknown command" path.
    """
    # Normalize: command uses hyphens, skill names may use hyphens or underscores
    normalized = command_name.lower().replace("_", "-")
    try:
        from tools.skills_tool import _get_disabled_skill_names
        from agent.skill_utils import get_all_skills_dirs
        disabled = _get_disabled_skill_names()

        # Check disabled skills across all dirs (local + external)
        for skills_dir in get_all_skills_dirs():
            if not skills_dir.exists():
                continue
            for skill_md in skills_dir.rglob("SKILL.md"):
                if any(part in {'.git', '.github', '.hub', '.archive'} for part in skill_md.parts):
                    continue
                slug, declared_name = _skill_slug_from_frontmatter(skill_md)
                if not slug or not declared_name:
                    continue
                # disabled is keyed by the declared frontmatter name (what
                # skills.disabled / skills.platform_disabled store).
                if slug == normalized and declared_name in disabled:
                    return (
                        f"The **{command_name}** skill is installed but disabled.\n"
                        f"Enable it with: `hermes skills config`"
                    )

        # Check optional skills (shipped with repo but not installed)
        from hermes_constants import get_optional_skills_dir
        repo_root = Path(__file__).resolve().parent.parent
        optional_dir = get_optional_skills_dir(repo_root / "optional-skills")
        if optional_dir.exists():
            for skill_md in optional_dir.rglob("SKILL.md"):
                slug, _declared = _skill_slug_from_frontmatter(skill_md)
                if not slug:
                    continue
                if slug == normalized:
                    # Build install path: official/<category>/<name>
                    rel = skill_md.parent.relative_to(optional_dir)
                    parts = list(rel.parts)
                    install_path = f"official/{'/'.join(parts)}"
                    return (
                        f"The **{command_name}** skill is available but not installed.\n"
                        f"Install it with: `hermes skills install {install_path}`"
                    )
    except Exception:
        pass
    return None




def _teams_pipeline_plugin_enabled() -> bool:
    """Return True when the standalone Teams pipeline plugin is enabled."""
    config = _load_gateway_config()
    enabled = cfg_get(config, "plugins", "enabled", default=[])
    if not isinstance(enabled, list):
        return False
    return "teams_pipeline" in enabled or "teams-pipeline" in enabled








def _parse_session_key(session_key: str) -> "dict | None":
    """Parse a session key into its component parts.

    Session keys follow the format
    ``agent:main:{platform}:{chat_type}:{chat_id}[:{extra}...]``.
    Returns a dict with ``platform``, ``chat_type``, ``chat_id``, and
    optionally ``thread_id`` keys, or None if the key doesn't match.

    The 6th element is only returned as ``thread_id`` for chat types where
    it is unambiguous (``dm`` and ``thread``).  For group/channel sessions
    the suffix may be a user_id (per-user isolation) rather than a
    thread_id, so we leave ``thread_id`` out to avoid mis-routing.
    """
    parts = session_key.split(":")
    if len(parts) >= 5 and parts[0] == "agent" and parts[1] == "main":
        result = {
            "platform": parts[2],
            "chat_type": parts[3],
            "chat_id": parts[4],
        }
        if len(parts) > 5 and parts[3] in {"dm", "thread"}:
            result["thread_id"] = parts[5]
        return result
    return None


def _format_gateway_process_notification(evt: dict) -> "str | None":
    """Format a watch pattern event from completion_queue into a [IMPORTANT:] message."""
    evt_type = evt.get("type", "completion")
    _sid = evt.get("session_id", "unknown")
    _cmd = evt.get("command", "unknown")

    if evt_type == "watch_disabled":
        return f"[IMPORTANT: {evt.get('message', '')}]"

    if evt_type == "watch_match":
        _pat = evt.get("pattern", "?")
        _out = evt.get("output", "")
        _sup = evt.get("suppressed", 0)
        text = (
            f"[IMPORTANT: Background process {_sid} matched "
            f"watch pattern \"{_pat}\".\n"
            f"Command: {_cmd}\n"
            f"Matched output:\n{_out}"
        )
        if _sup:
            text += f"\n({_sup} earlier matches were suppressed by rate limit)"
        text += "]"
        return text

    return None


# Module-level weak reference to the active GatewayRunner instance.
# Used by tools (e.g. send_message) that need to route through a live
# adapter for plugin platforms.  Set in GatewayRunner.__init__().
import weakref as _weakref
_gateway_runner_ref: _weakref.ref = lambda: None


def _normalize_empty_agent_response(
    agent_result: dict,
    response: str,
    *,
    history_len: int = 0,
) -> str:
    """Normalize empty/None agent responses into user-facing messages.

    Consolidates the existing ``failed`` handler and adds a catch-all for
    the case where the agent did work (api_calls > 0) but returned no text.
    Fix for #18765.
    """
    if response:
        return response

    if agent_result.get("failed"):
        error_detail = agent_result.get("error", "unknown error")
        error_str = str(error_detail).lower()
        is_context_failure = any(
            p in error_str
            for p in ("context", "token", "too large", "too long", "exceed", "payload")
        ) or ("400" in error_str and history_len > 50)
        if is_context_failure:
            return (
                "⚠️ Session too large for the model's context window.\n"
                "Use /compact to compress the conversation, or "
                "/reset to start fresh."
            )
        return (
            f"The request failed: {str(error_detail)[:300]}\n"
            "Try again or use /reset to start a fresh session."
        )

    api_calls = int(agent_result.get("api_calls", 0) or 0)
    if api_calls > 0 and not agent_result.get("interrupted"):
        if agent_result.get("partial"):
            err = agent_result.get("error", "processing incomplete")
            return f"⚠️ Processing stopped: {str(err)[:200]}. Try again."
        return (
            "⚠️ Processing completed but no response was generated. "
            "This may be a transient error — try sending your message again."
        )

    return response


def _should_clear_resume_pending_after_turn(agent_result: dict) -> bool:
    """Return True only when a gateway turn really completed successfully.

    Restart recovery uses ``resume_pending`` as a durable marker for sessions
    interrupted during gateway drain.  A soft interrupt can still bubble out as
    a syntactically normal agent result with an empty final response; clearing
    the marker in that case loses the recovery signal and startup auto-resume
    has nothing to schedule.
    """
    if not isinstance(agent_result, dict):
        return False
    if agent_result.get("interrupted"):
        return False
    if agent_result.get("failed") or agent_result.get("partial") or agent_result.get("error"):
        return False
    if agent_result.get("completed") is False:
        return False
    return True


def _preserve_queued_followup_history_offset(
    current_result: dict,
    followup_result: dict,
) -> dict:
    """Carry the outer history offset through queued follow-up drains.

    ``_process_message_background()`` persists transcript rows only once, after the
    entire in-band queued-follow-up chain returns.  Each recursive ``_run_agent()``
    call advances ``history_offset`` to the history it received, so without
    correction the outermost persistence step sees only the *last* queued turn as
    "new" and silently drops earlier turns from the same drain chain.

    Preserve the earliest (outermost) history offset so the final transcript slice
    still includes every queued turn that ran during the chain.
    """
    if not isinstance(followup_result, dict):
        return followup_result
    if not isinstance(current_result, dict):
        return followup_result

    current_offset = current_result.get("history_offset")
    followup_offset = followup_result.get("history_offset")
    if not isinstance(current_offset, int):
        return followup_result
    if isinstance(followup_offset, int) and followup_offset <= current_offset:
        return followup_result

    merged = dict(followup_result)
    merged["history_offset"] = current_offset
    return merged


from gateway.telegram_topics_mixin import TelegramTopicsMixin
from gateway.voice_mixin import VoiceMixin
from gateway.goal_mixin import GoalMixin
from gateway.kanban_mixin import KanbanMixin
from gateway.slash_commands_mixin import SlashCommandsMixin
from gateway.session_mixin import SessionMixin
from gateway.auth_mixin import AuthMixin
from gateway.config_loader_mixin import ConfigLoaderMixin


from gateway.watcher_mixin import WatcherMixin
from gateway.message_handler_mixin import MessageHandlerMixin
from gateway.agent_runner_mixin import AgentRunnerMixin

class GatewayRunner(MessageHandlerMixin, AgentRunnerMixin, TelegramTopicsMixin, VoiceMixin, GoalMixin, KanbanMixin, SlashCommandsMixin, SessionMixin, AuthMixin, ConfigLoaderMixin, WatcherMixin):
    """
    Main gateway controller.

    Manages the lifecycle of all platform adapters and routes
    messages to/from the agent.
    """

    # Class-level defaults so partial construction in tests doesn't
    # blow up on attribute access.
    _running_agents_ts: Dict[str, float] = {}
    _busy_input_mode: str = "interrupt"
    _restart_drain_timeout: float = DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
    _exit_code: Optional[int] = None
    _draining: bool = False
    _restart_requested: bool = False
    _restart_task_started: bool = False
    _restart_detached: bool = False
    _restart_via_service: bool = False
    _stop_task: Optional[asyncio.Task] = None
    _session_model_overrides: Dict[str, Dict[str, str]] = {}
    _session_reasoning_overrides: Dict[str, Dict[str, Any]] = {}

    def __init__(self, config: Optional[GatewayConfig] = None):
        global _gateway_runner_ref
        self.config = config or load_gateway_config()
        self.adapters: Dict[Platform, BasePlatformAdapter] = {}
        self._warn_if_docker_media_delivery_is_risky()
        _gateway_runner_ref = _weakref.ref(self)

        # Load ephemeral config from config.yaml / env vars.
        # Both are injected at API-call time only and never persisted.
        self._prefill_messages = self._load_prefill_messages()
        self._ephemeral_system_prompt = self._load_ephemeral_system_prompt()
        self._reasoning_config = self._load_reasoning_config()
        self._service_tier = self._load_service_tier()
        self._show_reasoning = self._load_show_reasoning()
        self._busy_input_mode = self._load_busy_input_mode()
        self._restart_drain_timeout = self._load_restart_drain_timeout()
        self._provider_routing = self._load_provider_routing()
        self._fallback_model = self._load_fallback_model()

        # Wire process registry into session store for reset protection
        from tools.process_registry import process_registry
        self.session_store = SessionStore(
            self.config.sessions_dir, self.config,
            has_active_processes_fn=lambda key: process_registry.has_active_for_session(key),
        )
        self.delivery_router = DeliveryRouter(self.config)
        self._running = False
        self._gateway_loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown_event = asyncio.Event()
        self._exit_cleanly = False
        self._exit_with_failure = False
        self._exit_reason: Optional[str] = None
        self._exit_code: Optional[int] = None
        self._draining = False
        self._restart_requested = False
        self._restart_task_started = False
        self._restart_detached = False
        self._restart_via_service = False
        self._stop_task: Optional[asyncio.Task] = None
        
        # Track running agents per session for interrupt support
        # Key: session_key, Value: AIAgent instance
        self._running_agents: Dict[str, Any] = {}
        self._running_agents_ts: Dict[str, float] = {}  # start timestamp per session
        self._pending_messages: Dict[str, str] = {}  # Queued messages during interrupt
        # Overflow buffer for explicit /queue commands.  The adapter-level
        # _pending_messages dict is a single slot per session (designed for
        # "next-turn" follow-ups where repeated sends collapse into one
        # event).  /queue has different semantics: each invocation must
        # produce its own full agent turn, in FIFO order, with no merging.
        # When the slot is occupied, additional /queue items land here and
        # are promoted one-at-a-time after each run's drain.  Cleared on
        # /new and /reset.  /model and other mid-session operations
        # preserve the queue.
        self._queued_events: Dict[str, List[MessageEvent]] = {}
        self._pending_native_image_paths_by_session: Dict[str, List[str]] = {}
        self._busy_ack_ts: Dict[str, float] = {}  # last busy-ack timestamp per session (debounce)
        self._session_run_generation: Dict[str, int] = {}
        # LRU cache of live SessionSources keyed by session_key. Used by
        # fallback routing paths (shutdown notifications, synthetic
        # background-process events) when the persisted origin is missing
        # and _parse_session_key can't recover thread_id. Capped so it
        # cannot grow unbounded over a long-running gateway lifetime.
        self._session_sources: "OrderedDict[str, SessionSource]" = OrderedDict()
        self._session_sources_max = 512

        # Cache AIAgent instances per session to preserve prompt caching.
        # Without this, a new AIAgent is created per message, rebuilding the
        # system prompt (including memory) every turn — breaking prefix cache
        # and costing ~10x more on providers with prompt caching (Anthropic).
        # Key: session_key, Value: (AIAgent, config_signature_str)
        #
        # OrderedDict so _enforce_agent_cache_cap() can pop the least-recently-
        # used entry (move_to_end() on cache hits, popitem(last=False) for
        # eviction).  Hard cap via _AGENT_CACHE_MAX_SIZE, idle TTL enforced
        # from _session_expiry_watcher().
        import threading as _threading
        self._agent_cache: "OrderedDict[str, tuple]" = OrderedDict()
        self._agent_cache_lock = _threading.Lock()

        # Per-session model overrides from /model command.
        # Key: session_key, Value: dict with model/provider/api_key/base_url/api_mode
        self._session_model_overrides: Dict[str, Dict[str, str]] = {}
        # Per-session reasoning effort overrides from /reasoning.
        # Key: session_key, Value: parsed reasoning config dict.
        self._session_reasoning_overrides: Dict[str, Dict[str, Any]] = {}
        self._kanban_notifier_profile = self._active_profile_name()
        # Teams meeting pipeline runtime (bound later when msgraph_webhook adapter exists).
        self._teams_pipeline_runtime = None
        self._teams_pipeline_runtime_error: Optional[str] = None
        # Track pending exec approvals per session
        # Key: session_key, Value: {"command": str, "pattern_key": str, ...}
        self._pending_approvals: Dict[str, Dict[str, Any]] = {}

        # Track platforms that failed to connect for background reconnection.
        # Key: Platform enum, Value: {"config": platform_config, "attempts": int, "next_retry": float}
        self._failed_platforms: Dict[Platform, Dict[str, Any]] = {}

        # Track pending /update prompt responses per session.
        # Key: session_key, Value: True when a prompt is waiting for user input.
        self._update_prompt_pending: Dict[str, bool] = {}

        # Slash-confirm state lives in tools.slash_confirm (module-level),
        # so platform adapters can resolve callbacks without a backref to
        # this runner.  Keep a local counter for confirm_id generation so
        # IDs stay compact (button callback_data has a 64-byte cap on
        # some platforms).
        import itertools as _itertools
        self._slash_confirm_counter = _itertools.count(1)

        # Persistent Honcho managers keyed by gateway session key.
        # This preserves write_frequency="session" semantics across short-lived
        # per-message AIAgent instances.



        # Ensure tirith security scanner is available (downloads if needed)
        try:
            from tools.tirith_security import ensure_installed
            ensure_installed(log_failures=False)
        except Exception:
            pass  # Non-fatal — fail-open at scan time if unavailable
        
        # Initialize session database for session_search tool support
        self._session_db = None
        try:
            from hermes_state import SessionDB
            self._session_db = SessionDB()
        except Exception as e:
            # WARNING (not DEBUG) so the failure appears in errors.log — matches
            # cli.py's handling of the same init path.  Users hitting NFS-mounted
            # HERMES_HOME silently lost /resume, /title, /history, /branch, and
            # session search without this.  The underlying cause (usually
            # "locking protocol" from NFS) is now also captured by
            # hermes_state.get_last_init_error() for slash-command error strings.
            logger.warning("SQLite session store not available: %s", e)

        # Opportunistic state.db maintenance: prune ended sessions older
        # than sessions.retention_days + optional VACUUM. Tracks last-run
        # in state_meta so it only actually executes once per
        # sessions.min_interval_hours.  Gateway is long-lived so blocking
        # a few seconds once per day is acceptable; failures are logged
        # but never raised.
        if self._session_db is not None:
            try:
                from hermes_cli.config import load_config as _load_full_config
                _sess_cfg = (_load_full_config().get("sessions") or {})
                if _sess_cfg.get("auto_prune", False):
                    self._session_db.maybe_auto_prune_and_vacuum(
                        retention_days=int(_sess_cfg.get("retention_days", 90)),
                        min_interval_hours=int(_sess_cfg.get("min_interval_hours", 24)),
                        vacuum=bool(_sess_cfg.get("vacuum_after_prune", True)),
                        sessions_dir=self.config.sessions_dir,
                    )
            except Exception as exc:
                logger.debug("state.db auto-maintenance skipped: %s", exc)

        # Opportunistic shadow-repo cleanup — deletes orphan/stale
        # checkpoint repos under ~/.hermes/checkpoints/.  Opt-in via
        # checkpoints.auto_prune, idempotent via .last_prune marker.
        try:
            from hermes_cli.config import load_config as _load_full_config
            _ckpt_cfg = (_load_full_config().get("checkpoints") or {})
            if _ckpt_cfg.get("auto_prune", False):
                from tools.checkpoint_manager import maybe_auto_prune_checkpoints
                maybe_auto_prune_checkpoints(
                    retention_days=int(_ckpt_cfg.get("retention_days", 7)),
                    min_interval_hours=int(_ckpt_cfg.get("min_interval_hours", 24)),
                    delete_orphans=bool(_ckpt_cfg.get("delete_orphans", True)),
                    max_total_size_mb=int(_ckpt_cfg.get("max_total_size_mb", 500)),
                )
        except Exception as exc:
            logger.debug("checkpoint auto-maintenance skipped: %s", exc)

        # DM pairing store for code-based user authorization
        from gateway.pairing import PairingStore
        self.pairing_store = PairingStore()
        
        # Event hook system
        from gateway.hooks import HookRegistry
        self.hooks = HookRegistry()

        # Per-chat voice reply mode: "off" | "voice_only" | "all"
        self._voice_mode: Dict[str, str] = self._load_voice_modes()
        # Recent voice transcripts per (guild,user) for duplicate suppression.
        # Protects against the same utterance being emitted twice by the voice
        # capture / STT pipeline, which otherwise produces a second delayed reply.
        self._recent_voice_transcripts: Dict[tuple[int, int], List[tuple[float, str]]] = {}

        # Track background tasks to prevent garbage collection mid-execution
        self._background_tasks: set = set()


    def _wire_teams_pipeline_runtime(self) -> None:
        """Bind the Teams meeting pipeline runtime to Graph webhook ingress.

        No-op when the msgraph_webhook adapter isn't running or the
        teams_pipeline plugin isn't enabled — lets the gateway start cleanly
        whether or not the user has opted into the pipeline.
        """
        if Platform.MSGRAPH_WEBHOOK not in self.adapters:
            return
        if not _teams_pipeline_plugin_enabled():
            logger.debug("Teams pipeline plugin is disabled; skipping runtime wiring")
            return
        try:
            from plugins.teams_pipeline.runtime import bind_gateway_runtime
        except Exception as exc:
            logger.warning("Teams pipeline runtime import failed: %s", exc)
            return
        try:
            bound = bind_gateway_runtime(self)
        except Exception as exc:
            logger.warning("Teams pipeline runtime wiring failed: %s", exc)
            return
        if bound:
            logger.info("Teams pipeline runtime bound to msgraph webhook ingress")
        elif self._teams_pipeline_runtime_error:
            logger.warning(
                "Teams pipeline runtime unavailable: %s",
                self._teams_pipeline_runtime_error,
            )


    def _warn_if_docker_media_delivery_is_risky(self) -> None:
        """Warn when Docker-backed gateways lack an explicit export mount.

        MEDIA delivery happens in the gateway process, so paths emitted by the model
        must be readable from the host. A plain container-local path like
        `/workspace/report.txt` or `/output/report.txt` often exists only inside
        Docker, so users commonly need a dedicated export mount such as
        `host-dir:/output`.
        """
        if os.getenv("TERMINAL_ENV", "").strip().lower() != "docker":
            return

        connected = self.config.get_connected_platforms()
        messaging_platforms = [p for p in connected if p not in {Platform.LOCAL, Platform.API_SERVER, Platform.WEBHOOK}]
        if not messaging_platforms:
            return

        raw_volumes = os.getenv("TERMINAL_DOCKER_VOLUMES", "").strip()
        volumes: List[str] = []
        if raw_volumes:
            try:
                parsed = json.loads(raw_volumes)
                if isinstance(parsed, list):
                    volumes = [str(v) for v in parsed if isinstance(v, str)]
            except Exception:
                logger.debug("Could not parse TERMINAL_DOCKER_VOLUMES for gateway media warning", exc_info=True)

        has_explicit_output_mount = False
        for spec in volumes:
            match = _DOCKER_VOLUME_SPEC_RE.match(spec)
            if not match:
                continue
            container_path = match.group("container")
            if container_path in _DOCKER_MEDIA_OUTPUT_CONTAINER_PATHS:
                has_explicit_output_mount = True
                break

        if has_explicit_output_mount:
            return

        logger.warning(
            "Docker backend is enabled for the messaging gateway but no explicit host-visible "
            "output mount (for example '/home/user/.hermes/cache/documents:/output') is configured. "
            "This is fine if the model already emits host-visible paths, but MEDIA file delivery can fail "
            "for container-local paths like '/workspace/...' or '/output/...'."
        )



    # -- Setup skill availability ----------------------------------------

    def _has_setup_skill(self) -> bool:
        """Check if the hermes-agent-setup skill is installed."""
        try:
            from tools.skill_manager_tool import _find_skill
            return _find_skill("hermes-agent-setup") is not None
        except Exception:
            return False

    # -- Voice mode persistence ------------------------------------------

    _VOICE_MODE_PATH = _hermes_home / "gateway_voice_mode.json"







    async def _safe_adapter_disconnect(self, adapter, platform) -> None:
        """Call adapter.disconnect() defensively, swallowing any error.

        Used when adapter.connect() failed or raised — the adapter may
        have allocated partial resources (aiohttp.ClientSession, poll
        tasks, child subprocesses) that would otherwise leak and surface
        as "Unclosed client session" warnings at process exit.

        Must tolerate partial-init state and never raise, since callers
        use it inside error-handling blocks.
        """
        timeout = self._adapter_disconnect_timeout_secs()
        try:
            if timeout <= 0:
                await adapter.disconnect()
            else:
                await asyncio.wait_for(adapter.disconnect(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Timed out after %.1fs while disconnecting %s adapter; continuing shutdown",
                timeout,
                platform.value if platform is not None else "adapter",
            )
        except Exception as e:
            logger.debug(
                "Defensive %s disconnect after failed connect raised: %s",
                platform.value if platform is not None else "adapter",
                e,
            )

    def _adapter_disconnect_timeout_secs(self) -> float:
        """Return the per-adapter disconnect timeout used during shutdown."""
        raw = os.getenv("HERMES_GATEWAY_ADAPTER_DISCONNECT_TIMEOUT", "").strip()
        if raw:
            try:
                timeout = float(raw)
            except ValueError:
                logger.warning(
                    "Ignoring invalid HERMES_GATEWAY_ADAPTER_DISCONNECT_TIMEOUT=%r",
                    raw,
                )
            else:
                return max(0.0, timeout)
        return _ADAPTER_DISCONNECT_TIMEOUT_SECS_DEFAULT

    def _platform_connect_timeout_secs(self) -> float:
        """Return the per-platform connect timeout used during startup/retry."""
        raw = os.getenv("HERMES_GATEWAY_PLATFORM_CONNECT_TIMEOUT", "").strip()
        if raw:
            try:
                timeout = float(raw)
            except ValueError:
                logger.warning(
                    "Ignoring invalid HERMES_GATEWAY_PLATFORM_CONNECT_TIMEOUT=%r",
                    raw,
                )
            else:
                return max(0.0, timeout)
        return _PLATFORM_CONNECT_TIMEOUT_SECS_DEFAULT

    async def _connect_adapter_with_timeout(self, adapter, platform) -> bool:
        """Connect an adapter without allowing one platform to block others."""
        timeout = self._platform_connect_timeout_secs()
        if timeout <= 0:
            return await adapter.connect()
        try:
            return await asyncio.wait_for(adapter.connect(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"{platform.value} connect timed out after {timeout:g}s"
            ) from exc

    @property
    def should_exit_cleanly(self) -> bool:
        return self._exit_cleanly

    @property
    def should_exit_with_failure(self) -> bool:
        return self._exit_with_failure

    @property
    def exit_reason(self) -> Optional[str]:
        return self._exit_reason

    @property
    def exit_code(self) -> Optional[int]:
        return self._exit_code



    # Telegram's General (pinned top) topic in forum-enabled private chats.
    # Bot API behavior varies: some clients omit message_thread_id for
    # General, others send "1". Treat both as "root" for lobby/lane purposes.
    _TELEGRAM_GENERAL_TOPIC_IDS = frozenset({"", "1"})



    _TELEGRAM_LOBBY_REMINDER_COOLDOWN_S = 30.0








    def _resolve_turn_agent_config(self, user_message: str, model: str, runtime_kwargs: dict) -> dict:
        """Build the effective model/runtime config for a single turn.

        Always uses the session's primary model/provider.  If `/fast` is
        enabled and the model supports Priority Processing / Anthropic fast
        mode, attach `request_overrides` so the API call is marked
        accordingly.
        """
        from hermes_cli.models import resolve_fast_mode_overrides

        runtime = {
            "api_key": runtime_kwargs.get("api_key"),
            "base_url": runtime_kwargs.get("base_url"),
            "provider": runtime_kwargs.get("provider"),
            "api_mode": runtime_kwargs.get("api_mode"),
            "command": runtime_kwargs.get("command"),
            "args": list(runtime_kwargs.get("args") or []),
            "credential_pool": runtime_kwargs.get("credential_pool"),
        }
        route = {
            "model": model,
            "runtime": runtime,
            "signature": (
                model,
                runtime["provider"],
                runtime["base_url"],
                runtime["api_mode"],
                runtime["command"],
                tuple(runtime["args"]),
            ),
        }

        service_tier = getattr(self, "_service_tier", None)
        if not service_tier:
            route["request_overrides"] = {}
            return route

        try:
            overrides = resolve_fast_mode_overrides(route["model"])
        except Exception:
            overrides = None
        route["request_overrides"] = overrides or {}
        return route

    async def _handle_adapter_fatal_error(self, adapter: BasePlatformAdapter) -> None:
        """React to an adapter failure after startup.

        If the error is retryable (e.g. network blip, DNS failure), queue the
        platform for background reconnection instead of giving up permanently.
        """
        logger.error(
            "Fatal %s adapter error (%s): %s",
            adapter.platform.value,
            adapter.fatal_error_code or "unknown",
            adapter.fatal_error_message or "unknown error",
        )
        self._update_platform_runtime_status(
            adapter.platform.value,
            platform_state="retrying" if adapter.fatal_error_retryable else "fatal",
            error_code=adapter.fatal_error_code,
            error_message=adapter.fatal_error_message,
        )

        existing = self.adapters.get(adapter.platform)
        if existing is adapter:
            try:
                await adapter.disconnect()
            finally:
                self.adapters.pop(adapter.platform, None)
                self.delivery_router.adapters = self.adapters

        # Queue retryable failures for background reconnection
        if adapter.fatal_error_retryable:
            platform_config = self.config.platforms.get(adapter.platform)
            if platform_config and adapter.platform not in self._failed_platforms:
                self._failed_platforms[adapter.platform] = {
                    "config": platform_config,
                    "attempts": 0,
                    "next_retry": time.monotonic() + 30,
                }
                logger.info(
                    "%s queued for background reconnection",
                    adapter.platform.value,
                )

        if not self.adapters and not self._failed_platforms:
            self._exit_reason = adapter.fatal_error_message or "All messaging adapters disconnected"
            if adapter.fatal_error_retryable:
                self._exit_with_failure = True
                logger.error("No connected messaging platforms remain. Shutting down gateway for service restart.")
            else:
                logger.error("No connected messaging platforms remain. Shutting down gateway cleanly.")
            await self.stop()
        elif not self.adapters and self._failed_platforms:
            # All platforms are down and queued for background reconnection.
            # Keep the gateway alive so:
            #   • cron jobs still run
            #   • the reconnect watcher can recover platforms when the
            #     underlying problem clears (proxy comes back, user runs
            #     `hermes whatsapp`, etc.)
            # We used to exit-with-failure here to trigger systemd restart,
            # but that converted a transient outage into a restart loop and
            # killed in-process state every time. The reconnect watcher
            # already handles long-running recovery — let it do its job.
            logger.warning(
                "No connected messaging platforms remain, but %d platform(s) "
                "queued for reconnection — gateway staying alive, watcher will "
                "retry in background.",
                len(self._failed_platforms),
            )

    def _request_clean_exit(self, reason: str) -> None:
        self._exit_cleanly = True
        self._exit_reason = reason
        self._shutdown_event.set()

    def _running_agent_count(self) -> int:
        return len(self._running_agents)

    def _status_action_label(self) -> str:
        return "restart" if self._restart_requested else "shutdown"

    def _status_action_gerund(self) -> str:
        return "restarting" if self._restart_requested else "shutting down"

    def _queue_during_drain_enabled(self) -> bool:
        # Both "queue" and "steer" modes imply the user doesn't want messages
        # to be lost during restart — queue them for the newly-spawned gateway
        # process to pick up.  "interrupt" mode drops them (current behaviour).
        return self._restart_requested and self._busy_input_mode in {"queue", "steer"}

    # -------- /queue FIFO helpers --------------------------------------
    # /queue must produce one full agent turn per invocation, in FIFO
    # order, with no merging.  The adapter's _pending_messages dict is a
    # single "next-up" slot (shared with photo-burst follow-ups), so we
    # use it for the head of the queue and an overflow list for the
    # tail.  Enqueue puts new items in the slot when free, otherwise in
    # the overflow.  Promotion (called after each run's drain) moves the
    # next overflow item into the slot so the following recursion picks
    # it up.  Clearing happens on /new and /reset via
    # _handle_reset_command.

    def _enqueue_fifo(self, session_key: str, queued_event: "MessageEvent", adapter: Any) -> None:
        """Append a /queue event to the FIFO chain for a session."""
        if adapter is None:
            return
        pending_slot = getattr(adapter, "_pending_messages", None)
        if pending_slot is None:
            return
        queued_events = getattr(self, "_queued_events", None)
        if queued_events is None:
            queued_events = {}
            self._queued_events = queued_events
        if session_key in pending_slot:
            queued_events.setdefault(session_key, []).append(queued_event)
        else:
            pending_slot[session_key] = queued_event

    def _promote_queued_event(
        self,
        session_key: str,
        adapter: Any,
        pending_event: Optional["MessageEvent"],
    ) -> Optional["MessageEvent"]:
        """Promote the next overflow item after the slot was drained.

        Called at the drain site after _dequeue_pending_event consumed
        (or failed to consume) the slot.  If there's an overflow item:
          - When pending_event is None (slot was empty), return the
            overflow head as the new pending_event.
          - When pending_event already exists (slot was populated by an
            interrupt follow-up or similar), stage the overflow head in
            the slot so the NEXT recursion picks it up.
        Returns the (possibly updated) pending_event for drain to use.
        """
        queued_events = getattr(self, "_queued_events", None)
        if not queued_events:
            return pending_event
        overflow = queued_events.get(session_key)
        if not overflow:
            return pending_event
        next_queued = overflow.pop(0)
        if not overflow:
            queued_events.pop(session_key, None)
        if pending_event is None:
            return next_queued
        if adapter is not None and hasattr(adapter, "_pending_messages"):
            adapter._pending_messages[session_key] = next_queued
        else:
            # No adapter — push back so we don't silently drop the item.
            queued_events.setdefault(session_key, []).insert(0, next_queued)
        return pending_event

    def _queue_depth(self, session_key: str, *, adapter: Any = None) -> int:
        """Total pending /queue items for a session — slot + overflow."""
        queued_events = getattr(self, "_queued_events", None) or {}
        depth = len(queued_events.get(session_key, []))
        if adapter is not None and session_key in getattr(adapter, "_pending_messages", {}):
            depth += 1
        return depth

    def _update_runtime_status(self, gateway_state: Optional[str] = None, exit_reason: Optional[str] = None) -> None:
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(
                gateway_state=gateway_state,
                exit_reason=exit_reason,
                restart_requested=self._restart_requested,
                active_agents=self._running_agent_count(),
            )
        except Exception:
            pass

    def _update_platform_runtime_status(
        self,
        platform: str,
        *,
        platform_state: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(
                platform=platform,
                platform_state=platform_state,
                error_code=error_code,
                error_message=error_message,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Per-platform circuit breaker (pause/resume) — used by the reconnect
    # watcher when a retryable failure recurs past a threshold, and by the
    # /platform pause|resume slash command for manual control.
    # ------------------------------------------------------------------
    def _pause_failed_platform(self, platform, *, reason: str = "") -> None:
        """Mark a queued platform as paused — keep it in ``_failed_platforms``
        but stop the reconnect watcher from hammering it.

        Used by the circuit breaker after ``_PAUSE_AFTER_FAILURES`` consecutive
        retryable failures, and by ``/platform pause <name>`` for manual
        intervention.  Paused platforms are surfaced in ``/platform list``
        and resumed with ``/platform resume <name>``.
        """
        info = getattr(self, "_failed_platforms", {}).get(platform)
        if info is None:
            return
        if info.get("paused"):
            return
        info["paused"] = True
        info["pause_reason"] = reason or "auto-paused after repeated failures"
        # Push next_retry far enough out that even if "paused" is missed
        # by a stale code path, the watcher won't fire on it.
        info["next_retry"] = float("inf")
        try:
            self._update_platform_runtime_status(
                platform.value,
                platform_state="paused",
                error_code=None,
                error_message=info["pause_reason"],
            )
        except Exception:
            pass
        logger.warning(
            "%s paused after %d consecutive failures (%s) — "
            "fix the underlying issue then run `/platform resume %s` "
            "to retry, or `hermes gateway restart` to restart the gateway.",
            platform.value, info.get("attempts", 0),
            info["pause_reason"], platform.value,
        )

    def _resume_paused_platform(self, platform) -> bool:
        """Unpause a platform — reset its attempt counter and schedule an
        immediate retry.  Returns True if the platform was paused and is
        now queued; False if it wasn't paused (or wasn't in the queue).
        """
        info = getattr(self, "_failed_platforms", {}).get(platform)
        if info is None:
            return False
        if not info.get("paused"):
            return False
        info["paused"] = False
        info.pop("pause_reason", None)
        info["attempts"] = 0
        info["next_retry"] = time.monotonic()  # retry on next watcher tick
        try:
            self._update_platform_runtime_status(
                platform.value,
                platform_state="retrying",
                error_code=None,
                error_message=None,
            )
        except Exception:
            pass
        logger.info("%s resumed — retrying on next watcher tick", platform.value)
        return True

    @staticmethod

    @staticmethod

    @staticmethod

    @staticmethod
    def _parse_reasoning_command_args(raw_args: str) -> tuple[str, bool]:
        """Parse `/reasoning` args into `(value, persist_global)`.

        `/reasoning <level>` is session-scoped by default. `--global` may be
        supplied in any position to persist the change to config.yaml.
        """
        import shlex

        text = str(raw_args or "").strip().replace("—", "--")
        if not text:
            return "", False
        try:
            tokens = shlex.split(text)
        except ValueError:
            tokens = text.split()

        persist_global = False
        value_tokens = []
        for token in tokens:
            if token == "--global":
                persist_global = True
            else:
                value_tokens.append(token)
        return " ".join(value_tokens).strip().lower(), persist_global

    def _snapshot_running_agents(self) -> Dict[str, Any]:
        return {
            session_key: agent
            for session_key, agent in self._running_agents.items()
            if agent is not _AGENT_PENDING_SENTINEL
        }

    def _queue_or_replace_pending_event(self, session_key: str, event: MessageEvent) -> None:
        adapter = self.adapters.get(event.source.platform)
        if not adapter:
            return
        merge_pending_message_event(adapter._pending_messages, session_key, event)


    async def _drain_active_agents(self, timeout: float) -> tuple[Dict[str, Any], bool]:
        snapshot = self._snapshot_running_agents()
        last_active_count = self._running_agent_count()
        last_status_at = 0.0

        def _maybe_update_status(force: bool = False) -> None:
            nonlocal last_active_count, last_status_at
            now = asyncio.get_running_loop().time()
            active_count = self._running_agent_count()
            if force or active_count != last_active_count or (now - last_status_at) >= 1.0:
                self._update_runtime_status("draining")
                last_active_count = active_count
                last_status_at = now

        if not self._running_agents:
            _maybe_update_status(force=True)
            return snapshot, False

        _maybe_update_status(force=True)
        if timeout <= 0:
            return snapshot, True

        deadline = asyncio.get_running_loop().time() + timeout
        while self._running_agents and asyncio.get_running_loop().time() < deadline:
            _maybe_update_status()
            await asyncio.sleep(0.1)
        timed_out = bool(self._running_agents)
        _maybe_update_status(force=True)
        return snapshot, timed_out

    def _interrupt_running_agents(self, reason: str) -> None:
        for session_key, agent in list(self._running_agents.items()):
            if agent is _AGENT_PENDING_SENTINEL:
                continue
            try:
                agent.interrupt(reason)
                logger.debug("Interrupted running agent for session %s during shutdown", session_key)
            except Exception as e:
                logger.debug("Failed interrupting agent during shutdown: %s", e)


    def _finalize_shutdown_agents(self, active_agents: Dict[str, Any]) -> None:
        for agent in active_agents.values():
            try:
                from hermes_cli.plugins import invoke_hook as _invoke_hook
                _invoke_hook(
                    "on_session_finalize",
                    session_id=getattr(agent, "session_id", None),
                    platform="gateway",
                )
            except Exception:
                pass
            self._cleanup_agent_resources(agent)

    def _should_emit_long_running_notification(
        self,
        session_key: Optional[str],
        agent: Any,
        executor_task: Optional[Any],
    ) -> bool:
        """Only emit the heartbeat while this task still owns the live run.

        Guards against a stale ``running: delegate_task`` heartbeat outliving the
        run that started it: stop once the executor finishes, the agent is gone,
        or the session key has been rebound to a different live agent (e.g. the
        user sent ``/new`` and a fresh agent took the slot mid-run, #12029).
        """
        if agent is None:
            return False
        if executor_task is not None and executor_task.done():
            return False
        if session_key and self._running_agents.get(session_key) is not agent:
            return False
        return True

    def _cleanup_agent_resources(self, agent: Any) -> None:
        """Best-effort cleanup for temporary or cached agent instances."""
        if agent is None:
            return
        try:
            if hasattr(agent, "shutdown_memory_provider"):
                # Pass the agent's own conversation transcript so memory
                # providers' ``on_session_end`` hooks see the real messages
                # instead of the empty default (#15165). ``_session_messages``
                # is set on ``AIAgent`` (run_agent.py:1518) and refreshed at
                # the end of every ``run_conversation`` turn via
                # ``_persist_session``; on an agent built through
                # ``object.__new__`` (test stubs) the attribute may be
                # absent, so ``getattr`` with a ``None`` default keeps the
                # call signature-compatible with the pre-fix behaviour
                # (``shutdown_memory_provider(messages=None)``).
                session_messages = getattr(agent, "_session_messages", None)
                if isinstance(session_messages, list):
                    agent.shutdown_memory_provider(session_messages)
                else:
                    agent.shutdown_memory_provider()
        except Exception:
            pass
        # Close tool resources (terminal sandboxes, browser daemons,
        # background processes, httpx clients) to prevent zombie
        # process accumulation.
        try:
            if hasattr(agent, "close"):
                agent.close()
        except Exception:
            pass
        # Auxiliary async clients (session_search/web/vision/etc.) live in a
        # process-global cache and are created inside worker threads. Clean up
        # any entries whose event loop is now dead so their httpx transports do
        # not accumulate across gateway turns.
        try:
            from agent.auxiliary_client import cleanup_stale_async_clients
            cleanup_stale_async_clients()
        except Exception:
            pass

    _STUCK_LOOP_THRESHOLD = 3  # restarts while active before auto-suspend
    _STUCK_LOOP_FILE = ".restart_failure_counts"

    def _increment_restart_failure_counts(self, active_session_keys: set) -> None:
        """Increment restart-failure counters for sessions active at shutdown.

        Persists to a JSON file so counters survive across restarts.
        Sessions NOT in active_session_keys are removed (they completed
        successfully, so the loop is broken).
        """
        import json

        path = _hermes_home / self._STUCK_LOOP_FILE
        try:
            counts = json.loads(path.read_text()) if path.exists() else {}
        except Exception:
            counts = {}

        # Increment active sessions, remove inactive ones (loop broken)
        new_counts = {}
        for key in active_session_keys:
            new_counts[key] = counts.get(key, 0) + 1
        # Keep any entries that are still above 0 even if not active now
        # (they might become active again next restart)

        try:
            atomic_json_write(path, new_counts, indent=None)
        except Exception:
            pass


    def _clear_restart_failure_count(self, session_key: str) -> None:
        """Clear the restart-failure counter for a session that completed OK.

        Called after a successful agent turn to signal the loop is broken.
        """
        import json

        path = _hermes_home / self._STUCK_LOOP_FILE
        if not path.exists():
            return
        try:
            counts = json.loads(path.read_text())
            if session_key in counts:
                del counts[session_key]
                if counts:
                    atomic_json_write(path, counts, indent=None)
                else:
                    path.unlink(missing_ok=True)
        except Exception:
            pass

    async def _launch_detached_restart_command(self) -> None:
        import shutil
        import subprocess

        hermes_cmd = _resolve_hermes_bin()
        if not hermes_cmd:
            logger.error("Could not locate hermes binary for detached /restart")
            return

        current_pid = os.getpid()

        # On Windows there's no bash/setsid chain — spawn a tiny Python
        # watcher directly via sys.executable instead.  The watcher polls
        # current_pid, waits for our exit, then runs `hermes gateway
        # restart` with detach flags so the respawn survives the CLI
        # that triggered the /restart command closing its console.
        if sys.platform == "win32":
            import textwrap
            from hermes_cli._subprocess_compat import windows_detach_popen_kwargs

            cmd_argv = [*hermes_cmd, "gateway", "restart"]
            watcher = textwrap.dedent(
                """
                import os, subprocess, sys, time
                pid = int(sys.argv[1])
                cmd = sys.argv[2:]
                deadline = time.monotonic() + 120

                def _alive(p):
                    # On Windows, os.kill(pid, 0) is NOT a no-op — it maps to
                    # GenerateConsoleCtrlEvent(0, pid) (bpo-14484). Use the
                    # Win32 handle-based existence check instead.
                    if os.name == 'nt':
                        import ctypes
                        k32 = ctypes.windll.kernel32
                        k32.OpenProcess.restype = ctypes.c_void_p
                        k32.WaitForSingleObject.restype = ctypes.c_uint
                        k32.GetLastError.restype = ctypes.c_uint
                        h = k32.OpenProcess(0x1000 | 0x100000, False, int(p))
                        if not h:
                            return k32.GetLastError() != 87
                        try:
                            return k32.WaitForSingleObject(h, 0) == 0x102
                        finally:
                            k32.CloseHandle(h)
                    try:
                        os.kill(int(p), 0)
                        return True
                    except ProcessLookupError:
                        return False
                    except PermissionError:
                        return True
                    except OSError:
                        return False

                while time.monotonic() < deadline:
                    if not _alive(pid):
                        break
                    time.sleep(0.2)
                _CREATE_NEW_PROCESS_GROUP = 0x00000200
                _DETACHED_PROCESS = 0x00000008
                _CREATE_NO_WINDOW = 0x08000000
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=_CREATE_NEW_PROCESS_GROUP | _DETACHED_PROCESS | _CREATE_NO_WINDOW,
                )
                """
            ).strip()
            subprocess.Popen(
                [sys.executable, "-c", watcher, str(current_pid), *cmd_argv],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **windows_detach_popen_kwargs(),
            )
            return

        cmd = " ".join(shlex.quote(part) for part in hermes_cmd)
        shell_cmd = (
            f"while kill -0 {current_pid} 2>/dev/null; do sleep 0.2; done; "
            f"{cmd} gateway restart"
        )
        setsid_bin = shutil.which("setsid")
        if setsid_bin:
            subprocess.Popen(
                [setsid_bin, "bash", "-lc", shell_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        else:
            subprocess.Popen(
                ["bash", "-lc", shell_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

    def request_restart(self, *, detached: bool = False, via_service: bool = False) -> bool:
        if self._restart_task_started:
            return False
        self._restart_requested = True
        self._restart_detached = detached
        self._restart_via_service = via_service
        self._restart_task_started = True

        async def _run_restart() -> None:
            await asyncio.sleep(0.05)
            await self.stop(restart=True, detached_restart=detached, service_restart=via_service)

        task = asyncio.create_task(_run_restart())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return True

    # Drain-timeout reasons set by _stop_impl() when a still-running turn is
    # force-interrupted; "restart_interrupted" is set by
    # SessionStore.suspend_recently_active() on crash recovery (no
    # .clean_shutdown marker).  All three mean "the agent was mid-turn and
    # we killed it" — eligible for startup auto-resume.
    _AUTO_RESUME_REASONS = frozenset(
        {"restart_timeout", "shutdown_timeout", "restart_interrupted"}
    )


    async def start(self) -> bool:
        """
        Start the gateway and all configured platform adapters.
        
        Returns True if at least one adapter connected successfully.
        """
        logger.info("Starting Hermes Gateway...")
        try:
            self._gateway_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._gateway_loop = None
        logger.info("Session storage: %s", self.config.sessions_dir)

        # Sanity-check that systemd's TimeoutStopSec covers our drain
        # window.  When the user upgraded hermes-agent without re-running
        # ``hermes setup``, their unit file may still encode the old
        # default — in which case SIGKILL hits mid-drain and looks like
        # a phantom kill in the journal.  Best-effort, never raises.
        try:
            from gateway.shutdown_forensics import check_systemd_timing_alignment
            _alignment = check_systemd_timing_alignment(self._restart_drain_timeout)
            if _alignment is not None and _alignment.get("mismatch"):
                logger.warning(
                    "Stale systemd unit detected: %s has TimeoutStopSec=%.0fs but "
                    "drain_timeout=%.0fs (expected >=%.0fs). systemd may SIGKILL the "
                    "gateway mid-drain. Run `hermes gateway service install --replace` "
                    "to regenerate the unit, or shorten agent.restart_drain_timeout.",
                    _alignment.get("unit", "(unknown)"),
                    _alignment["timeout_stop_sec"],
                    _alignment["drain_timeout"],
                    _alignment["expected_min"],
                )
        except Exception as _e:
            logger.debug("check_systemd_timing_alignment failed: %s", _e)
        # Log the resolved max_iterations budget so operators can verify the
        # config.yaml → env bridge did the right thing at a glance (instead
        # of silently running at a stale .env value for weeks).
        try:
            _effective_max_iter = int(os.getenv("HERMES_MAX_ITERATIONS", "90"))
            logger.info(
                "Agent budget: max_iterations=%d (agent.max_turns from config.yaml, "
                "or HERMES_MAX_ITERATIONS from .env, or default 90)",
                _effective_max_iter,
            )
        except Exception:
            pass
        # Redaction status: ON by default (#17691). Surface a prominent
        # warning if an operator has explicitly opted out so they don't
        # forget the downgrade is active — the redactor snapshots its
        # state at import time, so this log line is the source of truth
        # for this process's lifetime.
        try:
            _redact_raw = os.getenv("HERMES_REDACT_SECRETS", "true")
            _redact_on = _redact_raw.lower() in {"1", "true", "yes", "on"}
            if _redact_on:
                logger.info(
                    "Secret redaction: ENABLED (tool output, logs, and chat "
                    "responses are scrubbed before delivery)"
                )
            else:
                logger.warning(
                    "Secret redaction: DISABLED (HERMES_REDACT_SECRETS=%s). "
                    "API keys and tokens may appear verbatim in chat output, "
                    "session JSONs, and logs. Set security.redact_secrets: true "
                    "in config.yaml to re-enable.",
                    _redact_raw,
                )
        except Exception:
            pass
        try:
            from hermes_cli.profiles import get_active_profile_name
            _profile = get_active_profile_name()
            if _profile and _profile != "default":
                logger.info("Active profile: %s", _profile)
        except Exception:
            pass
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(gateway_state="starting", exit_reason=None)
        except Exception:
            pass

        # Log any active supply-chain security advisories. Operators see this
        # in gateway.log and `hermes status` surfaces it; we do NOT block
        # startup or surface it inline to user messages, since the gateway
        # operator is the one who can act on it (uninstall the package,
        # rotate credentials).  See hermes_cli/security_advisories.py.
        try:
            from hermes_cli.security_advisories import (
                detect_compromised,
                gateway_log_message,
            )
            _adv_hits = detect_compromised()
            _adv_msg = gateway_log_message(_adv_hits)
            if _adv_msg:
                logger.warning("%s", _adv_msg)
                logger.warning(
                    "Run `hermes doctor` on the gateway host for full "
                    "remediation steps."
                )
        except Exception:
            logger.debug(
                "security advisory check failed at gateway startup",
                exc_info=True,
            )
        
        # Warn if no user allowlists are configured and open access is not opted in
        _builtin_allowed_vars = (
            "TELEGRAM_ALLOWED_USERS", "DISCORD_ALLOWED_USERS",
            "WHATSAPP_ALLOWED_USERS", "SLACK_ALLOWED_USERS",
            "SIGNAL_ALLOWED_USERS", "SIGNAL_GROUP_ALLOWED_USERS",
            "TELEGRAM_GROUP_ALLOWED_USERS",
            "TELEGRAM_GROUP_ALLOWED_CHATS",
            "EMAIL_ALLOWED_USERS",
            "SMS_ALLOWED_USERS", "MATTERMOST_ALLOWED_USERS",
            "MATRIX_ALLOWED_USERS", "DINGTALK_ALLOWED_USERS",
            "FEISHU_ALLOWED_USERS",
            "WECOM_ALLOWED_USERS",
            "WECOM_CALLBACK_ALLOWED_USERS",
            "WEIXIN_ALLOWED_USERS",
            "BLUEBUBBLES_ALLOWED_USERS",
            "QQ_ALLOWED_USERS",
            "YUANBAO_ALLOWED_USERS",
            "GATEWAY_ALLOWED_USERS",
        )
        _builtin_allow_all_vars = (
            "TELEGRAM_ALLOW_ALL_USERS", "DISCORD_ALLOW_ALL_USERS",
            "WHATSAPP_ALLOW_ALL_USERS", "SLACK_ALLOW_ALL_USERS",
            "SIGNAL_ALLOW_ALL_USERS", "EMAIL_ALLOW_ALL_USERS",
            "SMS_ALLOW_ALL_USERS", "MATTERMOST_ALLOW_ALL_USERS",
            "MATRIX_ALLOW_ALL_USERS", "DINGTALK_ALLOW_ALL_USERS",
            "FEISHU_ALLOW_ALL_USERS",
            "WECOM_ALLOW_ALL_USERS",
            "WECOM_CALLBACK_ALLOW_ALL_USERS",
            "WEIXIN_ALLOW_ALL_USERS",
            "BLUEBUBBLES_ALLOW_ALL_USERS",
            "QQ_ALLOW_ALL_USERS",
            "YUANBAO_ALLOW_ALL_USERS",
        )
        # Also pick up plugin-registered platforms — each entry can declare
        # its own allowed_users_env / allow_all_env, so the warning stays
        # accurate as plugins like IRC come online.
        _plugin_allowed_vars: tuple = ()
        _plugin_allow_all_vars: tuple = ()
        try:
            from gateway.platform_registry import platform_registry
            _plugin_allowed_vars = tuple(
                e.allowed_users_env for e in platform_registry.plugin_entries()
                if e.allowed_users_env
            )
            _plugin_allow_all_vars = tuple(
                e.allow_all_env for e in platform_registry.plugin_entries()
                if e.allow_all_env
            )
        except Exception:
            pass
        _any_allowlist = any(
            os.getenv(v) for v in _builtin_allowed_vars + _plugin_allowed_vars
        )
        _allow_all = os.getenv("GATEWAY_ALLOW_ALL_USERS", "").lower() in {"true", "1", "yes"} or any(
            os.getenv(v, "").lower() in {"true", "1", "yes"}
            for v in _builtin_allow_all_vars + _plugin_allow_all_vars
        )
        if not _any_allowlist and not _allow_all:
            logger.warning(
                "No user allowlists configured. All unauthorized users will be denied. "
                "Set GATEWAY_ALLOW_ALL_USERS=true in ~/.hermes/.env to allow open access, "
                "or configure platform allowlists (e.g., TELEGRAM_ALLOWED_USERS=your_id)."
            )
        
        # Discover Python plugins before shell hooks so plugin block
        # decisions take precedence in tie cases.  The CLI startup path
        # does this via an explicit call in hermes_cli/main.py; the
        # gateway lazily imports run_agent inside per-request handlers,
        # so the discover_plugins() side-effect in model_tools.py is NOT
        # guaranteed to have run by the time we reach this point.
        try:
            from hermes_cli.plugins import discover_plugins
            discover_plugins()
        except Exception:
            logger.warning(
                "plugin discovery failed at gateway startup", exc_info=True,
            )

        # Register declarative shell hooks from cli-config.yaml.  Gateway
        # has no TTY, so consent has to come from one of the three opt-in
        # channels (--accept-hooks on launch, HERMES_ACCEPT_HOOKS env var,
        # or hooks_auto_accept: true in config.yaml).  We pass
        # accept_hooks=False here and let register_from_config resolve
        # the effective value from env + config itself — the CLI-side
        # registration already honored --accept-hooks, and re-reading
        # hooks_auto_accept here would just duplicate that lookup.
        # Failures are logged but must never block gateway startup.
        try:
            from hermes_cli.config import load_config
            from agent.shell_hooks import register_from_config
            register_from_config(load_config(), accept_hooks=False)
        except Exception:
            logger.debug(
                "shell-hook registration failed at gateway startup",
                exc_info=True,
            )

        # Discover and load event hooks
        self.hooks.discover_and_load()

        
        # Recover background processes from checkpoint (crash recovery)
        try:
            from tools.process_registry import process_registry
            recovered = process_registry.recover_from_checkpoint()
            if recovered:
                logger.info("Recovered %s background process(es) from previous run", recovered)
        except Exception as e:
            logger.warning("Process checkpoint recovery: %s", e)

        # Suspend sessions that were active when the gateway last exited.
        # This prevents stuck sessions from being blindly resumed on restart,
        # which can create an unrecoverable loop (#7536).  Suspended sessions
        # auto-reset on the next incoming message, giving the user a clean start.
        #
        # SKIP suspension after a clean (graceful) shutdown — the previous
        # process already drained active agents, so sessions aren't stuck.
        # This prevents unwanted auto-resets after `hermes update`,
        # `hermes gateway restart`, or `/restart`.
        _clean_marker = _hermes_home / ".clean_shutdown"
        if _clean_marker.exists():
            logger.info("Previous gateway exited cleanly — skipping session suspension")
            try:
                _clean_marker.unlink()
            except Exception:
                pass
        else:
            try:
                suspended = self.session_store.suspend_recently_active()
                if suspended:
                    logger.info("Marked %d in-flight session(s) as resumable from previous run", suspended)
            except Exception as e:
                logger.warning("Session suspension on startup failed: %s", e)

        # Stuck-loop detection (#7536): if a session has been active across
        # 3+ consecutive restarts, it's probably stuck in a loop (the same
        # history keeps causing the agent to hang).  Auto-suspend it so the
        # user gets a clean slate on the next message.
        try:
            stuck = self._suspend_stuck_loop_sessions()
            if stuck:
                logger.warning("Auto-suspended %d stuck-loop session(s)", stuck)
        except Exception as e:
            logger.debug("Stuck-loop detection failed: %s", e)

        connected_count = 0
        enabled_platform_count = 0
        startup_nonretryable_errors: list[str] = []
        startup_retryable_errors: list[str] = []
        
        # Initialize and connect each configured platform
        for platform, platform_config in self.config.platforms.items():
            if not platform_config.enabled:
                continue
            enabled_platform_count += 1
            
            adapter = self._create_adapter(platform, platform_config)
            if not adapter:
                # Distinguish between missing builtin deps and missing plugin
                _pval = platform.value
                _builtin_names = {m.value for m in Platform.__members__.values()}
                if _pval not in _builtin_names:
                    logger.warning(
                        "No adapter for '%s' — is the plugin installed? "
                        "(platform is enabled in config.yaml but no plugin registered it)",
                        _pval,
                    )
                else:
                    logger.warning("No adapter available for %s", _pval)
                continue
            
            # Set up message + fatal error handlers
            adapter.set_message_handler(self._handle_message)
            adapter.set_fatal_error_handler(self._handle_adapter_fatal_error)
            adapter.set_session_store(self.session_store)
            adapter.set_busy_session_handler(self._handle_active_session_busy_message)
            
            # Try to connect
            logger.info("Connecting to %s...", platform.value)
            self._update_platform_runtime_status(
                platform.value,
                platform_state="connecting",
                error_code=None,
                error_message=None,
            )
            try:
                success = await self._connect_adapter_with_timeout(adapter, platform)
                if success:
                    self.adapters[platform] = adapter
                    self._sync_voice_mode_state_to_adapter(adapter)
                    connected_count += 1
                    self._update_platform_runtime_status(
                        platform.value,
                        platform_state="connected",
                        error_code=None,
                        error_message=None,
                    )
                    logger.info("✓ %s connected", platform.value)
                else:
                    logger.warning("✗ %s failed to connect", platform.value)
                    # Defensive cleanup: a failed connect() may have
                    # allocated resources (aiohttp.ClientSession, poll
                    # tasks, bridge subprocesses) before giving up.
                    # Without this call, those resources are orphaned
                    # and Python logs "Unclosed client session" at
                    # process exit. Adapter disconnect() implementations
                    # are expected to be idempotent and tolerate
                    # partial-init state.
                    await self._safe_adapter_disconnect(adapter, platform)
                    if adapter.has_fatal_error:
                        self._update_platform_runtime_status(
                            platform.value,
                            platform_state="retrying" if adapter.fatal_error_retryable else "fatal",
                            error_code=adapter.fatal_error_code,
                            error_message=adapter.fatal_error_message,
                        )
                        target = (
                            startup_retryable_errors
                            if adapter.fatal_error_retryable
                            else startup_nonretryable_errors
                        )
                        target.append(
                            f"{platform.value}: {adapter.fatal_error_message}"
                        )
                        # Queue for reconnection if the error is retryable
                        if adapter.fatal_error_retryable:
                            self._failed_platforms[platform] = {
                                "config": platform_config,
                                "attempts": 1,
                                "next_retry": time.monotonic() + 30,
                            }
                    else:
                        self._update_platform_runtime_status(
                            platform.value,
                            platform_state="retrying",
                            error_code=None,
                            error_message="failed to connect",
                        )
                        startup_retryable_errors.append(
                            f"{platform.value}: failed to connect"
                        )
                        # No fatal error info means likely a transient issue — queue for retry
                        self._failed_platforms[platform] = {
                            "config": platform_config,
                            "attempts": 1,
                            "next_retry": time.monotonic() + 30,
                        }
            except Exception as e:
                logger.error("✗ %s error: %s", platform.value, e)
                # Same defensive cleanup path for exceptions — an adapter
                # that raised mid-connect may still have a live
                # aiohttp.ClientSession or child subprocess.
                await self._safe_adapter_disconnect(adapter, platform)
                self._update_platform_runtime_status(
                    platform.value,
                    platform_state="retrying",
                    error_code=None,
                    error_message=str(e),
                )
                startup_retryable_errors.append(f"{platform.value}: {e}")
                # Unexpected exceptions are typically transient — queue for retry
                self._failed_platforms[platform] = {
                    "config": platform_config,
                    "attempts": 1,
                    "next_retry": time.monotonic() + 30,
                }
        
        if connected_count == 0:
            if startup_nonretryable_errors:
                reason = "; ".join(startup_nonretryable_errors)
                logger.error("Gateway hit a non-retryable startup conflict: %s", reason)
                try:
                    from gateway.status import write_runtime_status
                    write_runtime_status(gateway_state="startup_failed", exit_reason=reason)
                except Exception:
                    pass
                self._request_clean_exit(reason)
                return True
            if enabled_platform_count > 0:
                if startup_retryable_errors:
                    # All enabled platforms hit retryable failures (network
                    # blip, bridge not paired, npm install timeout, etc.).
                    # Keep the gateway alive so:
                    #   • cron jobs still run
                    #   • the reconnect watcher gets a chance to recover the
                    #     failing platforms once the underlying problem is
                    #     fixed (e.g. user runs `hermes whatsapp`, fixes
                    #     proxy, etc.)
                    # Exiting here used to convert a single misconfigured
                    # platform into an infinite systemd restart loop.
                    reason = "; ".join(startup_retryable_errors)
                    logger.warning(
                        "Gateway started with no connected platforms — "
                        "%d platform(s) queued for retry: %s",
                        len(self._failed_platforms), reason,
                    )
                    try:
                        from gateway.status import write_runtime_status
                        write_runtime_status(
                            gateway_state="degraded",
                            exit_reason=None,
                        )
                    except Exception:
                        pass
                    # Fall through to the normal "running" state — reconnect
                    # watcher takes it from here.
                # All enabled platforms had no adapter (missing library or credentials).
                # In fleet deployments the same config.yaml is shared across nodes that
                # may only have credentials for a subset of platforms.  Rather than
                # failing hard, degrade gracefully and allow cron jobs to run (#5196).
                logger.warning(
                    "No adapter could be created for any of the %d configured platform(s). "
                    "Check that required dependencies are installed and credentials are set. "
                    "Gateway will continue for cron job execution.",
                    enabled_platform_count,
                )
            else:
                logger.warning("No messaging platforms enabled.")
                logger.info("Gateway will continue running for cron job execution.")
        
        # Update delivery router with adapters
        self.delivery_router.adapters = self.adapters
        self._wire_teams_pipeline_runtime()

        self._running = True
        self._update_runtime_status("running")
        
        # Emit gateway:startup hook
        hook_count = len(self.hooks.loaded_hooks)
        if hook_count:
            logger.info("%s hook(s) loaded", hook_count)
        await self.hooks.emit("gateway:startup", {
            "platforms": [p.value for p in self.adapters.keys()],
        })
        
        if connected_count > 0:
            logger.info("Gateway running with %s platform(s)", connected_count)
        
        # Build initial channel directory for send_message name resolution
        try:
            from gateway.channel_directory import build_channel_directory
            directory = await build_channel_directory(self.adapters)
            ch_count = sum(len(chs) for chs in directory.get("platforms", {}).values())
            logger.info("Channel directory built: %d target(s)", ch_count)
        except Exception as e:
            logger.warning("Channel directory build failed: %s", e)
        
        # Check if we're restarting after a /update command. If the update is
        # still running, keep watching so we notify once it actually finishes.
        notified = await self._send_update_notification()
        if not notified and any(
            path.exists()
            for path in (
                _hermes_home / ".update_pending.json",
                _hermes_home / ".update_pending.claimed.json",
            )
        ):
            self._schedule_update_notification_watch()

        # Give freshly connected platform adapters a brief moment to settle
        # before sending restart/startup lifecycle messages. In practice this
        # helps Discord thread deliveries right after reconnect.
        if connected_count > 0:
            await asyncio.sleep(1.0)

        # Notify the chat that initiated /restart that the gateway is back.
        restart_notification_pending = _restart_notification_pending()
        delivered_restart_target = await self._send_restart_notification()

        # Broadcast a lightweight "gateway is back" message to configured
        # home channels only when this startup is resuming from /restart. If a
        # /restart requester already received a direct completion notice in the
        # same chat, skip the generic broadcast there to avoid duplicates while
        # still allowing a home-channel fallback when the direct send fails.
        if restart_notification_pending or delivered_restart_target is not None:
            skip_home_targets = (
                {delivered_restart_target} if delivered_restart_target else None
            )
            await self._send_home_channel_startup_notifications(
                skip_targets=skip_home_targets,
            )

        # Automatically continue fresh sessions that were interrupted by the
        # previous gateway restart/shutdown.  The resume_pending flag is cleared
        # by the normal successful-turn path, so a failed auto-resume remains
        # visible for manual recovery on the next user message.
        self._schedule_resume_pending_sessions()

        # Drain any recovered process watchers (from crash recovery checkpoint)
        try:
            from tools.process_registry import process_registry
            while process_registry.pending_watchers:
                watcher = process_registry.pending_watchers.pop(0)
                asyncio.create_task(self._run_process_watcher(watcher))
                logger.info("Resumed watcher for recovered process %s", watcher.get("session_id"))
        except Exception as e:
            logger.error("Recovered watcher setup error: %s", e)

        # Start background session expiry watcher to finalize expired sessions
        asyncio.create_task(self._session_expiry_watcher())

        # Start background kanban notifier — delivers `completed`, `blocked`,
        # `spawn_auto_blocked`, and `crashed` events to gateway subscribers
        # so human-in-the-loop workflows hear back without polling.
        asyncio.create_task(self._kanban_notifier_watcher())

        # Start background kanban dispatcher — spawns workers for ready
        # tasks. Gated by `kanban.dispatch_in_gateway` (default True).
        # When false, users run `hermes kanban daemon` externally or
        # simply don't use kanban; this loop becomes a no-op.
        asyncio.create_task(self._kanban_dispatcher_watcher())

        # Start background reconnection watcher for platforms that failed at startup
        if self._failed_platforms:
            logger.info(
                "Starting reconnection watcher for %d failed platform(s): %s",
                len(self._failed_platforms),
                ", ".join(p.value for p in self._failed_platforms),
            )
        asyncio.create_task(self._platform_reconnect_watcher())

        # Start background handoff watcher — picks up CLI sessions marked
        # handoff_state='pending' in state.db and re-binds them to the
        # destination platform's home channel, then forges a synthetic user
        # turn so the agent kicks off the new chat.
        asyncio.create_task(self._handoff_watcher())

        logger.info("Press Ctrl+C to stop")
        
        return True


    async def _process_handoff(self, row: Dict[str, Any]) -> None:
        """Execute one handoff row. Raises on failure (caller marks failed)."""
        from gateway.config import Platform
        from gateway.session import SessionSource, build_session_key
        from gateway.platforms.base import MessageEvent

        cli_session_id = row["id"]
        platform_name = (row.get("handoff_platform") or "").strip().lower()
        if not platform_name:
            raise RuntimeError("handoff_platform is empty")

        # Resolve platform enum
        try:
            platform = Platform(platform_name)
        except (ValueError, KeyError):
            raise RuntimeError(f"unknown platform '{platform_name}'")

        # Adapter must be live
        adapter = self.adapters.get(platform)
        if not adapter:
            raise RuntimeError(
                f"platform '{platform_name}' is not active in this gateway"
            )

        # Home channel must be configured
        home = self.config.get_home_channel(platform)
        if not home or not home.chat_id:
            raise RuntimeError(
                f"no home channel configured for {platform_name}; "
                f"run /sethome on the desired chat first"
            )

        cli_title = row.get("title") or cli_session_id[:8]

        # Try to create a fresh thread on the destination so the handoff
        # has its own scrollback. Adapter returns None if threading isn't
        # supported (Matrix/WhatsApp/Signal/SMS) or if creation failed
        # (no permission, topics-mode off, parent is a DM, etc.). When
        # None we fall through to using the home channel directly — the
        # synthetic turn still lands; just without thread isolation.
        thread_name = f"Hermes — {cli_title}"
        try:
            new_thread_id = await adapter.create_handoff_thread(
                str(home.chat_id), thread_name,
            )
        except Exception as exc:
            logger.debug(
                "Handoff: create_handoff_thread raised on %s: %s",
                platform_name, exc, exc_info=True,
            )
            new_thread_id = None

        # Use the new thread if the adapter created one; otherwise fall
        # back to whatever thread (if any) the home channel was configured
        # with.
        effective_thread_id = new_thread_id or (
            str(home.thread_id) if home.thread_id else None
        )

        # Determine chat_type for the destination source. If we created a
        # thread, key the session_key as a thread (build_session_key sets
        # thread sessions to user-shared by default, which is what we
        # want — the synthetic turn and any later real-user message both
        # land on the same key without needing a user_id).
        if new_thread_id:
            dest_chat_type = "thread"
        else:
            # No thread — assume DM-style for the home channel. For
            # group/channel home channels without thread support
            # (Matrix/WhatsApp/Signal), the platform's own keying makes
            # the synthetic turn shared anyway (single-DM platforms).
            dest_chat_type = "dm"

        dest_source = SessionSource(
            platform=platform,
            chat_id=str(home.chat_id),
            chat_name=home.name,
            chat_type=dest_chat_type,
            user_id="system:handoff",
            user_name="Handoff",
            thread_id=effective_thread_id,
        )

        # Compute the gateway's session_key for that destination using the
        # same rules its adapters use, so switch_session targets the right
        # entry. For thread destinations build_session_key keys without
        # user_id (thread_sessions_per_user defaults to False) — so the
        # next real user message in the thread shares this same session.
        platform_cfg = self.config.platforms.get(platform)
        extra = platform_cfg.extra if platform_cfg else {}
        session_key = build_session_key(
            dest_source,
            group_sessions_per_user=extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=extra.get("thread_sessions_per_user", False),
        )

        # Make sure there's an entry in the session_store for this key. If
        # the home channel has never been used, get_or_create_session
        # creates one; switch_session then re-points it.
        self.session_store.get_or_create_session(dest_source)

        # Re-bind the destination key to the CLI session_id. switch_session
        # ends the prior session in SQLite and reopens the CLI session under
        # the new key. The CLI's transcript becomes the active one for the
        # gateway from this moment on.
        switched = self.session_store.switch_session(session_key, cli_session_id)
        if switched is None:
            raise RuntimeError(
                f"could not switch session key {session_key} → {cli_session_id}"
            )

        # Evict any cached AIAgent for this session_key so the next dispatch
        # rebuilds it against the CLI session_id (mirrors /resume / /branch).
        self._evict_cached_agent(session_key)

        # Cancel any in-flight running-agent state for the destination key
        # so the synthetic turn isn't queued behind a stale running flag.
        self._release_running_agent_state(session_key)

        synthetic_text = (
            f"[Session was just handed off from CLI (\"{cli_title}\") to this "
            f"channel. The full prior conversation history is loaded above. "
            f"Briefly confirm you're working here and summarize what we were "
            f"working on, so the user can continue from this device.]"
        )

        synthetic_event = MessageEvent(
            text=synthetic_text,
            source=dest_source,
            internal=True,
        )

        logger.info(
            "Handoff: dispatching synthetic turn for CLI session %s → %s "
            "(home=%s, thread=%s, session_key=%s)",
            cli_session_id, platform_name, home.chat_id, effective_thread_id,
            session_key,
        )

        # Dispatch through the runner directly. Going through
        # adapter.handle_message would spawn a background task and we'd
        # lose synchronous error visibility; calling _handle_message inline
        # keeps the success/failure path observable for the watcher.
        response_text = await self._handle_message(synthetic_event)
        if not response_text:
            # Streaming may have already delivered the response inline.
            # Either way, agent ran without raising — count as success.
            return

        # Send the agent's reply to the destination. Route to the new
        # thread if we created one; otherwise the configured home channel
        # (which may itself carry a thread_id).
        send_metadata: Dict[str, Any] = {}
        if effective_thread_id:
            send_metadata["thread_id"] = effective_thread_id
        try:
            result = await adapter.send(
                chat_id=str(home.chat_id),
                content=response_text,
                metadata=send_metadata or None,
            )
        except Exception as exc:
            raise RuntimeError(f"adapter.send failed: {exc}") from exc

        if not getattr(result, "success", True):
            err = getattr(result, "error", "send returned success=False")
            raise RuntimeError(f"adapter.send failed: {err}")


    def _active_profile_name(self) -> str:
        """Return the profile name this gateway represents."""
        try:
            from hermes_cli.profiles import get_active_profile_name
            return get_active_profile_name() or "default"
        except Exception:
            return "default"








    async def stop(
        self,
        *,
        restart: bool = False,
        detached_restart: bool = False,
        service_restart: bool = False,
    ) -> None:
        """Stop the gateway and disconnect all adapters."""
        if restart:
            self._restart_requested = True
            self._restart_detached = detached_restart
            self._restart_via_service = service_restart
        if self._stop_task is not None:
            await self._stop_task
            return

        async def _stop_impl() -> None:
            def _kill_tool_subprocesses(phase: str) -> None:
                """Kill tool subprocesses + tear down terminal envs + browsers.

                Called twice in the shutdown path: once eagerly after a
                drain timeout forces agent interrupt (so we reclaim bash/
                sleep children before systemd TimeoutStopSec escalates to
                SIGKILL on the cgroup — #8202), and once as a final
                catch-all at the end of _stop_impl() for the graceful
                path or anything respawned mid-teardown.

                All steps are best-effort; exceptions are swallowed so
                one subsystem's failure doesn't block the rest.
                """
                try:
                    from tools.process_registry import process_registry
                    _killed = process_registry.kill_all()
                    if _killed:
                        logger.info(
                            "Shutdown (%s): killed %d tool subprocess(es)",
                            phase, _killed,
                        )
                except Exception as _e:
                    logger.debug("process_registry.kill_all (%s) error: %s", phase, _e)
                try:
                    from tools.terminal_tool import cleanup_all_environments
                    cleanup_all_environments()
                except Exception as _e:
                    logger.debug("cleanup_all_environments (%s) error: %s", phase, _e)
                try:
                    from tools.browser_tool import cleanup_all_browsers
                    cleanup_all_browsers()
                except Exception as _e:
                    logger.debug("cleanup_all_browsers (%s) error: %s", phase, _e)

            logger.info(
                "Stopping gateway%s...",
                " for restart" if self._restart_requested else "",
            )
            _stop_started_at = time.monotonic()

            def _phase_elapsed() -> float:
                return time.monotonic() - _stop_started_at

            self._running = False
            self._draining = True

            # Notify all chats with active agents BEFORE draining.
            # Adapters are still connected here, so messages can be sent.
            await self._notify_active_sessions_of_shutdown()
            logger.info(
                "Shutdown phase: notify_active_sessions done at +%.2fs",
                _phase_elapsed(),
            )

            timeout = self._restart_drain_timeout

            # Pre-mark sessions as resume_pending BEFORE the drain wait.
            # If the process is killed by the service manager during the
            # drain, the durable marker is already written so the next
            # gateway boot can recover in-flight sessions (#27856).
            _pre_drain_keys: list[str] = []
            for _sk, _agent in list(self._running_agents.items()):
                if _agent is _AGENT_PENDING_SENTINEL:
                    continue
                try:
                    self.session_store.mark_resume_pending(
                        _sk,
                        "restart_timeout" if self._restart_requested else "shutdown_timeout",
                    )
                    _pre_drain_keys.append(_sk)
                except Exception as _e:
                    logger.debug("pre-drain mark_resume_pending failed for %s: %s", _sk, _e)

            _drain_started_at = time.monotonic()
            active_agents, timed_out = await self._drain_active_agents(timeout)
            logger.info(
                "Shutdown phase: drain done at +%.2fs (drain took %.2fs, "
                "timed_out=%s, active_at_start=%d, active_now=%d)",
                _phase_elapsed(),
                time.monotonic() - _drain_started_at,
                timed_out,
                len(active_agents),
                self._running_agent_count(),
            )

            if not timed_out:
                # Drain completed gracefully — all running sessions finished.
                # Clear the pre-drain resume_pending markers so sessions that
                # completed during the drain window don't carry a stale flag.
                for _sk in _pre_drain_keys:
                    if _sk not in self._running_agents:
                        try:
                            self.session_store.clear_resume_pending(_sk)
                        except Exception as _e:
                            logger.debug(
                                "clear_resume_pending after drain failed for %s: %s",
                                _sk, _e,
                            )

            if timed_out:
                logger.warning(
                    "Gateway drain timed out after %.1fs with %d active agent(s); interrupting remaining work.",
                    timeout,
                    self._running_agent_count(),
                )
                # Clear pre-drain marks for sessions that finished gracefully
                # during the drain window — they are no longer in _running_agents
                # and must NOT carry a stale resume_pending flag.  Only sessions
                # still running at timeout should be marked.
                for _sk in _pre_drain_keys:
                    if _sk not in self._running_agents:
                        try:
                            self.session_store.clear_resume_pending(_sk)
                        except Exception as _e:
                            logger.debug(
                                "clear_resume_pending (timeout) failed for %s: %s",
                                _sk, _e,
                            )
                # Mark forcibly-interrupted sessions as resume_pending BEFORE
                # interrupting the agents.  This preserves each session's
                # session_id + transcript so the next message on the same
                # session_key auto-resumes from the existing conversation
                # instead of getting routed through suspend_recently_active()
                # and converted into a fresh session.  Terminal escalation
                # for genuinely stuck sessions still flows through the
                # existing ``.restart_failure_counts`` stuck-loop counter
                # (incremented below, threshold 3), which sets
                # ``suspended=True`` and overrides resume_pending.
                #
                # Iterate self._running_agents (current) rather than the
                # drain-start ``active_agents`` snapshot — the snapshot
                # may include sessions that finished gracefully during
                # the drain window, and marking those falsely would give
                # them a stray restart-interruption system note on their
                # next turn even though their previous turn completed
                # cleanly.  Skip pending sentinels for the same reason
                # _interrupt_running_agents() does: their agent hasn't
                # started yet, there's nothing to interrupt, and the
                # session shouldn't carry a misleading resume flag.
                _resume_reason = (
                    "restart_timeout" if self._restart_requested else "shutdown_timeout"
                )
                for _sk, _agent in list(self._running_agents.items()):
                    if _agent is _AGENT_PENDING_SENTINEL:
                        continue
                    try:
                        self.session_store.mark_resume_pending(_sk, _resume_reason)
                    except Exception as _e:
                        logger.debug(
                            "mark_resume_pending failed for %s: %s",
                            _sk, _e,
                        )
                self._interrupt_running_agents(
                    _INTERRUPT_REASON_GATEWAY_RESTART if self._restart_requested else _INTERRUPT_REASON_GATEWAY_SHUTDOWN
                )
                interrupt_deadline = asyncio.get_running_loop().time() + 5.0
                while self._running_agents and asyncio.get_running_loop().time() < interrupt_deadline:
                    self._update_runtime_status("draining")
                    await asyncio.sleep(0.1)

                # Kill lingering tool subprocesses NOW, before we spend more
                # budget on adapter disconnect / session DB close.  Under
                # systemd (TimeoutStopSec bounded by drain_timeout+headroom),
                # deferring this to the end of stop() risks systemd escalating
                # to SIGKILL on the cgroup first — at which point bash/sleep
                # children left behind by an interrupted terminal tool get
                # killed by systemd instead of us (issue #8202).  The final
                # catch-all cleanup below still runs for the graceful path.
                _kill_tool_subprocesses("post-interrupt")
                logger.info(
                    "Shutdown phase: post-interrupt tool kill done at +%.2fs",
                    _phase_elapsed(),
                )

            if self._restart_requested and self._restart_detached:
                try:
                    await self._launch_detached_restart_command()
                except Exception as e:
                    logger.error("Failed to launch detached gateway restart: %s", e)

            self._finalize_shutdown_agents(active_agents)

            # Also shut down memory providers on idle cached agents.
            # _finalize_shutdown_agents only handles agents that were
            # mid-turn at drain time; the _agent_cache may still hold
            # idle agents whose MemoryProviders never received
            # on_session_end().
            _cache_lock = getattr(self, "_agent_cache_lock", None)
            _cache = getattr(self, "_agent_cache", None)
            if _cache_lock is not None and _cache is not None:
                with _cache_lock:
                    _idle_agents = list(_cache.values())
                    _cache.clear()
                for _entry in _idle_agents:
                    _agent = (
                        _entry[0] if isinstance(_entry, tuple) else _entry
                    )
                    self._cleanup_agent_resources(_agent)

            for platform, adapter in list(self.adapters.items()):
                _adapter_started_at = time.monotonic()
                try:
                    await adapter.cancel_background_tasks()
                except Exception as e:
                    logger.debug("✗ %s background-task cancel error: %s", platform.value, e)
                try:
                    await adapter.disconnect()
                    logger.info(
                        "✓ %s disconnected (%.2fs)",
                        platform.value,
                        time.monotonic() - _adapter_started_at,
                    )
                except Exception as e:
                    logger.error(
                        "✗ %s disconnect error after %.2fs: %s",
                        platform.value,
                        time.monotonic() - _adapter_started_at,
                        e,
                    )
            logger.info(
                "Shutdown phase: all adapters disconnected at +%.2fs",
                _phase_elapsed(),
            )

            for _task in list(self._background_tasks):
                if _task is self._stop_task:
                    continue
                _task.cancel()
            self._background_tasks.clear()

            self.adapters.clear()
            self._running_agents.clear()
            self._running_agents_ts.clear()
            self._pending_messages.clear()
            self._pending_approvals.clear()
            if hasattr(self, '_busy_ack_ts'):
                self._busy_ack_ts.clear()
            self._shutdown_event.set()

            # Global cleanup: kill any remaining tool subprocesses not tied
            # to a specific agent (catch-all for zombie prevention). On the
            # drain-timeout path we already did this earlier after agent
            # interrupt — this second call catches (a) the graceful path
            # where drain succeeded without interrupt, and (b) anything
            # that got respawned between the earlier call and adapter
            # disconnect (defense in depth; safe to call repeatedly).
            _kill_tool_subprocesses("final-cleanup")
            logger.info(
                "Shutdown phase: final-cleanup tool kill done at +%.2fs",
                _phase_elapsed(),
            )

            # Reap the process-global auxiliary-client cache once at the very
            # end of teardown.  Per-turn cleanup runs in _cleanup_agent_resources
            # for each active agent, but clients bound to worker-thread loops
            # that died with their ThreadPoolExecutor (notably cron ticks) only
            # get swept here.  Without this, long-running gateways accumulate
            # async httpx transports until they hit EMFILE on macOS's default
            # RLIMIT_NOFILE=256.  See #14210.
            try:
                from agent.auxiliary_client import shutdown_cached_clients
                shutdown_cached_clients()
            except Exception as _e:
                logger.debug("shutdown_cached_clients error: %s", _e)

            # Close SQLite session DBs so the WAL write lock is released.
            # Without this, --replace and similar restart flows leave the
            # old gateway's connection holding the WAL lock until Python
            # actually exits — causing 'database is locked' errors when
            # the new gateway tries to open the same file.
            for _db_holder in (self, getattr(self, "session_store", None)):
                _db = getattr(_db_holder, "_db", None) if _db_holder else None
                if _db is None or not hasattr(_db, "close"):
                    continue
                try:
                    _db.close()
                except Exception as _e:
                    logger.debug("SessionDB close error: %s", _e)
            logger.info(
                "Shutdown phase: SessionDB close done at +%.2fs",
                _phase_elapsed(),
            )

            from gateway.status import remove_pid_file, release_gateway_runtime_lock
            remove_pid_file()
            release_gateway_runtime_lock()

            # Write a clean-shutdown marker so the next startup knows this
            # wasn't a crash.  suspend_recently_active() only needs to run
            # after unexpected exits.  However, if the drain timed out and
            # agents were force-interrupted, their sessions may be in an
            # incomplete state (trailing tool response, no final assistant
            # message).  Skip the marker in that case so the next startup
            # suspends those sessions — giving users a clean slate instead
            # of resuming a half-finished tool loop.
            if not timed_out:
                try:
                    (_hermes_home / ".clean_shutdown").touch()
                except Exception:
                    pass
            else:
                logger.info(
                    "Skipping .clean_shutdown marker — drain timed out with "
                    "interrupted agents; next startup will suspend recently "
                    "active sessions."
                )

            # Track sessions that were active at shutdown for stuck-loop
            # detection (#7536).  On each restart, the counter increments
            # for sessions that were running.  If a session hits the
            # threshold (3 consecutive restarts while active), the next
            # startup auto-suspends it — breaking the loop.
            if active_agents:
                self._increment_restart_failure_counts(set(active_agents.keys()))

            if self._restart_requested and self._restart_via_service:
                self._exit_code = GATEWAY_SERVICE_RESTART_EXIT_CODE
                self._exit_reason = self._exit_reason or "Gateway restart requested"

            self._draining = False
            self._update_runtime_status("stopped", self._exit_reason)
            logger.info("Gateway stopped (total teardown %.2fs)", _phase_elapsed())

        self._stop_task = asyncio.create_task(_stop_impl())
        await self._stop_task

    async def wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        await self._shutdown_event.wait()

    def _create_adapter(
        self, 
        platform: Platform, 
        config: Any
    ) -> Optional[BasePlatformAdapter]:
        """Create the appropriate adapter for a platform.

        Checks the platform_registry first (plugin adapters), then falls
        through to the built-in if/elif chain for core platforms.
        """
        if hasattr(config, "extra") and isinstance(config.extra, dict):
            config.extra.setdefault(
                "group_sessions_per_user",
                self.config.group_sessions_per_user,
            )
            config.extra.setdefault(
                "thread_sessions_per_user",
                getattr(self.config, "thread_sessions_per_user", False),
            )

        # ── Plugin-registered platforms (checked first) ───────────────────
        try:
            from gateway.platform_registry import platform_registry
            if platform_registry.is_registered(platform.value):
                adapter = platform_registry.create_adapter(platform.value, config)
                if adapter is not None:
                    return adapter
                # Registered but failed to instantiate — don't silently fall
                # through to built-ins (there are none for plugin platforms).
                logger.error(
                    "Platform '%s' is registered but adapter creation failed "
                    "(check dependencies and config)",
                    platform.value,
                )
                return None
        except Exception as e:
            logger.debug("Platform registry lookup for '%s' failed: %s", platform.value, e)
        # Fall through to built-in adapters below

        if platform == Platform.TELEGRAM:
            from gateway.platforms.telegram import TelegramAdapter, check_telegram_requirements
            if not check_telegram_requirements():
                logger.warning("Telegram: python-telegram-bot not installed")
                return None
            adapter = TelegramAdapter(config)
            # Apply Telegram notification mode from config.  Controls whether
            # intermediate messages (tool progress, streaming, status) trigger
            # push notifications.  Supports ENV override for quick testing.
            _notify_mode = os.getenv("HERMES_TELEGRAM_NOTIFICATIONS", "")
            if not _notify_mode:
                try:
                    _gw_cfg = _load_gateway_config()
                    _raw = cfg_get(_gw_cfg, "display", "platforms", "telegram", "notifications")
                    if _raw not in {None, ""}:
                        _notify_mode = str(_raw).strip().lower()
                except Exception:
                    pass
            _notify_mode = _notify_mode or "important"
            if _notify_mode not in {"all", "important"}:
                logger.warning(
                    "Unknown telegram notifications mode '%s', "
                    "defaulting to 'important' (valid: all, important)",
                    _notify_mode,
                )
                _notify_mode = "important"
            adapter._notifications_mode = _notify_mode
            return adapter
        
        elif platform == Platform.DISCORD:
            from gateway.platforms.discord import DiscordAdapter, check_discord_requirements
            if not check_discord_requirements():
                logger.warning("Discord: discord.py not installed")
                return None
            adapter = DiscordAdapter(config)
            adapter.gateway_runner = self  # For cross-platform admin alerts on unauthorized slash
            return adapter
        
        elif platform == Platform.WHATSAPP:
            from gateway.platforms.whatsapp import WhatsAppAdapter, check_whatsapp_requirements
            if not check_whatsapp_requirements():
                logger.warning("WhatsApp: Node.js not installed or bridge not configured")
                return None
            return WhatsAppAdapter(config)
        
        elif platform == Platform.SLACK:
            from gateway.platforms.slack import SlackAdapter, check_slack_requirements
            if not check_slack_requirements():
                logger.warning("Slack: slack-bolt not installed. Run: pip install 'hermes-agent[slack]'")
                return None
            return SlackAdapter(config)

        elif platform == Platform.SIGNAL:
            from gateway.platforms.signal import SignalAdapter, check_signal_requirements
            if not check_signal_requirements():
                logger.warning("Signal: SIGNAL_HTTP_URL or SIGNAL_ACCOUNT not configured")
                return None
            return SignalAdapter(config)

        elif platform == Platform.HOMEASSISTANT:
            from gateway.platforms.homeassistant import HomeAssistantAdapter, check_ha_requirements
            if not check_ha_requirements():
                logger.warning("HomeAssistant: aiohttp not installed or HASS_TOKEN not set")
                return None
            return HomeAssistantAdapter(config)

        elif platform == Platform.EMAIL:
            from gateway.platforms.email import EmailAdapter, check_email_requirements
            if not check_email_requirements():
                logger.warning("Email: EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_IMAP_HOST, or EMAIL_SMTP_HOST not set")
                return None
            return EmailAdapter(config)

        elif platform == Platform.SMS:
            from gateway.platforms.sms import SmsAdapter, check_sms_requirements
            if not check_sms_requirements():
                logger.warning("SMS: aiohttp not installed or TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN not set")
                return None
            return SmsAdapter(config)

        elif platform == Platform.DINGTALK:
            from gateway.platforms.dingtalk import DingTalkAdapter, check_dingtalk_requirements
            if not check_dingtalk_requirements():
                logger.warning("DingTalk: dingtalk-stream not installed or DINGTALK_CLIENT_ID/SECRET not set")
                return None
            return DingTalkAdapter(config)

        elif platform == Platform.FEISHU:
            from gateway.platforms.feishu import FeishuAdapter, check_feishu_requirements
            if not check_feishu_requirements():
                logger.warning("Feishu: lark-oapi not installed or FEISHU_APP_ID/SECRET not set")
                return None
            return FeishuAdapter(config)

        elif platform == Platform.WECOM_CALLBACK:
            from gateway.platforms.wecom_callback import (
                WecomCallbackAdapter,
                check_wecom_callback_requirements,
            )
            if not check_wecom_callback_requirements():
                logger.warning("WeComCallback: aiohttp/httpx not installed")
                return None
            return WecomCallbackAdapter(config)

        elif platform == Platform.WECOM:
            from gateway.platforms.wecom import WeComAdapter, check_wecom_requirements
            if not check_wecom_requirements():
                logger.warning("WeCom: aiohttp not installed or WECOM_BOT_ID/SECRET not set")
                return None
            return WeComAdapter(config)

        elif platform == Platform.WEIXIN:
            from gateway.platforms.weixin import WeixinAdapter, check_weixin_requirements
            if not check_weixin_requirements():
                logger.warning("Weixin: aiohttp/cryptography not installed")
                return None
            return WeixinAdapter(config)

        elif platform == Platform.MATTERMOST:
            from gateway.platforms.mattermost import MattermostAdapter, check_mattermost_requirements
            if not check_mattermost_requirements():
                logger.warning("Mattermost: MATTERMOST_TOKEN or MATTERMOST_URL not set, or aiohttp missing")
                return None
            return MattermostAdapter(config)

        elif platform == Platform.MATRIX:
            from gateway.platforms.matrix import MatrixAdapter, check_matrix_requirements
            if not check_matrix_requirements():
                logger.warning("Matrix: mautrix not installed or credentials not set. Run: pip install 'mautrix[encryption]'")
                return None
            return MatrixAdapter(config)

        elif platform == Platform.API_SERVER:
            from gateway.platforms.api_server import APIServerAdapter, check_api_server_requirements
            if not check_api_server_requirements():
                logger.warning("API Server: aiohttp not installed")
                return None
            return APIServerAdapter(config)

        elif platform == Platform.WEBHOOK:
            from gateway.platforms.webhook import WebhookAdapter, check_webhook_requirements
            if not check_webhook_requirements():
                logger.warning("Webhook: aiohttp not installed")
                return None
            adapter = WebhookAdapter(config)
            adapter.gateway_runner = self  # For cross-platform delivery
            return adapter

        elif platform == Platform.MSGRAPH_WEBHOOK:
            from gateway.platforms.msgraph_webhook import (
                MSGraphWebhookAdapter,
                check_msgraph_webhook_requirements,
            )
            if not check_msgraph_webhook_requirements():
                logger.warning("MSGraph webhook: aiohttp not installed")
                return None
            return MSGraphWebhookAdapter(config)

        elif platform == Platform.BLUEBUBBLES:
            from gateway.platforms.bluebubbles import BlueBubblesAdapter, check_bluebubbles_requirements
            if not check_bluebubbles_requirements():
                logger.warning("BlueBubbles: aiohttp/httpx missing or BLUEBUBBLES_SERVER_URL/BLUEBUBBLES_PASSWORD not configured")
                return None
            return BlueBubblesAdapter(config)

        elif platform == Platform.QQBOT:
            from gateway.platforms.qqbot import QQAdapter, check_qq_requirements
            if not check_qq_requirements():
                logger.warning("QQBot: aiohttp/httpx missing or QQ_APP_ID/QQ_CLIENT_SECRET not configured")
                return None
            return QQAdapter(config)

        elif platform == Platform.YUANBAO:
            from gateway.platforms.yuanbao import YuanbaoAdapter, WEBSOCKETS_AVAILABLE
            if not WEBSOCKETS_AVAILABLE:
                logger.warning("Yuanbao: websockets not installed. Run: pip install websockets")
                return None
            return YuanbaoAdapter(config)

        elif platform == Platform.LINE:
            from gateway.platforms.line import LineAdapter, check_line_requirements
            if not check_line_requirements():
                logger.warning("LINE: aiohttp missing or LINE_CHANNEL_ACCESS_TOKEN/LINE_CHANNEL_SECRET not configured")
                return None
            return LineAdapter(config)

        elif platform == Platform.IRC:
            from gateway.platforms.irc import IrcAdapter, check_irc_requirements
            if not check_irc_requirements():
                logger.warning("IRC: IRC_NICK not configured")
                return None
            return IrcAdapter(config)

        elif platform == Platform.TWITCH:
            # Twitch uses IRC protocol with IRC_SERVER=twitch
            from gateway.platforms.irc import IrcAdapter, check_irc_requirements
            if not check_irc_requirements():
                logger.warning("Twitch: IRC_NICK not configured")
                return None
            adapter = IrcAdapter(config)
            adapter._is_twitch = True
            adapter._server_host = "chat.twitch.tv"
            adapter._server_port = 6697
            return adapter

        elif platform == Platform.ZALO:
            from gateway.platforms.zalo import ZaloAdapter, check_zalo_requirements
            if not check_zalo_requirements():
                logger.warning("Zalo: aiohttp missing or ZALO_ACCESS_TOKEN/ZALO_SECRET_KEY not configured")
                return None
            return ZaloAdapter(config)

        elif platform == Platform.NOSTR:
            from gateway.platforms.nostr import NostrAdapter, check_nostr_requirements
            if not check_nostr_requirements():
                logger.warning("Nostr: websockets missing or NOSTR_PRIVATE_KEY not configured")
                return None
            return NostrAdapter(config)

        elif platform == Platform.SYNOLOGY_CHAT:
            from gateway.platforms.synology_chat import SynologyChatAdapter, check_synology_chat_requirements
            if not check_synology_chat_requirements():
                logger.warning("Synology Chat: aiohttp missing or SYNOLOGY_CHAT_INCOMING_URL not configured")
                return None
            return SynologyChatAdapter(config)

        return None
    # P3: _deliver_platform_notice, _handle_message,
    # _prepare_inbound_message_text, _consume_pending_native_image_paths,
    # _handle_message_with_agent — extracted to gateway.message_handler_mixin





    def _check_slash_access(
        self, source: SessionSource, canonical_cmd: str
    ) -> Optional[str]:
        """Return a denial message if ``source`` cannot run ``canonical_cmd``,
        else None. Used by both the cold and running-agent dispatch paths
        in ``_handle_message`` so admin/user gating can't be bypassed by
        an in-flight agent.

        Backward-compat semantics live in
        :func:`gateway.slash_access.policy_for_source` — when the operator
        hasn't set ``allow_admin_from`` for the scope, the policy returns
        ``enabled=False`` and this method always returns None.
        """
        from gateway.slash_access import policy_for_source as _policy_for_source

        if not canonical_cmd:
            return None
        policy = _policy_for_source(self.config, source)
        if not policy.enabled or policy.can_run(source.user_id, canonical_cmd):
            return None
        logger.info(
            "Slash command /%s denied for %s:%s (not admin, not in user_allowed_commands)",
            canonical_cmd,
            source.platform.value if source.platform else "?",
            source.user_id,
        )
        allowed_preview = sorted(policy.user_allowed_commands)
        if allowed_preview:
            suffix = (
                "You can run: "
                + ", ".join(f"/{c}" for c in allowed_preview[:12])
                + ("…" if len(allowed_preview) > 12 else "")
                + ". Use /whoami for the full list."
            )
        else:
            suffix = (
                "No slash commands are enabled for non-admins on this "
                "platform. Ask an admin to add you to allow_admin_from "
                "or to set user_allowed_commands."
            )
        return f"⛔ /{canonical_cmd} is admin-only here. {suffix}"










    def _is_stale_restart_redelivery(self, event: MessageEvent) -> bool:
        """Return True if this /restart is a Telegram re-delivery we already handled.

        The previous gateway wrote ``.restart_last_processed.json`` with the
        triggering platform + update_id when it processed the /restart.  If
        we now see a /restart on the same platform with an update_id <= that
        recorded value AND the marker is recent (< 5 minutes), it's a
        redelivery and should be ignored.

        Only applies to Telegram today (the only platform that exposes a
        numeric cross-session update ordering); other platforms return False.
        """
        if event is None or event.source is None:
            return False
        if event.platform_update_id is None:
            return False
        if event.source.platform is None:
            return False
        # Only Telegram populates platform_update_id currently; be explicit
        # so future platforms aren't accidentally gated by this check.
        try:
            platform_value = event.source.platform.value
        except Exception:
            return False
        if platform_value != "telegram":
            return False

        try:
            marker_path = _hermes_home / ".restart_last_processed.json"
            if not marker_path.exists():
                return False
            data = json.loads(marker_path.read_text())
        except Exception:
            return False

        if data.get("platform") != platform_value:
            return False
        recorded_uid = data.get("update_id")
        if not isinstance(recorded_uid, int):
            return False
        # Staleness guard: ignore markers older than 5 minutes.  A legitimately
        # old marker (e.g. crash recovery where notify never fired) should not
        # swallow a fresh /restart from the user.
        requested_at = data.get("requested_at")
        if isinstance(requested_at, (int, float)):
            if time.time() - requested_at > 300:
                return False
        return event.platform_update_id <= recorded_uid








    # ────────────────────────────────────────────────────────────────
    # /goal — persistent cross-turn goals (Ralph-style loop)
    # ────────────────────────────────────────────────────────────────









    @staticmethod
    def _get_guild_id(event: MessageEvent) -> Optional[int]:
        """Extract Discord guild_id from the raw message object."""
        raw = getattr(event, "raw_message", None)
        if raw is None:
            return None
        # Slash command interaction
        if hasattr(raw, "guild_id") and raw.guild_id:
            return int(raw.guild_id)
        # Regular message
        if hasattr(raw, "guild") and raw.guild:
            return raw.guild.id
        return None









    async def _deliver_media_from_response(
        self,
        response: str,
        event: MessageEvent,
        adapter,
    ) -> None:
        """Extract MEDIA: tags and local file paths from a response and deliver them.

        Called after streaming has already sent the text to the user, so the
        text itself is already delivered — this only handles file attachments
        that the normal _process_message_background path would have caught.
        """
        from pathlib import Path
        from urllib.parse import quote as _quote

        try:
            # Capture [[as_document]] before extract_media strips it, so the
            # dispatch partition below can route image-extension files
            # through send_document (preserving bytes) instead of
            # send_multiple_images (Telegram sendPhoto recompresses to ~1280px).
            force_document_attachments = "[[as_document]]" in response

            media_files, _ = adapter.extract_media(response)
            _, cleaned = adapter.extract_images(response)
            local_files, _ = adapter.extract_local_files(cleaned)

            _thread_meta = self._thread_metadata_for_source(event.source, self._reply_anchor_for_event(event))

            from gateway.platforms.base import should_send_media_as_audio

            _VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'}
            _IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

            # Partition out images so they can be sent as a single batch
            # (e.g. Signal's multi-attachment RPC). When [[as_document]] was
            # set, image-extension files skip the photo path and route to
            # send_document below — preserving original bytes.
            image_paths: list = []
            non_image_media: list = []
            for media_path, is_voice in media_files:
                ext = Path(media_path).suffix.lower()
                if (ext in _IMAGE_EXTS
                        and not is_voice
                        and not force_document_attachments):
                    image_paths.append(media_path)
                else:
                    non_image_media.append((media_path, is_voice))

            non_image_local: list = []
            for file_path in local_files:
                if (Path(file_path).suffix.lower() in _IMAGE_EXTS
                        and not force_document_attachments):
                    image_paths.append(file_path)
                else:
                    non_image_local.append(file_path)

            if image_paths:
                try:
                    images = [(f"file://{_quote(p)}", "") for p in image_paths]
                    await adapter.send_multiple_images(
                        chat_id=event.source.chat_id,
                        images=images,
                        metadata=_thread_meta,
                    )
                except Exception as e:
                    logger.warning("[%s] Post-stream image batch delivery failed: %s", adapter.name, e)

            for media_path, is_voice in non_image_media:
                try:
                    ext = Path(media_path).suffix.lower()
                    if should_send_media_as_audio(event.source.platform, ext, is_voice=is_voice):
                        await adapter.send_voice(
                            chat_id=event.source.chat_id,
                            audio_path=media_path,
                            metadata=_thread_meta,
                        )
                    elif ext in _VIDEO_EXTS:
                        await adapter.send_video(
                            chat_id=event.source.chat_id,
                            video_path=media_path,
                            metadata=_thread_meta,
                        )
                    else:
                        await adapter.send_document(
                            chat_id=event.source.chat_id,
                            file_path=media_path,
                            metadata=_thread_meta,
                        )
                except Exception as e:
                    logger.warning("[%s] Post-stream media delivery failed: %s", adapter.name, e)

            for file_path in non_image_local:
                try:
                    ext = Path(file_path).suffix.lower()
                    if ext in _VIDEO_EXTS:
                        await adapter.send_video(
                            chat_id=event.source.chat_id,
                            video_path=file_path,
                            metadata=_thread_meta,
                        )
                    else:
                        await adapter.send_document(
                            chat_id=event.source.chat_id,
                            file_path=file_path,
                            metadata=_thread_meta,
                        )
                except Exception as e:
                    logger.warning("[%s] Post-stream file delivery failed: %s", adapter.name, e)

        except Exception as e:
            logger.warning("Post-stream media extraction failed: %s", e)



    async def _run_background_task(
        self,
        prompt: str,
        source: "SessionSource",
        task_id: str,
        event_message_id: Optional[str] = None,
        media_urls: Optional[List[str]] = None,
        media_types: Optional[List[str]] = None,
    ) -> None:
        """Execute a background agent task and deliver the result to the chat."""
        from run_agent import AIAgent

        media_urls = media_urls or []
        media_types = media_types or []

        adapter = self.adapters.get(source.platform)
        if not adapter:
            logger.warning("No adapter for platform %s in background task %s", source.platform, task_id)
            return

        _thread_metadata = self._thread_metadata_for_source(source, event_message_id)

        try:
            user_config = _load_gateway_config()
            model, runtime_kwargs = self._resolve_session_agent_runtime(
                source=source,
                user_config=user_config,
            )
            if not runtime_kwargs.get("api_key"):
                await adapter.send(
                    source.chat_id,
                    f"❌ Background task {task_id} failed: no provider credentials configured.",
                    metadata=_thread_metadata,
                )
                return

            platform_key = _platform_config_key(source.platform)

            from hermes_cli.tools_config import _get_platform_tools
            enabled_toolsets = sorted(_get_platform_tools(user_config, platform_key))
            agent_cfg = user_config.get("agent") or {}
            disabled_toolsets = agent_cfg.get("disabled_toolsets") or None

            pr = self._provider_routing
            max_iterations = int(os.getenv("HERMES_MAX_ITERATIONS", "90"))
            reasoning_config = self._resolve_session_reasoning_config(source=source)
            self._reasoning_config = reasoning_config
            self._service_tier = self._load_service_tier()
            turn_route = self._resolve_turn_agent_config(prompt, model, runtime_kwargs)

            # Enrich the prompt with image descriptions so the background
            # agent can see user-attached images (same as the main flow).
            enriched_prompt = prompt
            if media_urls:
                image_paths = []
                for i, path in enumerate(media_urls):
                    mtype = media_types[i] if i < len(media_types) else ""
                    if mtype.startswith("image/"):
                        image_paths.append(path)
                if image_paths:
                    try:
                        enriched_prompt = await self._enrich_message_with_vision(
                            prompt, image_paths,
                        )
                    except Exception as e:
                        logger.warning("Background task vision enrichment failed: %s", e)

            def run_sync():
                agent = AIAgent(
                    model=turn_route["model"],
                    **turn_route["runtime"],
                    max_iterations=max_iterations,
                    quiet_mode=True,
                    verbose_logging=False,
                    enabled_toolsets=enabled_toolsets,
                    disabled_toolsets=disabled_toolsets,
                    reasoning_config=reasoning_config,
                    service_tier=self._service_tier,
                    request_overrides=turn_route.get("request_overrides"),
                    providers_allowed=pr.get("only"),
                    providers_ignored=pr.get("ignore"),
                    providers_order=pr.get("order"),
                    provider_sort=pr.get("sort"),
                    provider_require_parameters=pr.get("require_parameters", False),
                    provider_data_collection=pr.get("data_collection"),
                    session_id=task_id,
                    platform=platform_key,
                    user_id=source.user_id,
                    user_name=source.user_name,
                    chat_id=source.chat_id,
                    chat_name=source.chat_name,
                    chat_type=source.chat_type,
                    thread_id=source.thread_id,
                    session_db=self._session_db,
                    fallback_model=self._fallback_model,
                )
                try:
                    return agent.run_conversation(
                        user_message=enriched_prompt,
                        task_id=task_id,
                    )
                finally:
                    self._cleanup_agent_resources(agent)

            result = await self._run_in_executor_with_context(run_sync)

            response = result.get("final_response", "") if result else ""
            if not response and result and result.get("error"):
                response = f"Error: {result['error']}"

            # Extract media files from the response
            if response:
                media_files, response = adapter.extract_media(response)
                images, text_content = adapter.extract_images(response)

                preview = prompt[:60] + ("..." if len(prompt) > 60 else "")
                header = f'✅ Background task complete\nPrompt: "{preview}"\n\n'

                if text_content:
                    await adapter.send(
                        chat_id=source.chat_id,
                        content=header + text_content,
                        metadata=_thread_metadata,
                    )
                elif not images and not media_files:
                    await adapter.send(
                        chat_id=source.chat_id,
                        content=header + "(No response generated)",
                        metadata=_thread_metadata,
                    )

                # Send extracted images
                for image_url, alt_text in (images or []):
                    try:
                        await adapter.send_image(
                            chat_id=source.chat_id,
                            image_url=image_url,
                            caption=alt_text,
                            metadata=_thread_metadata,
                        )
                    except Exception:
                        pass

                # Send media files
                for media_path, _is_voice in (media_files or []):
                    try:
                        await adapter.send_document(
                            chat_id=source.chat_id,
                            file_path=media_path,
                            metadata=_thread_metadata,
                        )
                    except Exception:
                        pass
            else:
                preview = prompt[:60] + ("..." if len(prompt) > 60 else "")
                await adapter.send(
                    chat_id=source.chat_id,
                    content=f'✅ Background task complete\nPrompt: "{preview}"\n\n(No response generated)',
                    metadata=_thread_metadata,
                )

        except Exception as e:
            logger.exception("Background task %s failed", task_id)
            try:
                await adapter.send(
                    chat_id=source.chat_id,
                    content=f"❌ Background task {task_id} failed: {e}",
                    metadata=_thread_metadata,
                )
            except Exception:
                pass














    _TELEGRAM_CAPABILITY_HINT_COOLDOWN_S = 300.0













    async def _execute_mcp_reload(self, event: MessageEvent) -> str:
        """Actually disconnect, reconnect, and notify MCP tool changes.

        Split out from ``_handle_reload_mcp_command`` so the confirmation
        wrapper can invoke the same path whether the user confirmed via
        button, text reply, or has the confirm gate disabled.
        """
        loop = asyncio.get_running_loop()
        try:
            from tools.mcp_tool import shutdown_mcp_servers, discover_mcp_tools, _servers, _lock

            # Capture old server names before shutdown
            with _lock:
                old_servers = set(_servers.keys())

            # Read new config before shutting down, so we know what will be added/removed
            # Shutdown existing connections
            await loop.run_in_executor(None, shutdown_mcp_servers)

            # Reconnect by discovering tools (reads config.yaml fresh)
            new_tools = await loop.run_in_executor(None, discover_mcp_tools)

            # Compute what changed
            with _lock:
                connected_servers = set(_servers.keys())

            added = connected_servers - old_servers
            removed = old_servers - connected_servers
            reconnected = connected_servers & old_servers

            lines = [t("gateway.reload_mcp.header")]
            if reconnected:
                lines.append(t("gateway.reload_mcp.reconnected", names=", ".join(sorted(reconnected))))
            if added:
                lines.append(t("gateway.reload_mcp.added", names=", ".join(sorted(added))))
            if removed:
                lines.append(t("gateway.reload_mcp.removed", names=", ".join(sorted(removed))))
            if not connected_servers:
                lines.append(t("gateway.reload_mcp.none_connected"))
            else:
                lines.append(t("gateway.reload_mcp.tools_available", tools=len(new_tools), servers=len(connected_servers)))

            # Inject a message at the END of the session history so the
            # model knows tools changed on its next turn.  Appended after
            # all existing messages to preserve prompt-cache for the prefix.
            change_parts = []
            if added:
                change_parts.append(f"Added servers: {', '.join(sorted(added))}")
            if removed:
                change_parts.append(f"Removed servers: {', '.join(sorted(removed))}")
            if reconnected:
                change_parts.append(f"Reconnected servers: {', '.join(sorted(reconnected))}")
            tool_summary = f"{len(new_tools)} MCP tool(s) now available" if new_tools else "No MCP tools available"
            change_detail = ". ".join(change_parts) + ". " if change_parts else ""
            reload_msg = {
                "role": "user",
                "content": f"[IMPORTANT: MCP servers have been reloaded. {change_detail}{tool_summary}. The tool list for this conversation has been updated accordingly.]",
            }
            try:
                session_entry = self.session_store.get_or_create_session(event.source)
                self.session_store.append_to_transcript(
                    session_entry.session_id, reload_msg
                )
            except Exception:
                pass  # Best-effort; don't fail the reload over a transcript write

            return "\n".join(lines)

        except Exception as e:
            logger.warning("MCP reload failed: %s", e)
            return t("gateway.reload_mcp.failed", error=e)



    # ------------------------------------------------------------------
    # Slash-command confirmation primitive (generic)
    # ------------------------------------------------------------------
    # Used by slash commands that have a non-destructive but expensive
    # side effect worth an explicit user confirmation (currently only
    # /reload-mcp, which invalidates the prompt cache).  Two delivery
    # paths:
    #   1. Button UI — adapters that override ``send_slash_confirm``
    #      (Telegram, Discord, Slack, Matrix, Feishu) render three
    #      inline buttons.  The adapter routes the button click back via
    #      ``tools.slash_confirm.resolve(session_key, confirm_id, choice)``.
    #   2. Text fallback — adapters that don't override the hook get a
    #      plain text prompt.  Users reply with /approve, /always, or
    #      /cancel; the early intercept in ``_handle_message`` matches
    #      those replies against ``tools.slash_confirm.get_pending()``.

    async def _maybe_confirm_destructive_slash(
        self,
        *,
        event: MessageEvent,
        command: str,
        title: str,
        detail: str,
        execute,
    ) -> Union[str, "EphemeralReply", None]:
        """Gate a destructive session slash command (/new, /reset, /undo).

        ``execute`` is an async callable ``execute() -> str | EphemeralReply``
        that performs the destructive action.  If the
        ``approvals.destructive_slash_confirm`` config gate is off, ``execute``
        runs immediately (returning its result).  Otherwise this routes
        through ``_request_slash_confirm`` — native yes/no buttons on
        Telegram/Discord/Slack, text fallback elsewhere.

        Three-option resolution:

          - ``once``  — run ``execute`` and return its result
          - ``always`` — persist ``approvals.destructive_slash_confirm: false``,
                        then run ``execute``
          - ``cancel`` — return a "cancelled" message; do not run ``execute``
        """
        # Gate check.
        confirm_required = True
        try:
            cfg = self._read_user_config()
            approvals = cfg.get("approvals") if isinstance(cfg, dict) else None
            if isinstance(approvals, dict):
                confirm_required = bool(approvals.get("destructive_slash_confirm", True))
        except Exception:
            pass

        if not confirm_required:
            return await execute()

        session_key = self._session_key_for_source(event.source)

        async def _on_confirm(choice: str):
            if choice == "cancel":
                return f"🟡 /{command} cancelled. Conversation unchanged."
            if choice == "always":
                try:
                    from cli import save_config_value
                    save_config_value("approvals.destructive_slash_confirm", False)
                    logger.info(
                        "User opted out of destructive slash confirm (session=%s)",
                        session_key,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to persist destructive_slash_confirm=false: %s", exc,
                    )
            result = await execute()
            if choice == "always":
                note = (
                    "\n\nℹ️ Future /clear, /new, /reset, and /undo will run "
                    "without confirmation. Re-enable via "
                    "`approvals.destructive_slash_confirm: true` in config.yaml."
                )
                if isinstance(result, str):
                    return result + note
                # EphemeralReply or other — leave untouched; the opt-out note
                # would otherwise mangle structured replies.  The persist itself
                # already happened above; user gets the same UX next time.
                return result
            return result

        prompt_message = (
            f"⚠️ **Confirm /{command}**\n\n"
            f"{detail}\n\n"
            "Choose:\n"
            "• **Approve Once** — proceed this time only\n"
            "• **Always Approve** — proceed and silence this prompt permanently\n"
            "• **Cancel** — keep current conversation\n\n"
            "_Text fallback: reply `/approve`, `/always`, or `/cancel`._"
        )
        return await self._request_slash_confirm(
            event=event,
            command=command,
            title=title,
            message=prompt_message,
            handler=_on_confirm,
        )

    async def _request_slash_confirm(
        self,
        *,
        event: MessageEvent,
        command: str,
        title: str,
        message: str,
        handler,
    ) -> Optional[str]:
        """Ask the user to confirm an expensive slash command.

        ``handler`` is an async callable ``handler(choice: str) -> str``
        where ``choice`` is ``"once"``, ``"always"``, or ``"cancel"``.
        The handler runs on the event loop when the user responds; its
        return value is sent back as a gateway message.

        Returns a short acknowledgment string to send immediately (before
        the user's response).  If buttons rendered successfully the ack
        is ``None`` (buttons are self-explanatory); if we fell back to
        text the message itself IS the ack.
        """
        from tools import slash_confirm as _slash_confirm_mod

        source = event.source
        session_key = self._session_key_for_source(source)
        # Bare-runner test harnesses (object.__new__(GatewayRunner)) skip
        # __init__ and don't have the counter attribute — fall back to a
        # local counter so tests don't AttributeError.  Real runs always
        # have the instance attribute.
        counter = getattr(self, "_slash_confirm_counter", None)
        if counter is None:
            import itertools as _itertools
            counter = _itertools.count(1)
            self._slash_confirm_counter = counter
        confirm_id = f"{next(counter)}"

        # Register the pending confirm FIRST so a super-fast button click
        # cannot race the send_slash_confirm return.
        _slash_confirm_mod.register(session_key, confirm_id, command, handler)

        adapter = self.adapters.get(source.platform)
        metadata = self._thread_metadata_for_source(source, self._reply_anchor_for_event(event))

        used_buttons = False
        if adapter is not None:
            try:
                button_result = await adapter.send_slash_confirm(
                    chat_id=source.chat_id,
                    title=title,
                    message=message,
                    session_key=session_key,
                    confirm_id=confirm_id,
                    metadata=metadata,
                )
                if button_result and getattr(button_result, "success", False):
                    used_buttons = True
            except Exception as exc:
                logger.debug(
                    "send_slash_confirm failed for %s on %s: %s",
                    command, source.platform, exc,
                )

        if used_buttons:
            # Buttons rendered — no redundant text ack.
            return None
        # Text fallback — return the prompt message as the direct reply.
        return message

    def _read_user_config(self) -> Dict[str, Any]:
        """Read the user's raw config.yaml (cached) for gate lookups.

        Used by slash-confirm gates that must reflect on-disk state changes
        (e.g. a prior "Always Approve" click) without a gateway restart.
        """
        try:
            from hermes_cli.config import load_config
            cfg = load_config()
            return cfg if isinstance(cfg, dict) else {}
        except Exception:
            return {}

    def _thread_metadata_for_source(
        self,
        source,
        reply_to_message_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Build the metadata dict platforms need for thread-aware replies."""
        thread_id = getattr(source, "thread_id", None)
        if thread_id is None:
            return None
        metadata: Dict[str, Any] = {"thread_id": thread_id}
        if (
            getattr(source, "platform", None) == Platform.TELEGRAM
            and getattr(source, "chat_type", None) == "dm"
        ):
            metadata["telegram_dm_topic_reply_fallback"] = True
            # Telegram DM topic lanes need direct_messages_topic_id in metadata
            # so synthetic/queued messages (goal continuations, status notices)
            # route to the correct topic even when reply anchor is unavailable.
            tid = str(thread_id)
            if tid and tid not in {"", "1"}:
                metadata["direct_messages_topic_id"] = tid
            anchor = reply_to_message_id or getattr(source, "message_id", None)
            if anchor is not None:
                metadata["telegram_reply_to_message_id"] = str(anchor)
        return metadata

    @staticmethod
    def _reply_anchor_for_event(event: MessageEvent) -> Optional[str]:
        """Return the platform-specific reply anchor for GatewayRunner sends."""
        return _reply_anchor_for_event(event)


    # ------------------------------------------------------------------
    # /approve & /deny — explicit dangerous-command approval
    # ------------------------------------------------------------------

    _APPROVAL_TIMEOUT_SECONDS = 300  # 5 minutes



    # Platforms where /update is allowed.  ACP, API server, and webhooks are
    # programmatic interfaces that should not trigger system updates.
    _UPDATE_ALLOWED_PLATFORMS = frozenset({
        Platform.TELEGRAM, Platform.DISCORD, Platform.SLACK, Platform.WHATSAPP,
        Platform.SIGNAL, Platform.MATTERMOST, Platform.MATRIX,
        Platform.HOMEASSISTANT, Platform.EMAIL, Platform.SMS, Platform.DINGTALK,
        Platform.FEISHU, Platform.WECOM, Platform.WECOM_CALLBACK, Platform.WEIXIN, Platform.BLUEBUBBLES, Platform.QQBOT, Platform.LOCAL,
    })








    async def _send_restart_notification(self) -> Optional[tuple[str, str, Optional[str]]]:
        """Notify the chat that initiated /restart that the gateway is back."""
        notify_path = _hermes_home / ".restart_notify.json"
        if not notify_path.exists():
            return None

        try:
            data = json.loads(notify_path.read_text())
            platform_str = data.get("platform")
            chat_id = data.get("chat_id")
            thread_id = data.get("thread_id")

            if not platform_str or not chat_id:
                return None

            platform = Platform(platform_str)
            adapter = self.adapters.get(platform)
            if not adapter:
                logger.debug(
                    "Restart notification skipped: %s adapter not connected",
                    platform_str,
                )
                return None

            platform_cfg = self.config.platforms.get(platform)
            if platform_cfg is not None and not platform_cfg.gateway_restart_notification:
                logger.info(
                    "Restart notification suppressed: %s has gateway_restart_notification=false",
                    platform_str,
                )
                return None

            metadata = {"thread_id": thread_id} if thread_id else None
            result = await adapter.send(
                str(chat_id),
                "♻ Gateway restarted successfully. Your session continues.",
                metadata=metadata,
            )
            # adapter.send() catches provider errors (e.g. "Chat not found")
            # and returns SendResult(success=False) rather than raising, so
            # we must inspect the result before claiming success — otherwise
            # the log line is misleading and hides real delivery failures.
            if result is not None and getattr(result, "success", True) is False:
                logger.warning(
                    "Restart notification to %s:%s was not delivered: %s",
                    platform_str,
                    chat_id,
                    getattr(result, "error", "send returned success=False"),
                )
                return None

            logger.info(
                "Sent restart notification to %s:%s",
                platform_str,
                chat_id,
            )
            return str(platform_str), str(chat_id), str(thread_id) if thread_id else None
        except Exception as e:
            logger.warning("Restart notification failed: %s", e)
            return None
        finally:
            notify_path.unlink(missing_ok=True)

    async def _send_home_channel_startup_notifications(
        self,
        *,
        skip_targets: Optional[set[tuple[str, str, Optional[str]]]] = None,
    ) -> set[tuple[str, str, Optional[str]]]:
        """Notify configured home channels that the gateway is back online.

        The notification is best-effort and sent once per connected platform
        home channel. ``skip_targets`` lets startup avoid duplicate messages
        when a more specific restart notification is queued for the same chat.
        """
        delivered: set[tuple[str, str, Optional[str]]] = set()
        skipped = skip_targets or set()
        message = "♻️ Gateway online — Hermes is back and ready."

        for platform, adapter in self.adapters.items():
            home = self.config.get_home_channel(platform)
            if not home or not home.chat_id:
                continue

            platform_cfg = self.config.platforms.get(platform)
            if platform_cfg is not None and not platform_cfg.gateway_restart_notification:
                logger.info(
                    "Home-channel startup notification suppressed: %s has gateway_restart_notification=false",
                    platform.value,
                )
                continue

            target = (platform.value, str(home.chat_id), str(home.thread_id) if home.thread_id else None)
            if target in skipped or target in delivered:
                continue

            try:
                metadata = {"thread_id": home.thread_id} if home.thread_id else None
                if metadata:
                    result = await adapter.send(str(home.chat_id), message, metadata=metadata)
                else:
                    result = await adapter.send(str(home.chat_id), message)
                if result is not None and getattr(result, "success", True) is False:
                    logger.warning(
                        "Home-channel startup notification failed for %s:%s: %s",
                        platform.value,
                        home.chat_id,
                        getattr(result, "error", "send returned success=False"),
                    )
                    continue

                delivered.add(target)
                logger.info(
                    "Sent home-channel startup notification to %s:%s",
                    platform.value,
                    home.chat_id,
                )
            except Exception as exc:
                logger.warning(
                    "Home-channel startup notification failed for %s:%s: %s",
                    platform.value,
                    home.chat_id,
                    exc,
                )

        return delivered



    async def _run_in_executor_with_context(self, func, *args):
        """Run blocking work in the thread pool while preserving session contextvars."""
        loop = asyncio.get_running_loop()
        ctx = copy_context()
        return await loop.run_in_executor(None, ctx.run, func, *args)

    def _decide_image_input_mode(self) -> str:
        """Resolve the image-input routing for the currently active model.

        Returns ``"native"`` (attach pixels on the user turn) or ``"text"``
        (pre-analyze with vision_analyze and prepend the description). See
        agent/image_routing.py for the full decision table.

        The active provider/model are read from config.yaml so the decision
        tracks ``/model`` switches automatically on the next message.
        """
        try:
            from agent.image_routing import decide_image_input_mode
            from agent.auxiliary_client import _read_main_model, _read_main_provider
            from hermes_cli.config import load_config

            cfg = load_config()
            provider = _read_main_provider()
            model = _read_main_model()
            return decide_image_input_mode(provider, model, cfg)
        except Exception as exc:
            logger.debug("image_routing: decision failed, falling back to text — %s", exc)
            return "text"

    async def _enrich_message_with_vision(
        self,
        user_text: str,
        image_paths: List[str],
    ) -> str:
        """
        Auto-analyze user-attached images with the vision tool and prepend
        the descriptions to the message text.

        Each image is analyzed with a general-purpose prompt.  The resulting
        description *and* the local cache path are injected so the model can:
          1. Immediately understand what the user sent (no extra tool call).
          2. Re-examine the image with vision_analyze if it needs more detail.

        Args:
            user_text:   The user's original caption / message text.
            image_paths: List of local file paths to cached images.

        Returns:
            The enriched message string with vision descriptions prepended.
        """
        from tools.vision_tools import vision_analyze_tool
        from agent.memory_manager import sanitize_context

        analysis_prompt = (
            "Describe everything visible in this image in thorough detail. "
            "Include any text, code, data, objects, people, layout, colors, "
            "and any other notable visual information."
        )

        enriched_parts = []
        for path in image_paths:
            try:
                logger.debug("Auto-analyzing user image: %s", path)
                result_json = await vision_analyze_tool(
                    image_url=path,
                    user_prompt=analysis_prompt,
                )
                result = json.loads(result_json)
                if result.get("success"):
                    description = result.get("analysis", "")
                    description = sanitize_context(description)
                    enriched_parts.append(
                        f"[The user sent an image~ Here's what I can see:\n{description}]\n"
                        f"[If you need a closer look, use vision_analyze with "
                        f"image_url: {path} ~]"
                    )
                else:
                    enriched_parts.append(
                        "[The user sent an image but I couldn't quite see it "
                        "this time (>_<) You can try looking at it yourself "
                        f"with vision_analyze using image_url: {path}]"
                    )
            except Exception as e:
                logger.error("Vision auto-analysis error: %s", e)
                enriched_parts.append(
                    f"[The user sent an image but something went wrong when I "
                    f"tried to look at it~ You can try examining it yourself "
                    f"with vision_analyze using image_url: {path}]"
                )

        # Combine: vision descriptions first, then the user's original text
        if enriched_parts:
            prefix = "\n\n".join(enriched_parts)
            if user_text:
                return f"{prefix}\n\n{user_text}"
            return prefix
        return user_text

    async def _enrich_message_with_transcription(
        self,
        user_text: str,
        audio_paths: List[str],
    ) -> str:
        """
        Auto-transcribe user voice/audio messages using the configured STT provider
        and prepend the transcript to the message text.

        Args:
            user_text:   The user's original caption / message text.
            audio_paths: List of local file paths to cached audio files.

        Returns:
            The enriched message string with transcriptions prepended.
        """
        if not getattr(self.config, "stt_enabled", True):
            notes = []
            for path in audio_paths:
                abs_path = os.path.abspath(path)
                duration_str = await _probe_audio_duration(abs_path)
                if duration_str:
                    notes.append(
                        f"[The user sent a voice message: {abs_path} (duration: {duration_str})]"
                    )
                else:
                    notes.append(f"[The user sent a voice message: {abs_path}]")
            if not notes:
                return user_text
            prefix = "\n\n".join(notes)
            _placeholder = "(The user sent a message with no text content)"
            if user_text and user_text.strip() == _placeholder:
                return prefix
            if user_text:
                return f"{prefix}\n\n{user_text}"
            return prefix

        from tools.transcription_tools import transcribe_audio

        enriched_parts = []
        for path in audio_paths:
            try:
                logger.debug("Transcribing user voice: %s", path)
                result = await asyncio.to_thread(transcribe_audio, path)
                if result["success"]:
                    transcript = result["transcript"]
                    enriched_parts.append(
                        f'[The user sent a voice message~ '
                        f'Here\'s what they said: "{transcript}"]'
                    )
                else:
                    error = result.get("error", "unknown error")
                    if (
                        "No STT provider" in error
                        or error.startswith("Neither VOICE_TOOLS_OPENAI_KEY nor OPENAI_API_KEY is set")
                    ):
                        _no_stt_note = (
                            "[The user sent a voice message but I can't listen "
                            "to it right now — no STT provider is configured. "
                            "A direct message has already been sent to the user "
                            "with setup instructions."
                        )
                        if self._has_setup_skill():
                            _no_stt_note += (
                                " You have a skill called hermes-agent-setup "
                                "that can help users configure Hermes features "
                                "including voice, tools, and more."
                            )
                        _no_stt_note += "]"
                        enriched_parts.append(_no_stt_note)
                    else:
                        enriched_parts.append(
                            "[The user sent a voice message but I had trouble "
                            f"transcribing it~ ({error})]"
                        )
            except Exception as e:
                logger.error("Transcription error: %s", e)
                enriched_parts.append(
                    "[The user sent a voice message but something went wrong "
                    "when I tried to listen to it~ Let them know!]"
                )

        if enriched_parts:
            prefix = "\n\n".join(enriched_parts)
            # Strip the empty-content placeholder from the Discord adapter
            # when we successfully transcribed the audio — it's redundant.
            _placeholder = "(The user sent a message with no text content)"
            if user_text and user_text.strip() == _placeholder:
                return prefix
            if user_text:
                return f"{prefix}\n\n{user_text}"
            return prefix
        return user_text

    def _build_process_event_source(self, evt: dict):
        """Resolve the canonical source for a synthetic background-process event.

        Prefer the persisted session-store origin for the event's session key.
        Falling back to the currently active foreground event is what causes
        cross-topic bleed, so don't do that.
        """
        from gateway.session import SessionSource

        session_key = str(evt.get("session_key") or "").strip()
        derived_platform = ""
        derived_chat_type = ""
        derived_chat_id = ""

        if session_key:
            try:
                self.session_store._ensure_loaded()
                entry = self.session_store._entries.get(session_key)
                if entry and getattr(entry, "origin", None):
                    return entry.origin
            except Exception as exc:
                logger.debug(
                    "Synthetic process-event session-store lookup failed for %s: %s",
                    session_key,
                    exc,
                )

            cached_source = self._get_cached_session_source(session_key)
            if cached_source is not None:
                return cached_source

            _parsed = _parse_session_key(session_key)
            if _parsed:
                derived_platform = _parsed["platform"]
                derived_chat_type = _parsed["chat_type"]
                derived_chat_id = _parsed["chat_id"]

        platform_name = str(evt.get("platform") or derived_platform or "").strip().lower()
        chat_type = str(evt.get("chat_type") or derived_chat_type or "").strip().lower()
        chat_id = str(evt.get("chat_id") or derived_chat_id or "").strip()
        if not platform_name or not chat_type or not chat_id:
            return None

        try:
            platform = Platform(platform_name)
            # Reject arbitrary strings that create dynamic pseudo-members.
            # Built-in platforms are always valid; plugin platforms must be
            # registered in the platform registry.
            if platform.value not in _BUILTIN_PLATFORM_VALUES:
                try:
                    from gateway.platform_registry import platform_registry
                    if not platform_registry.is_registered(platform.value):
                        raise ValueError(platform_name)
                except Exception:
                    raise ValueError(platform_name)
        except Exception:
            logger.warning(
                "Synthetic process event has invalid platform metadata: %r",
                platform_name,
            )
            return None

        return SessionSource(
            platform=platform,
            chat_id=chat_id,
            chat_type=chat_type,
            thread_id=str(evt.get("thread_id") or "").strip() or None,
            user_id=str(evt.get("user_id") or "").strip() or None,
            user_name=str(evt.get("user_name") or "").strip() or None,
        )



    _MAX_INTERRUPT_DEPTH = 3  # Cap recursive interrupt handling (#816)

    # Config keys whose values MUST invalidate the gateway's cached agent
    # when they change.  The agent bakes these into its compressor / context
    # handling at construction time, so a mid-running-gateway config edit
    # would otherwise be silently ignored until the user triggers a
    # different cache eviction (model switch, /reset, etc.).
    #
    # Each entry is a tuple of (section, key) read from the raw config dict.
    # Add more here as new baked-at-construction config settings are added.
    _CACHE_BUSTING_CONFIG_KEYS: tuple = (
        ("model", "context_length"),
        ("model", "max_tokens"),
        ("compression", "enabled"),
        ("compression", "threshold"),
        ("compression", "target_ratio"),
        ("compression", "protect_last_n"),
        ("agent", "disabled_toolsets"),
    )

    @classmethod
    def _extract_cache_busting_config(cls, user_config: dict | None) -> dict:
        """Pull values that must bust the cached agent.

        Returns a flat dict keyed by 'section.key'.  Missing config keys and
        non-dict sections yield None values, which still contribute to the
        signature (so 'absent' vs 'present-and-null' differ).

        The live tool registry generation is included too.  MCP reloads and
        dynamic MCP tool-list changes mutate the registry without necessarily
        changing config.yaml.  Cached AIAgent instances freeze their tool
        schemas at construction time, so a registry generation change must
        rebuild the agent before the next turn.
        """
        out: Dict[str, Any] = {}
        cfg = user_config if isinstance(user_config, dict) else {}
        for section, key in cls._CACHE_BUSTING_CONFIG_KEYS:
            section_val = cfg.get(section)
            if isinstance(section_val, dict):
                out[f"{section}.{key}"] = section_val.get(key)
            else:
                out[f"{section}.{key}"] = None
        try:
            from tools.registry import registry

            out["tools.registry_generation"] = getattr(registry, "_generation", None)
        except Exception:
            out["tools.registry_generation"] = None
        return out

    @staticmethod
    def _agent_config_signature(
        model: str,
        runtime: dict,
        enabled_toolsets: list,
        ephemeral_prompt: str,
        cache_keys: dict | None = None,
    ) -> str:
        """Compute a stable string key from agent config values.

        When this signature changes between messages, the cached AIAgent is
        discarded and rebuilt.  When it stays the same, the cached agent is
        reused — preserving the frozen system prompt and tool schemas for
        prompt cache hits.

        ``cache_keys`` is an optional flat dict of additional config values
        that should invalidate the cache when they change.  Callers pass
        the output of ``_extract_cache_busting_config(user_config)`` so
        edits to model.context_length / compression.* in config.yaml are
        picked up on the next gateway message without a manual restart.
        """
        import hashlib, json as _j

        # Fingerprint the FULL credential string instead of using a short
        # prefix. OAuth/JWT-style tokens frequently share a common prefix
        # (e.g. "eyJhbGci"), which can cause false cache hits across auth
        # switches if only the first few characters are considered.
        _api_key = str(runtime.get("api_key", "") or "")
        _api_key_fingerprint = hashlib.sha256(_api_key.encode()).hexdigest() if _api_key else ""

        _cache_keys_sorted = sorted((cache_keys or {}).items())

        blob = _j.dumps(
            [
                model,
                _api_key_fingerprint,
                runtime.get("base_url", ""),
                runtime.get("provider", ""),
                runtime.get("api_mode", ""),
                sorted(enabled_toolsets) if enabled_toolsets else [],
                # reasoning_config excluded — it's set per-message on the
                # cached agent and doesn't affect system prompt or tools.
                ephemeral_prompt or "",
                _cache_keys_sorted,
            ],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


    def _is_intentional_model_switch(self, session_key: str, agent_model: str) -> bool:
        """Return True if *agent_model* matches an active /model session override."""
        override = self._session_model_overrides.get(session_key)
        return override is not None and override.get("model") == agent_model

    def _release_running_agent_state(
        self,
        session_key: str,
        *,
        run_generation: Optional[int] = None,
    ) -> bool:
        """Pop ALL per-running-agent state entries for ``session_key``.

        Replaces ad-hoc ``del self._running_agents[key]`` calls scattered
        across the gateway.  Those sites had drifted: some popped only
        ``_running_agents``; some also ``_running_agents_ts``; only one
        path also cleared ``_busy_ack_ts``.  Each missed entry was a
        small, persistent leak — a (str_key → float) tuple per session
        per gateway lifetime.

        Use this at every site that ends a running turn, regardless of
        cause (normal completion, /stop, /reset, /resume, sentinel
        cleanup, stale-eviction).  Per-session state that PERSISTS
        across turns (``_session_model_overrides``, ``_voice_mode``,
        ``_pending_approvals``, ``_update_prompt_pending``) is NOT
        touched here — those have their own lifecycles.

        When ``run_generation`` is provided, only clear the slot if that
        generation is still current for the session.  This prevents an
        older async run whose generation was bumped by /stop or /new from
        clobbering a newer run's state during its own unwind.  Returns
        True when the slot was cleared, False when an ownership guard
        blocked it.
        """
        if not session_key:
            return False
        if run_generation is not None and not self._is_session_run_current(
            session_key, run_generation
        ):
            return False
        self._running_agents.pop(session_key, None)
        self._running_agents_ts.pop(session_key, None)
        if hasattr(self, "_busy_ack_ts"):
            self._busy_ack_ts.pop(session_key, None)
        return True





    def _bind_adapter_run_generation(
        self,
        adapter: Any,
        session_key: str,
        generation: int | None,
    ) -> None:
        """Bind a gateway run generation to the adapter's active-session event."""
        if not adapter or not session_key or generation is None:
            return
        try:
            interrupt_event = getattr(adapter, "_active_sessions", {}).get(session_key)
            if interrupt_event is not None:
                setattr(interrupt_event, "_hermes_run_generation", int(generation))
        except Exception:
            pass


    def _evict_cached_agent(self, session_key: str) -> None:
        """Remove a cached agent for a session (called on /new, /model, etc)."""
        _lock = getattr(self, "_agent_cache_lock", None)
        if _lock:
            with _lock:
                self._agent_cache.pop(session_key, None)

    @staticmethod
    def _init_cached_agent_for_turn(agent: Any, interrupt_depth: int) -> None:
        """Reset per-turn state on a cached agent before a new turn starts.

        Both _last_activity_ts and _last_activity_desc are only reset for
        fresh external turns (depth 0); they are semantically paired —
        desc describes the activity *at* ts, so updating one without the
        other would make get_activity_summary() misleading.
        For interrupt-recursive turns both are preserved so the inactivity
        watchdog can accumulate stuck-turn idle time and fire the 30-min
        timeout (#15654).  The depth-0 reset is still needed: a session
        idle for 29 min would otherwise trip the watchdog before the new
        turn makes its first API call (#9051).
        """
        if interrupt_depth == 0:
            agent._last_activity_ts = time.time()
            agent._last_activity_desc = "starting new turn (cached)"
        agent._api_call_count = 0

    def _release_evicted_agent_soft(self, agent: Any) -> None:
        """Soft cleanup for cache-evicted agents — preserves session tool state.

        Called from _enforce_agent_cache_cap and _sweep_idle_cached_agents.
        Distinct from _cleanup_agent_resources (full teardown) because a
        cache-evicted session may resume at any time — its terminal
        sandbox, browser daemon, and tracked bg processes must outlive
        the Python AIAgent instance so the next agent built for the
        same task_id inherits them.
        """
        if agent is None:
            return
        try:
            if hasattr(agent, "release_clients"):
                agent.release_clients()
            else:
                # Older agent instance (shouldn't happen in practice) —
                # fall back to the legacy full-close path.
                self._cleanup_agent_resources(agent)
        except Exception:
            pass

    def _enforce_agent_cache_cap(self) -> None:
        """Evict oldest cached agents when cache exceeds _AGENT_CACHE_MAX_SIZE.

        Must be called with _agent_cache_lock held.  Resource cleanup
        (memory provider shutdown, tool resource close) is scheduled
        on a daemon thread so the caller doesn't block on slow teardown
        while holding the cache lock.

        Agents currently in _running_agents are SKIPPED — their clients,
        terminal sandboxes, background processes, and child subagents
        are all in active use by the running turn.  Evicting them would
        tear down those resources mid-turn and crash the request.  If
        every candidate in the LRU order is active, we simply leave the
        cache over the cap; it will be re-checked on the next insert.
        """
        _cache = getattr(self, "_agent_cache", None)
        if _cache is None:
            return
        # OrderedDict.popitem(last=False) pops oldest; plain dict lacks the
        # arg so skip enforcement if a test fixture swapped the cache type.
        if not hasattr(_cache, "move_to_end"):
            return

        # Snapshot of agent instances that are actively mid-turn.  Use id()
        # so the lookup is O(1) and doesn't depend on AIAgent.__eq__ (which
        # MagicMock overrides in tests).
        running_ids = {
            id(a)
            for a in getattr(self, "_running_agents", {}).values()
            if a is not None and a is not _AGENT_PENDING_SENTINEL
        }

        # Walk LRU → MRU and evict excess-LRU entries that aren't mid-turn.
        # We only consider entries in the first (size - cap) LRU positions
        # as eviction candidates.  If one of those slots is held by an
        # active agent, we SKIP it without compensating by evicting a
        # newer entry — that would penalise a freshly-inserted session
        # (which has no cache history to retain) while protecting an
        # already-cached long-running one.  The cache may therefore stay
        # temporarily over cap; it will re-check on the next insert,
        # after active turns have finished.
        excess = max(0, len(_cache) - _AGENT_CACHE_MAX_SIZE)
        evict_plan: List[tuple] = []  # [(key, agent), ...]
        if excess > 0:
            ordered_keys = list(_cache.keys())
            for key in ordered_keys[:excess]:
                entry = _cache.get(key)
                agent = entry[0] if isinstance(entry, tuple) and entry else None
                if agent is not None and id(agent) in running_ids:
                    continue  # active mid-turn; don't evict, don't substitute
                evict_plan.append((key, agent))

        for key, _ in evict_plan:
            _cache.pop(key, None)

        remaining_over_cap = len(_cache) - _AGENT_CACHE_MAX_SIZE
        if remaining_over_cap > 0:
            logger.warning(
                "Agent cache over cap (%d > %d); %d excess slot(s) held by "
                "mid-turn agents — will re-check on next insert.",
                len(_cache), _AGENT_CACHE_MAX_SIZE, remaining_over_cap,
            )

        for key, agent in evict_plan:
            logger.info(
                "Agent cache at cap; evicting LRU session=%s (cache_size=%d)",
                key, len(_cache),
            )
            if agent is not None:
                threading.Thread(
                    target=self._release_evicted_agent_soft,
                    args=(agent,),
                    daemon=True,
                    name=f"agent-cache-evict-{key[:24]}",
                ).start()

    def _sweep_idle_cached_agents(self) -> int:
        """Evict cached agents whose AIAgent has been idle > _AGENT_CACHE_IDLE_TTL_SECS.

        Safe to call from the session expiry watcher without holding the
        cache lock — acquires it internally.  Returns the number of entries
        evicted.  Resource cleanup is scheduled on daemon threads.

        Agents currently in _running_agents are SKIPPED for the same reason
        as _enforce_agent_cache_cap: tearing down an active turn's clients
        mid-flight would crash the request.
        """
        _cache = getattr(self, "_agent_cache", None)
        _lock = getattr(self, "_agent_cache_lock", None)
        if _cache is None or _lock is None:
            return 0
        now = time.time()
        to_evict: List[tuple] = []
        running_ids = {
            id(a)
            for a in getattr(self, "_running_agents", {}).values()
            if a is not None and a is not _AGENT_PENDING_SENTINEL
        }
        with _lock:
            for key, entry in list(_cache.items()):
                agent = entry[0] if isinstance(entry, tuple) and entry else None
                if agent is None:
                    continue
                if id(agent) in running_ids:
                    continue  # mid-turn — don't tear it down
                last_activity = getattr(agent, "_last_activity_ts", None)
                if last_activity is None:
                    continue
                if (now - last_activity) > _AGENT_CACHE_IDLE_TTL_SECS:
                    to_evict.append((key, agent))
            for key, _ in to_evict:
                _cache.pop(key, None)
        for key, agent in to_evict:
            logger.info(
                "Agent cache idle-TTL evict: session=%s (idle=%.0fs)",
                key, now - getattr(agent, "_last_activity_ts", now),
            )
            threading.Thread(
                target=self._release_evicted_agent_soft,
                args=(agent,),
                daemon=True,
                name=f"agent-cache-idle-{key[:24]}",
            ).start()
        return len(to_evict)

    # ------------------------------------------------------------------
    # Proxy mode: forward messages to a remote Hermes API server
    # ------------------------------------------------------------------

    def _get_proxy_url(self) -> Optional[str]:
        """Return the proxy URL if proxy mode is configured, else None.

        Checks GATEWAY_PROXY_URL env var first (convenient for Docker),
        then ``gateway.proxy_url`` in config.yaml.
        """
        url = os.getenv("GATEWAY_PROXY_URL", "").strip()
        if url:
            return url.rstrip("/")
        cfg = _load_gateway_config()
        url = (cfg.get("gateway") or {}).get("proxy_url", "").strip()
        if url:
            return url.rstrip("/")
        return None

    # P3: _run_agent_via_proxy, _run_agent — extracted to gateway.agent_runner_mixin


def _start_cron_ticker(stop_event: threading.Event, adapters=None, loop=None, interval: int = 60):
    """
    Background thread that ticks the cron scheduler at a regular interval.
    
    Runs inside the gateway process so cronjobs fire automatically without
    needing a separate `hermes cron daemon` or system cron entry.

    When ``adapters`` and ``loop`` are provided, passes them through to the
    cron delivery path so live adapters can be used for E2EE rooms.

    Also refreshes the channel directory every 5 minutes and prunes the
    image/audio/document cache + expired ``hermes debug share`` pastes
    once per hour.
    """
    from cron.scheduler import tick as cron_tick
    from gateway.platforms.base import cleanup_image_cache, cleanup_document_cache
    from hermes_cli.debug import _sweep_expired_pastes

    IMAGE_CACHE_EVERY = 60   # ticks — once per hour at default 60s interval
    CHANNEL_DIR_EVERY = 5    # ticks — every 5 minutes
    PASTE_SWEEP_EVERY = 60   # ticks — once per hour
    CURATOR_EVERY = 60       # ticks — poll hourly (inner gate handles the real cadence)

    logger.info("Cron ticker started (interval=%ds)", interval)
    tick_count = 0
    while not stop_event.is_set():
        try:
            cron_tick(verbose=False, adapters=adapters, loop=loop)
        except Exception as e:
            logger.debug("Cron tick error: %s", e)

        tick_count += 1

        if tick_count % CHANNEL_DIR_EVERY == 0 and adapters:
            try:
                from gateway.channel_directory import build_channel_directory
                if loop is not None:
                    # build_channel_directory is async (Slack web calls), and
                    # this ticker runs in a background thread. Schedule onto
                    # the gateway event loop and wait briefly for completion
                    # so refresh failures are still logged via the except.
                    fut = safe_schedule_threadsafe(
                        build_channel_directory(adapters), loop,
                        logger=logger,
                        log_message="Channel directory refresh scheduling error",
                    )
                    if fut is not None:
                        fut.result(timeout=30)
            except Exception as e:
                logger.debug("Channel directory refresh error: %s", e)

        if tick_count % IMAGE_CACHE_EVERY == 0:
            try:
                removed = cleanup_image_cache(max_age_hours=24)
                if removed:
                    logger.info("Image cache cleanup: removed %d stale file(s)", removed)
            except Exception as e:
                logger.debug("Image cache cleanup error: %s", e)
            try:
                removed = cleanup_document_cache(max_age_hours=24)
                if removed:
                    logger.info("Document cache cleanup: removed %d stale file(s)", removed)
            except Exception as e:
                logger.debug("Document cache cleanup error: %s", e)

        if tick_count % PASTE_SWEEP_EVERY == 0:
            try:
                deleted, remaining = _sweep_expired_pastes()
                if deleted:
                    logger.info(
                        "Paste sweep: deleted %d expired paste(s), %d pending",
                        deleted, remaining,
                    )
            except Exception as e:
                logger.debug("Paste sweep error: %s", e)

        # Curator — piggy-back on the existing cron ticker so long-running
        # gateways get weekly skill maintenance without needing restarts.
        # maybe_run_curator() is internally gated by config.interval_hours
        # (7 days by default), so CURATOR_EVERY is just the poll rate — the
        # real work only fires once per config interval.
        if tick_count % CURATOR_EVERY == 0:
            try:
                from agent.curator import maybe_run_curator
                maybe_run_curator(
                    idle_for_seconds=float("inf"),
                    on_summary=lambda msg: logger.info("curator: %s", msg),
                )
            except Exception as e:
                logger.debug("Curator tick error: %s", e)

        stop_event.wait(timeout=interval)
    logger.info("Cron ticker stopped")


async def start_gateway(config: Optional[GatewayConfig] = None, replace: bool = False, verbosity: Optional[int] = 0) -> bool:
    """
    Start the gateway and run until interrupted.
    
    This is the main entry point for running the gateway.
    Returns True if the gateway ran successfully, False if it failed to start.
    A False return causes a non-zero exit code so systemd can auto-restart.
    
    Args:
        config: Optional gateway configuration override.
        replace: If True, kill any existing gateway instance before starting.
                 Useful for systemd services to avoid restart-loop deadlocks
                 when the previous process hasn't fully exited yet.
    """
    # ── Duplicate-instance guard ──────────────────────────────────────
    # Prevent two gateways from running under the same HERMES_HOME.
    # The PID file is scoped to HERMES_HOME, so future multi-profile
    # setups (each profile using a distinct HERMES_HOME) will naturally
    # allow concurrent instances without tripping this guard.
    from gateway.status import (
        acquire_gateway_runtime_lock,
        get_running_pid,
        get_process_start_time,
        release_gateway_runtime_lock,
        remove_pid_file,
        terminate_pid,
    )
    existing_pid = get_running_pid()
    if existing_pid is not None and existing_pid != os.getpid():
        if replace:
            existing_start_time = get_process_start_time(existing_pid)
            logger.info(
                "Replacing existing gateway instance (PID %d) with --replace.",
                existing_pid,
            )
            # Record a takeover marker so the target's shutdown handler
            # recognises its SIGTERM as a planned takeover and exits 0
            # (rather than exit 1, which would trigger systemd's
            # Restart=on-failure and start a flap loop against us).
            # Best-effort — proceed even if the write fails.
            try:
                from gateway.status import write_takeover_marker
                write_takeover_marker(existing_pid)
            except Exception as e:
                logger.debug("Could not write takeover marker: %s", e)
            try:
                terminate_pid(existing_pid, force=False)
            except ProcessLookupError:
                pass  # Already gone
            except (PermissionError, OSError):
                logger.error(
                    "Permission denied killing PID %d. Cannot replace.",
                    existing_pid,
                )
                # Marker is scoped to a specific target; clean it up on
                # give-up so it doesn't grief an unrelated future shutdown.
                try:
                    from gateway.status import clear_takeover_marker
                    clear_takeover_marker()
                except Exception:
                    pass
                return False
            # Wait up to 10 seconds for the old process to exit.
            # ``os.kill(pid, 0)`` on Windows is NOT a no-op — use the
            # handle-based existence check instead.
            from gateway.status import _pid_exists
            for _ in range(20):
                if not _pid_exists(existing_pid):
                    break  # Process is gone
                time.sleep(0.5)
            else:
                # Still alive after 10s — force kill
                logger.warning(
                    "Old gateway (PID %d) did not exit after SIGTERM, sending SIGKILL.",
                    existing_pid,
                )
                try:
                    terminate_pid(existing_pid, force=True)
                    time.sleep(0.5)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            remove_pid_file()
            # remove_pid_file() is a no-op when the PID doesn't match.
            # Force-unlink to cover the old-process-crashed case.
            try:
                (get_hermes_home() / "gateway.pid").unlink(missing_ok=True)
            except Exception:
                pass
            # Clean up any takeover marker the old process didn't consume
            # (e.g. SIGKILL'd before its shutdown handler could read it).
            try:
                from gateway.status import clear_takeover_marker
                clear_takeover_marker()
            except Exception:
                pass
            # Also release all scoped locks left by the old process.
            # Stopped (Ctrl+Z) processes don't release locks on exit,
            # leaving stale lock files that block the new gateway from starting.
            try:
                from gateway.status import release_all_scoped_locks
                _released = release_all_scoped_locks(
                    owner_pid=existing_pid,
                    owner_start_time=existing_start_time,
                )
                if _released:
                    logger.info("Released %d stale scoped lock(s) from old gateway.", _released)
            except Exception:
                pass
        else:
            hermes_home = str(get_hermes_home())
            logger.error(
                "Another gateway instance is already running (PID %d, HERMES_HOME=%s). "
                "Use 'hermes gateway restart' to replace it, or 'hermes gateway stop' first.",
                existing_pid, hermes_home,
            )
            logger.info(
                f"\n❌ Gateway already running (PID {existing_pid}).\n"
                f"   Use 'hermes gateway restart' to replace it,\n"
                f"   or 'hermes gateway stop' to kill it first.\n"
                f"   Or use 'hermes gateway run --replace' to auto-replace.\n"
            )
            return False

    # Sync bundled skills on gateway start (fast -- skips unchanged)
    try:
        from tools.skills_sync import sync_skills
        sync_skills(quiet=True)
    except Exception:
        pass

    # Centralized logging — agent.log (INFO+), errors.log (WARNING+),
    # and gateway.log (INFO+, gateway-component records only).
    # Idempotent, so repeated calls from AIAgent.__init__ won't duplicate.
    from hermes_logging import setup_logging
    setup_logging(hermes_home=_hermes_home, mode="gateway")

    # Periodic process memory usage logging (gateway only) — emits a
    # grep-friendly "[MEMORY] rss=...MB ..." line every N minutes so
    # slow leaks in the long-lived gateway process show up as a time
    # series in agent.log / gateway.log.  Ported from cline/cline#10343.
    # Controlled by the logging.memory_monitor section in config.yaml.
    try:
        from gateway import memory_monitor as _memory_monitor

        _mm_cfg = {}
        try:
            # config is loaded a few lines up; re-read the logging section
            # here so we pick up user overrides without coupling to local
            # variable names inside the start_gateway body.
            from hermes_cli.config import load_config as _load_cli_config

            _mm_cfg = (_load_cli_config() or {}).get("logging", {}).get("memory_monitor", {}) or {}
        except Exception:
            _mm_cfg = {}
        if _mm_cfg.get("enabled", True):
            try:
                _mm_interval = float(_mm_cfg.get("interval_seconds", 300))
            except (TypeError, ValueError):
                _mm_interval = 300.0

            # P2.4 self-heal: opt-in soft/hard RSS thresholds. Unset (None)
            # keeps the pure-logging behaviour. soft_limit → throttled
            # gc.collect(); hard_limit → WARNING alert + forced collection.
            def _mm_int_or_none(key):
                val = _mm_cfg.get(key)
                if val in (None, "", 0):
                    return None
                try:
                    return int(val)
                except (TypeError, ValueError):
                    return None

            _mm_soft = _mm_int_or_none("soft_limit_mb")
            _mm_hard = _mm_int_or_none("hard_limit_mb")
            try:
                _mm_gc_gap = float(_mm_cfg.get("gc_min_interval_seconds", 60))
            except (TypeError, ValueError):
                _mm_gc_gap = 60.0

            _memory_monitor.start_memory_monitoring(
                interval_seconds=_mm_interval,
                soft_limit_mb=_mm_soft,
                hard_limit_mb=_mm_hard,
                gc_min_interval_seconds=_mm_gc_gap,
            )
    except Exception as _mm_exc:
        logger.debug("Failed to start memory monitor: %s", _mm_exc)

    # P2.2: install the process-wide LLM concurrency cap from config.
    # Disabled by default (limit=0 → no-op); only throttles multi-agent
    # fan-out when explicitly configured.
    try:
        from agent import llm_concurrency as _llm_conc
        from hermes_cli.config import load_config as _load_cli_config2

        _lc_cfg = (
            (_load_cli_config2() or {}).get("agent", {}).get("llm_concurrency", {})
            or {}
        )
        try:
            _lc_limit = int(_lc_cfg.get("limit", 0) or 0)
        except (TypeError, ValueError):
            _lc_limit = 0
        try:
            _lc_timeout = float(_lc_cfg.get("acquire_timeout_seconds", 30) or 30)
        except (TypeError, ValueError):
            _lc_timeout = 30.0
        if _lc_limit > 0:
            _llm_conc.configure(limit=_lc_limit, acquire_timeout=_lc_timeout)
    except Exception as _lc_exc:
        logger.debug("Failed to configure LLM concurrency limiter: %s", _lc_exc)

    # Optional stderr handler — level driven by -v/-q flags on the CLI.
    # verbosity=None (-q/--quiet): no stderr output
    # verbosity=0    (default):    WARNING and above
    # verbosity=1    (-v):         INFO and above
    # verbosity=2+   (-vv/-vvv):   DEBUG
    if verbosity is not None:
        from agent.redact import RedactingFormatter

        _stderr_level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
        _stderr_handler = logging.StreamHandler()
        _stderr_handler.setLevel(_stderr_level)
        _stderr_handler.setFormatter(RedactingFormatter('%(levelname)s %(name)s: %(message)s'))
        logging.getLogger().addHandler(_stderr_handler)
        # Lower root logger level if needed so DEBUG records can reach the handler
        if _stderr_level < logging.getLogger().level:
            logging.getLogger().setLevel(_stderr_level)

    runner = GatewayRunner(config)
    
    # Track whether an unexpected signal initiated the shutdown. When an
    # unexpected SIGTERM kills the gateway, we exit non-zero so service
    # managers can revive the process. Planned stop paths write a marker
    # before signalling us so they can exit cleanly instead.
    _signal_initiated_shutdown = False

    # Set up signal handlers
    def shutdown_signal_handler(received_signal=None):
        nonlocal _signal_initiated_shutdown
        # Planned --replace takeover check: when a sibling gateway is
        # taking over via --replace, it wrote a marker naming this PID
        # before sending SIGTERM. If present, treat the signal as a
        # planned shutdown and exit 0 so systemd's Restart=on-failure
        # doesn't revive us (which would flap-fight the replacer when
        # both services are enabled, e.g. hermes.service + hermes-
        # gateway.service from pre-rename installs).
        planned_takeover = False
        try:
            from gateway.status import consume_takeover_marker_for_self
            planned_takeover = consume_takeover_marker_for_self()
        except Exception as e:
            logger.debug("Takeover marker check failed: %s", e)

        # Planned stop check: service managers and `hermes gateway stop`
        # also send SIGTERM, which is indistinguishable from an unexpected
        # external kill unless the CLI marks it first. SIGINT comes from an
        # interactive Ctrl+C and is likewise an intentional foreground stop.
        planned_stop = False
        if received_signal == signal.SIGINT:
            planned_stop = True
        elif not planned_takeover:
            try:
                from gateway.status import consume_planned_stop_marker_for_self
                planned_stop = consume_planned_stop_marker_for_self()
            except Exception as e:
                logger.debug("Planned stop marker check failed: %s", e)

        # Fast (<10ms) snapshot of who's asking us to shut down — runs
        # synchronously inside the asyncio signal handler, so we keep it
        # purely stdlib + /proc reads, no subprocesses.  See PR #15826
        # (May 2026): the previous implementation called `ps aux` here
        # synchronously, blocking the event loop for up to 3s while
        # adapter teardown couldn't begin.
        try:
            from gateway.shutdown_forensics import (
                format_context_for_log,
                snapshot_shutdown_context,
                spawn_async_diagnostic,
            )
            _shutdown_ctx = snapshot_shutdown_context(received_signal)
        except Exception as _e:
            _shutdown_ctx = None
            logger.debug("snapshot_shutdown_context failed: %s", _e)

        if planned_takeover:
            logger.info(
                "Received %s as a planned --replace takeover — exiting cleanly",
                _shutdown_ctx["signal"] if _shutdown_ctx else "SIGTERM",
            )
        elif planned_stop:
            logger.info(
                "Received %s as a planned gateway stop — exiting cleanly",
                _shutdown_ctx["signal"] if _shutdown_ctx else "SIGTERM/SIGINT",
            )
        else:
            _signal_initiated_shutdown = True
            logger.info(
                "Received %s — initiating shutdown",
                _shutdown_ctx["signal"] if _shutdown_ctx else "SIGTERM/SIGINT",
            )

        # Always log who/what triggered the signal — most useful single
        # line when diagnosing "the gateway keeps dying" tickets.  Format
        # is one line, key=value, parent_cmdline last (often long).
        if _shutdown_ctx is not None:
            try:
                logger.warning(
                    "Shutdown context: %s", format_context_for_log(_shutdown_ctx)
                )
            except Exception as _e:
                logger.debug("format_context_for_log failed: %s", _e)

            # Spawn the heavyweight diagnostic (ps auxf, pstree, dmesg) in
            # a detached subprocess so it can finish writing to disk even
            # if our cgroup is being torn down.  Bounded by an internal
            # timeout; never blocks the event loop here.
            try:
                _diag_log = _hermes_home / "logs" / "gateway-shutdown-diag.log"
                spawn_async_diagnostic(
                    _diag_log, _shutdown_ctx["signal"], timeout_seconds=5.0
                )
            except Exception as _e:
                logger.debug("spawn_async_diagnostic failed: %s", _e)
        asyncio.create_task(runner.stop())

    def restart_signal_handler():
        runner.request_restart(detached=False, via_service=True)
    
    loop = asyncio.get_running_loop()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, shutdown_signal_handler, sig)  # windows-footgun: ok — wrapped in try/except NotImplementedError for Windows
            except NotImplementedError:
                pass
        if hasattr(signal, "SIGUSR1"):
            try:
                loop.add_signal_handler(signal.SIGUSR1, restart_signal_handler)  # windows-footgun: ok — POSIX signal, guarded by hasattr above + try/except NotImplementedError
            except NotImplementedError:
                pass
    else:
        logger.info("Skipping signal handlers (not running in main thread).")
    
    # Claim the PID file BEFORE bringing up any platform adapters.
    # This closes the --replace race window: two concurrent `gateway run
    # --replace` invocations both pass the termination-wait above, but
    # only the winner of the O_CREAT|O_EXCL race below will ever open
    # Telegram polling, Discord gateway sockets, etc. The loser exits
    # cleanly before touching any external service.
    import atexit
    from gateway.status import write_pid_file, remove_pid_file, get_running_pid
    _current_pid = get_running_pid()
    if _current_pid is not None and _current_pid != os.getpid():
        logger.error(
            "Another gateway instance (PID %d) started during our startup. "
            "Exiting to avoid double-running.", _current_pid
        )
        return False
    if not acquire_gateway_runtime_lock():
        logger.error(
            "Gateway runtime lock is already held by another instance. Exiting."
        )
        return False
    try:
        write_pid_file()
    except FileExistsError:
        release_gateway_runtime_lock()
        logger.error(
            "PID file race lost to another gateway instance. Exiting."
        )
        return False
    atexit.register(remove_pid_file)
    atexit.register(release_gateway_runtime_lock)

    # MCP tool discovery — run in an executor so the asyncio event loop
    # stays responsive even when a configured MCP server is slow or
    # unreachable.  discover_mcp_tools() uses a blocking 120s wait
    # internally; calling it from the loop thread would freeze platform
    # heartbeats (Discord shard, Telegram polling) until it returned.
    # See #16856.
    try:
        from tools.mcp_tool import discover_mcp_tools
        _loop = asyncio.get_running_loop()
        await _loop.run_in_executor(None, discover_mcp_tools)
    except Exception as e:
        logger.debug("MCP tool discovery failed: %s", e)

    # Start the gateway
    success = await runner.start()
    if not success:
        return False
    if runner.should_exit_cleanly:
        if runner.exit_reason:
            logger.error("Gateway exiting cleanly: %s", runner.exit_reason)
        return True
    
    # Start background cron ticker so scheduled jobs fire automatically.
    # Pass the event loop so cron delivery can use live adapters (E2EE support).
    cron_stop = threading.Event()
    cron_thread = threading.Thread(
        target=_start_cron_ticker,
        args=(cron_stop,),
        kwargs={"adapters": runner.adapters, "loop": asyncio.get_running_loop()},
        daemon=True,
        name="cron-ticker",
    )
    cron_thread.start()
    
    # Wait for shutdown
    await runner.wait_for_shutdown()

    if runner.should_exit_with_failure:
        if runner.exit_reason:
            logger.error("Gateway exiting with failure: %s", runner.exit_reason)
        return False
    
    # Stop cron ticker cleanly
    cron_stop.set()
    cron_thread.join(timeout=5)

    # Close MCP server connections
    try:
        from tools.mcp_tool import shutdown_mcp_servers
        shutdown_mcp_servers()
    except Exception:
        pass

    # Stop the periodic memory monitor (if it was started above).
    # This also emits one final "[MEMORY] shutdown rss=..." line so the
    # last RSS reading before gateway exit is always in the log.
    try:
        from gateway import memory_monitor as _memory_monitor

        _memory_monitor.stop_memory_monitoring(timeout=2.0)
    except Exception:
        pass

    if runner.exit_code is not None:
        raise SystemExit(runner.exit_code)

    # When an unexpected SIGTERM caused the shutdown and it wasn't a planned
    # restart (/restart, /update, SIGUSR1), exit non-zero so systemd's
    # Restart=on-failure revives the process.  This covers:
    #   - hermes update killing the gateway mid-work
    #   - External kill commands
    #   - WSL2/container runtime sending unexpected signals
    # `hermes gateway stop` and interactive Ctrl+C are handled above as
    # planned stops and should not trigger service-manager revival.
    if _signal_initiated_shutdown and not runner._restart_requested:
        logger.info(
            "Exiting with code 1 (signal-initiated shutdown without restart "
            "request) so systemd Restart=on-failure can revive the gateway."
        )
        return False  # → sys.exit(1) in the caller

    # When the gateway is restarting via the service manager (SIGUSR1 →
    # launchd_restart or /restart / /update commands), exit with code 75 so
    # that launchd's ``KeepAlive → SuccessfulExit → false`` policy treats
    # the exit as *unsuccessful* and relaunches the service.  This mirrors
    # the systemd ``RestartForceExitStatus=75`` convention already used by
    # the systemd unit template.
    if runner._restart_via_service:
        logger.info(
            "Exiting with code 75 (service-restart requested) so "
            "launchd KeepAlive relaunches the gateway."
        )
        raise SystemExit(75)

    return True


def main():
    """CLI entry point for the gateway."""
    # Force UTF-8 stdio on Windows — gateway logs and startup banner would
    # otherwise UnicodeEncodeError on cp1252 consoles.  No-op on POSIX.
    try:
        from hermes_cli.stdio import configure_windows_stdio
        configure_windows_stdio()
    except Exception:
        pass

    import argparse
    
    parser = argparse.ArgumentParser(description="Hermes Gateway - Multi-platform messaging")
    parser.add_argument("--config", "-c", help="Path to gateway config file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    config = None
    if args.config:
        import yaml
        with open(args.config, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            config = GatewayConfig.from_dict(data)
    
    # Run the gateway - exit with code 1 if no platforms connected,
    # so systemd Restart=on-failure will retry on transient errors (e.g. DNS)
    success = asyncio.run(start_gateway(config))
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
