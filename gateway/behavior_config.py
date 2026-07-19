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
class WhatsAppBehaviorConfig:
    require_mention: Optional[bool] = None
    mention_patterns: Optional[str] = None
    free_response_chats: List[str] = field(default_factory=list)
    dm_policy: Optional[str] = None  # "open" / "allowlist" / "disabled"
    group_policy: Optional[str] = None  # "open" / "allowlist" / "disabled"
    mode: Optional[str] = None  # "self-chat" / other modes
    reply_prefix: Optional[str] = None
    allowed_users: List[str] = field(default_factory=list)
    group_allowed_users: List[str] = field(default_factory=list)


@dataclass
class SignalBehaviorConfig:
    require_mention: Optional[bool] = None
    reactions: Optional[bool] = None
    allowed_users: List[str] = field(default_factory=list)
    group_allowed_users: List[str] = field(default_factory=list)


@dataclass
class DingTalkBehaviorConfig:
    require_mention: Optional[bool] = None
    mention_patterns: Optional[str] = None
    free_response_chats: List[str] = field(default_factory=list)
    allowed_chats: List[str] = field(default_factory=list)
    allowed_users: List[str] = field(default_factory=list)


@dataclass
class MattermostBehaviorConfig:
    require_mention: Optional[bool] = None
    reply_mode: Optional[str] = None  # "off" / "thread"
    free_response_channels: List[str] = field(default_factory=list)
    allowed_channels: List[str] = field(default_factory=list)


@dataclass
class MatrixBehaviorConfig:
    require_mention: Optional[bool] = None
    thread_require_mention: Optional[bool] = None
    free_response_rooms: List[str] = field(default_factory=list)
    allowed_rooms: List[str] = field(default_factory=list)
    auto_thread: Optional[bool] = None
    dm_auto_thread: Optional[bool] = None
    dm_mention_threads: Optional[bool] = None
    reactions: Optional[bool] = None
    allowed_users: List[str] = field(default_factory=list)


@dataclass
class FeishuBehaviorConfig:
    allow_bots: Optional[str] = None  # "none" / "mentions" / "all"
    group_policy: Optional[str] = None  # "allowlist" / other
    allowed_users: List[str] = field(default_factory=list)
    require_mention: Optional[bool] = None
    reactions: Optional[bool] = None


