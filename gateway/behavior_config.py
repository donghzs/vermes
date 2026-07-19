"""Platform behavior configuration objects.

These dataclasses provide structured access to platform behavior settings
(require_mention, allow_bots, reactions, etc.) that were previously
bridged through environment variables by load_gateway_config().

During the transition period, the env-var bridging is retained for
backward compatibility. Downstream modules can optionally use
BehaviorConfig for per-session configuration isolation.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class SlackBehaviorConfig:
    require_mention: Optional[bool] = None
    strict_mention: Optional[bool] = None
    allow_bots: Optional[str] = None  # "none" / "mention" / "always"
    free_response_channels: List[str] = field(default_factory=list)
    reactions: Optional[bool] = None
    allowed_channels: List[str] = field(default_factory=list)


@dataclass
class DiscordBehaviorConfig:
    require_mention: Optional[bool] = None
    thread_require_mention: Optional[bool] = None
    allow_bots: Optional[str] = None
    free_response_channels: List[str] = field(default_factory=list)
    auto_thread: Optional[bool] = None
    reactions: Optional[bool] = None
    ignored_channels: List[str] = field(default_factory=list)
    allowed_channels: List[str] = field(default_factory=list)
    no_thread_channels: List[str] = field(default_factory=list)
    history_backfill: Optional[bool] = None
    history_backfill_limit: Optional[int] = None
    reply_to_mode: Optional[str] = None  # "off" / "first" / "all"


@dataclass
class TelegramBehaviorConfig:
    require_mention: Optional[bool] = None
    mention_patterns: Optional[str] = None
    exclusive_bot_mentions: Optional[bool] = None
    guest_mode: Optional[bool] = None
    free_response_chats: List[str] = field(default_factory=list)
    allowed_chats: List[str] = field(default_factory=list)
    allowed_topics: List[str] = field(default_factory=list)
    ignored_threads: List[str] = field(default_factory=list)
    reactions: Optional[bool] = None
    proxy: Optional[str] = None
    reply_to_mode: Optional[str] = None
    allowed_users: List[str] = field(default_factory=list)


@dataclass
class BehaviorConfig:
    """All platform behavior configs."""
    slack: SlackBehaviorConfig = field(default_factory=SlackBehaviorConfig)
    discord: DiscordBehaviorConfig = field(default_factory=DiscordBehaviorConfig)
    telegram: TelegramBehaviorConfig = field(default_factory=TelegramBehaviorConfig)

    @classmethod
    def from_yaml(cls, yaml_cfg: dict) -> "BehaviorConfig":
        """Build BehaviorConfig from config.yaml platform sections.

        Reads the same YAML keys that load_gateway_config() bridges to
        env vars, but stores them in structured objects instead.
        """
        bc = cls()

        # Slack
        slack_cfg = yaml_cfg.get("slack", {})
        if isinstance(slack_cfg, dict):
            bc.slack = SlackBehaviorConfig(
                require_mention=_opt_bool(slack_cfg.get("require_mention")),
                strict_mention=_opt_bool(slack_cfg.get("strict_mention")),
                allow_bots=slack_cfg.get("allow_bots"),
                free_response_channels=_str_to_list(slack_cfg.get("free_response_channels")),
                reactions=_opt_bool(slack_cfg.get("reactions")),
                allowed_channels=_str_to_list(slack_cfg.get("allowed_channels")),
            )

        # Discord
        discord_cfg = yaml_cfg.get("discord", {})
        if isinstance(discord_cfg, dict):
            bc.discord = DiscordBehaviorConfig(
                require_mention=_opt_bool(discord_cfg.get("require_mention")),
                thread_require_mention=_opt_bool(discord_cfg.get("thread_require_mention")),
                allow_bots=discord_cfg.get("allow_bots"),
                free_response_channels=_str_to_list(discord_cfg.get("free_response_channels")),
                auto_thread=_opt_bool(discord_cfg.get("auto_thread")),
                reactions=_opt_bool(discord_cfg.get("reactions")),
                ignored_channels=_str_to_list(discord_cfg.get("ignored_channels")),
                allowed_channels=_str_to_list(discord_cfg.get("allowed_channels")),
                no_thread_channels=_str_to_list(discord_cfg.get("no_thread_channels")),
                history_backfill=_opt_bool(discord_cfg.get("history_backfill")),
                history_backfill_limit=_opt_int(discord_cfg.get("history_backfill_limit")),
                reply_to_mode=_coerce_reply_to_mode(discord_cfg.get("reply_to_mode")),
            )

        # Telegram
        telegram_cfg = yaml_cfg.get("telegram", {})
        if isinstance(telegram_cfg, dict):
            bc.telegram = TelegramBehaviorConfig(
                require_mention=_opt_bool(telegram_cfg.get("require_mention")),
                mention_patterns=telegram_cfg.get("mention_patterns"),
                exclusive_bot_mentions=_opt_bool(telegram_cfg.get("exclusive_bot_mentions")),
                guest_mode=_opt_bool(telegram_cfg.get("guest_mode")),
                free_response_chats=_str_to_list(telegram_cfg.get("free_response_chats")),
                allowed_chats=_str_to_list(telegram_cfg.get("allowed_chats")),
                allowed_topics=_str_to_list(telegram_cfg.get("allowed_topics")),
                ignored_threads=_str_to_list(telegram_cfg.get("ignored_threads")),
                reactions=_opt_bool(telegram_cfg.get("reactions")),
                proxy=telegram_cfg.get("proxy_url"),
                reply_to_mode=_coerce_reply_to_mode(telegram_cfg.get("reply_to_mode")),
                allowed_users=_str_to_list(telegram_cfg.get("allow_from")),
            )

        return bc

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dict suitable for JSON persistence."""
        return {
            "slack": _dataclass_to_dict(self.slack),
            "discord": _dataclass_to_dict(self.discord),
            "telegram": _dataclass_to_dict(self.telegram),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BehaviorConfig":
        """Deserialize from a dict produced by ``to_dict()``."""
        bc = cls()
        slack_data = data.get("slack", {})
        if isinstance(slack_data, dict):
            bc.slack = SlackBehaviorConfig(
                require_mention=slack_data.get("require_mention"),
                strict_mention=slack_data.get("strict_mention"),
                allow_bots=slack_data.get("allow_bots"),
                free_response_channels=list(slack_data.get("free_response_channels", [])),
                reactions=slack_data.get("reactions"),
                allowed_channels=list(slack_data.get("allowed_channels", [])),
            )
        discord_data = data.get("discord", {})
        if isinstance(discord_data, dict):
            bc.discord = DiscordBehaviorConfig(
                require_mention=discord_data.get("require_mention"),
                thread_require_mention=discord_data.get("thread_require_mention"),
                allow_bots=discord_data.get("allow_bots"),
                free_response_channels=list(discord_data.get("free_response_channels", [])),
                auto_thread=discord_data.get("auto_thread"),
                reactions=discord_data.get("reactions"),
                ignored_channels=list(discord_data.get("ignored_channels", [])),
                allowed_channels=list(discord_data.get("allowed_channels", [])),
                no_thread_channels=list(discord_data.get("no_thread_channels", [])),
                history_backfill=discord_data.get("history_backfill"),
                history_backfill_limit=discord_data.get("history_backfill_limit"),
                reply_to_mode=discord_data.get("reply_to_mode"),
            )
        telegram_data = data.get("telegram", {})
        if isinstance(telegram_data, dict):
            bc.telegram = TelegramBehaviorConfig(
                require_mention=telegram_data.get("require_mention"),
                mention_patterns=telegram_data.get("mention_patterns"),
                exclusive_bot_mentions=telegram_data.get("exclusive_bot_mentions"),
                guest_mode=telegram_data.get("guest_mode"),
                free_response_chats=list(telegram_data.get("free_response_chats", [])),
                allowed_chats=list(telegram_data.get("allowed_chats", [])),
                allowed_topics=list(telegram_data.get("allowed_topics", [])),
                ignored_threads=list(telegram_data.get("ignored_threads", [])),
                reactions=telegram_data.get("reactions"),
                proxy=telegram_data.get("proxy"),
                reply_to_mode=telegram_data.get("reply_to_mode"),
                allowed_users=list(telegram_data.get("allowed_users", [])),
            )
        return bc


def _opt_bool(v) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v).lower() in {"true", "1", "yes"}


def _opt_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _str_to_list(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return []


def _coerce_reply_to_mode(v) -> Optional[str]:
    """YAML 1.1 parses bare 'off' as boolean False — coerce to string 'off'."""
    if v is None:
        return None
    if v is False:
        return "off"
    if isinstance(v, str):
        lowered = v.strip().lower()
        if lowered in {"off", "first", "all"}:
            return lowered
        return lowered
    return str(v).lower()


def _dataclass_to_dict(dc) -> Dict[str, Any]:
    """Convert a BehaviorConfig sub-dataclass to a plain dict."""
    result = {}
    for f in dc.__dataclass_fields__:
        result[f] = getattr(dc, f)
    return result
