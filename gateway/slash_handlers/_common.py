"""Shared imports and helpers for slash_handlers sub-mixins (W1).

Every handler sub-mixin does ``from gateway.slash_handlers._common import *``
so method global-name lookups resolve exactly as they did in the original
monolithic slash_commands_mixin.py. ``__all__`` intentionally includes
single-underscore names so ``import *`` re-exports them; test monkeypatching
now targets the sub-module where a handler physically lives.
"""
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
from agent.account_usage import fetch_account_usage, render_account_usage_lines
from agent.async_utils import safe_schedule_threadsafe
from agent.i18n import t
from hermes_cli.config import cfg_get
from hermes_constants import get_hermes_home
from utils import atomic_json_write, atomic_yaml_write, base_url_host_matches, is_truthy_value
from dotenv import load_dotenv  # backward-compat for tests that monkeypatch this symbol
from hermes_cli.env_loader import load_hermes_dotenv
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
import weakref as _weakref
from gateway.telegram_topics_mixin import TelegramTopicsMixin
from gateway.voice_mixin import VoiceMixin
from gateway.goal_mixin import GoalMixin
from gateway.kanban_mixin import KanbanMixin
from gateway.gateway_utils import (
    _home_target_env_var,
    _home_thread_env_var,
    _load_gateway_config,
    _platform_config_key,
    _resolve_gateway_model,
    _resolve_hermes_bin,
    _telegramize_command_mentions,
)

import logging
logger = logging.getLogger(__name__)


def _get_hermes_home():
    """Dynamically resolve _hermes_home from gateway.run (monkeypatchable)."""
    from gateway import run as _run
    return _run._hermes_home


from gateway._run_attr import _get_run_attr  # W2: shared, was duplicated in 4 mixins




# Export everything (incl. single-underscore names) so `import *` mirrors
# the original module namespace exactly.
__all__ = [n for n in list(globals()) if not n.startswith('__')]