@dataclass
class BehaviorConfig:
    """All platform behavior configs."""
    slack: SlackBehaviorConfig = field(default_factory=SlackBehaviorConfig)
    discord: DiscordBehaviorConfig = field(default_factory=DiscordBehaviorConfig)
    telegram: TelegramBehaviorConfig = field(default_factory=TelegramBehaviorConfig)
    whatsapp: WhatsAppBehaviorConfig = field(default_factory=WhatsAppBehaviorConfig)
    signal: SignalBehaviorConfig = field(default_factory=SignalBehaviorConfig)
    dingtalk: DingTalkBehaviorConfig = field(default_factory=DingTalkBehaviorConfig)
    mattermost: MattermostBehaviorConfig = field(default_factory=MattermostBehaviorConfig)
    matrix: MatrixBehaviorConfig = field(default_factory=MatrixBehaviorConfig)
    feishu: FeishuBehaviorConfig = field(default_factory=FeishuBehaviorConfig)

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

        # WhatsApp
        whatsapp_cfg = yaml_cfg.get("whatsapp", {})
        if isinstance(whatsapp_cfg, dict):
            bc.whatsapp = WhatsAppBehaviorConfig(
                require_mention=_opt_bool(whatsapp_cfg.get("require_mention")),
                mention_patterns=whatsapp_cfg.get("mention_patterns"),
                free_response_chats=_str_to_list(whatsapp_cfg.get("free_response_chats")),
                dm_policy=whatsapp_cfg.get("dm_policy"),
                group_policy=whatsapp_cfg.get("group_policy"),
                mode=whatsapp_cfg.get("mode"),
                reply_prefix=whatsapp_cfg.get("reply_prefix"),
                allowed_users=_str_to_list(whatsapp_cfg.get("allow_from")),
                group_allowed_users=_str_to_list(whatsapp_cfg.get("group_allow_from")),
            )

        # Signal
        signal_cfg = yaml_cfg.get("signal", {})
        if isinstance(signal_cfg, dict):
            bc.signal = SignalBehaviorConfig(
                require_mention=_opt_bool(signal_cfg.get("require_mention")),
                reactions=_opt_bool(signal_cfg.get("reactions")),
                allowed_users=_str_to_list(signal_cfg.get("allow_from")),
                group_allowed_users=_str_to_list(signal_cfg.get("group_allow_from")),
            )

        # DingTalk
        dingtalk_cfg = yaml_cfg.get("dingtalk", {})
        if isinstance(dingtalk_cfg, dict):
            bc.dingtalk = DingTalkBehaviorConfig(
                require_mention=_opt_bool(dingtalk_cfg.get("require_mention")),
                mention_patterns=dingtalk_cfg.get("mention_patterns"),
                free_response_chats=_str_to_list(dingtalk_cfg.get("free_response_chats")),
                allowed_chats=_str_to_list(dingtalk_cfg.get("allowed_chats")),
                allowed_users=_str_to_list(dingtalk_cfg.get("allowed_users")),
            )

        # Mattermost
        mattermost_cfg = yaml_cfg.get("mattermost", {})
        if isinstance(mattermost_cfg, dict):
            bc.mattermost = MattermostBehaviorConfig(
                require_mention=_opt_bool(mattermost_cfg.get("require_mention")),
                reply_mode=_coerce_reply_to_mode(mattermost_cfg.get("reply_mode")),
                free_response_channels=_str_to_list(mattermost_cfg.get("free_response_channels")),
                allowed_channels=_str_to_list(mattermost_cfg.get("allowed_channels")),
            )

        # Matrix
        matrix_cfg = yaml_cfg.get("matrix", {})
        if isinstance(matrix_cfg, dict):
            bc.matrix = MatrixBehaviorConfig(
                require_mention=_opt_bool(matrix_cfg.get("require_mention")),
                thread_require_mention=_opt_bool(matrix_cfg.get("thread_require_mention")),
                free_response_rooms=_str_to_list(matrix_cfg.get("free_response_rooms")),
                allowed_rooms=_str_to_list(matrix_cfg.get("allowed_rooms")),
                auto_thread=_opt_bool(matrix_cfg.get("auto_thread")),
                dm_auto_thread=_opt_bool(matrix_cfg.get("dm_auto_thread")),
                dm_mention_threads=_opt_bool(matrix_cfg.get("dm_mention_threads")),
                reactions=_opt_bool(matrix_cfg.get("reactions")),
                allowed_users=_str_to_list(matrix_cfg.get("allow_from")),
            )

        # Feishu
        feishu_cfg = yaml_cfg.get("feishu", {})
        if isinstance(feishu_cfg, dict):
            bc.feishu = FeishuBehaviorConfig(
                allow_bots=feishu_cfg.get("allow_bots"),
                group_policy=feishu_cfg.get("group_policy"),
                allowed_users=_str_to_list(feishu_cfg.get("allow_from")),
                require_mention=_opt_bool(feishu_cfg.get("require_mention")),
                reactions=_opt_bool(feishu_cfg.get("reactions")),
            )

        return bc

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dict suitable for JSON persistence."""
        return {
            "slack": _dataclass_to_dict(self.slack),
            "discord": _dataclass_to_dict(self.discord),
            "telegram": _dataclass_to_dict(self.telegram),
            "whatsapp": _dataclass_to_dict(self.whatsapp),
            "signal": _dataclass_to_dict(self.signal),
            "dingtalk": _dataclass_to_dict(self.dingtalk),
            "mattermost": _dataclass_to_dict(self.mattermost),
            "matrix": _dataclass_to_dict(self.matrix),
            "feishu": _dataclass_to_dict(self.feishu),
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
        whatsapp_data = data.get("whatsapp", {})
        if isinstance(whatsapp_data, dict):
            bc.whatsapp = WhatsAppBehaviorConfig(
                require_mention=whatsapp_data.get("require_mention"),
                mention_patterns=whatsapp_data.get("mention_patterns"),
                free_response_chats=list(whatsapp_data.get("free_response_chats", [])),
                dm_policy=whatsapp_data.get("dm_policy"),
                group_policy=whatsapp_data.get("group_policy"),
                mode=whatsapp_data.get("mode"),
                reply_prefix=whatsapp_data.get("reply_prefix"),
                allowed_users=list(whatsapp_data.get("allowed_users", [])),
                group_allowed_users=list(whatsapp_data.get("group_allowed_users", [])),
            )
        signal_data = data.get("signal", {})
        if isinstance(signal_data, dict):
            bc.signal = SignalBehaviorConfig(
                require_mention=signal_data.get("require_mention"),
                reactions=signal_data.get("reactions"),
                allowed_users=list(signal_data.get("allowed_users", [])),
                group_allowed_users=list(signal_data.get("group_allowed_users", [])),
            )
        dingtalk_data = data.get("dingtalk", {})
        if isinstance(dingtalk_data, dict):
            bc.dingtalk = DingTalkBehaviorConfig(
                require_mention=dingtalk_data.get("require_mention"),
                mention_patterns=dingtalk_data.get("mention_patterns"),
                free_response_chats=list(dingtalk_data.get("free_response_chats", [])),
                allowed_chats=list(dingtalk_data.get("allowed_chats", [])),
                allowed_users=list(dingtalk_data.get("allowed_users", [])),
            )
        mattermost_data = data.get("mattermost", {})
        if isinstance(mattermost_data, dict):
            bc.mattermost = MattermostBehaviorConfig(
                require_mention=mattermost_data.get("require_mention"),
                reply_mode=mattermost_data.get("reply_mode"),
                free_response_channels=list(mattermost_data.get("free_response_channels", [])),
                allowed_channels=list(mattermost_data.get("allowed_channels", [])),
            )
        matrix_data = data.get("matrix", {})
        if isinstance(matrix_data, dict):
            bc.matrix = MatrixBehaviorConfig(
                require_mention=matrix_data.get("require_mention"),
                thread_require_mention=matrix_data.get("thread_require_mention"),
                free_response_rooms=list(matrix_data.get("free_response_rooms", [])),
                allowed_rooms=list(matrix_data.get("allowed_rooms", [])),
                auto_thread=matrix_data.get("auto_thread"),
                dm_auto_thread=matrix_data.get("dm_auto_thread"),
                dm_mention_threads=matrix_data.get("dm_mention_threads"),
                reactions=matrix_data.get("reactions"),
                allowed_users=list(matrix_data.get("allowed_users", [])),
            )
        feishu_data = data.get("feishu", {})
        if isinstance(feishu_data, dict):
            bc.feishu = FeishuBehaviorConfig(
                allow_bots=feishu_data.get("allow_bots"),
                group_policy=feishu_data.get("group_policy"),
                allowed_users=list(feishu_data.get("allowed_users", [])),
                require_mention=feishu_data.get("require_mention"),
                reactions=feishu_data.get("reactions"),
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
