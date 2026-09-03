"""③ Bot Mode P1 模块包。

集中放置房间 / 会话密钥 / @mention 解析等纯函数 helper，
供 T4（后端路由）与 T5（异构接线）调用，并预留 Phase 2（跨渠道群聊 / ⑭ Bot 实验室）扩展点。

落点决策（审计纪律）：计划 T3 原拟放 `gateway/session_mixin.py` 或本独立包二选一。
最终选独立包，原因——gateway 的 `_session_key_for_source` 是 **实例方法**
（依赖 `self.session_store` / `self.config`），Web 侧 `vermes_cli/blueprints/chat.py`
调不到；纯函数 + 独立包既不污染 gateway 链路，又能被 Web / TUI / Phase 2 复用。
"""

from vermes_cli.botmode.core import (
    ROOM_MENTION_RE,
    RoomIdNormalizer,
    _session_key_for_room,
    parse_room_mentions,
)

__all__ = [
    "ROOM_MENTION_RE",
    "RoomIdNormalizer",
    "_session_key_for_room",
    "parse_room_mentions",
]
