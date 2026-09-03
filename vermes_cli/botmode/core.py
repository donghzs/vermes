"""③ Bot Mode P1 · 房间与会话密钥 helper（T3）。

纯函数 / 薄壳，零外部业务依赖，供 T4（后端路由）与 T5（异构接线）调用。

与 ① A2A 的关系：房间内 @mention 解析正则**字面值对齐**
`agent/a2a/types.py:_HANDLE_RE`，保持协议层工程语言一致，避免两套正则语义漂移
（⑫ / ⑭ 跨渠道群聊将复用同一套 handle 解析）。
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

# 与 agent/a2a/types.py:_HANDLE_RE 字面值一致：
# @<name>（英文/数字/下划线/连字符/点）或 @中文名（CJK 基本区 + 点/连字符）。
ROOM_MENTION_RE = re.compile(r"@([\w\u4e00-\u9fff][\w\u4e00-\u9fff.-]*)")


def _session_key_for_room(room_id: str, agent_profile_id: str) -> str:
    """派生房间内某 agent 的 session key。

    命名空间前缀 ``room:`` 与单聊 session 隔离（plan §9 风险缓解：
    房间消息不会误触发人类私聊 agent）。

    plan 原拟"建于 ``_session_key_for_source`` 之上"——但该方法是 gateway 层
    实例方法，Web 侧不可达；此处以纯字符串格式实现同等的命名空间隔离语义。

    同时该 key **以 ``:{agent_profile_id}`` 结尾**，使
    ``agent_cache.pop_for_session`` 的 ``endswith(f":{session_id}")`` 匹配天然成立
    （plan §1 G4 兼容性，房间派生 session 同受缓存淘汰）。
    """
    return f"room:{room_id}:agent:{agent_profile_id}"


def parse_room_mentions(text: str, profiles: Optional[Iterable[dict]]) -> List[str]:
    """从房间消息文本解析 @mention，返回匹配的 agent_profile_id 列表。

    规则：
      - 用 ``ROOM_MENTION_RE`` 提取 @token（对齐 ① A2A 协议层正则）。
      - 大小写不敏感匹配 profile 的 ``id`` 或 ``name``（人读别名）。
      - 去重、保序（按文本中首次出现顺序）。
      - 无匹配 / 空文本 → 返回 ``[]``（调用方据此 fallback 到 default agent）。

    ``profiles`` 形态：T1 ``SessionDB.list_agent_profiles()`` 返回的行字典列表
    （含 ``id`` / ``name`` 键）。也兼容任意含 ``id`` / ``name`` 属性的对象。
    """
    if not text:
        return []
    tokens = ROOM_MENTION_RE.findall(text)
    if not tokens:
        return []

    by_lower: Dict[str, str] = {}
    for p in profiles or []:
        if isinstance(p, dict):
            pid = p.get("id")
            name = p.get("name") or ""
        else:
            pid = getattr(p, "id", None)
            name = getattr(p, "name", "") or ""
        if not pid:
            continue
        by_lower[str(pid).lower()] = str(pid)
        if name:
            by_lower[str(name).lower()] = str(pid)

    matched: List[str] = []
    seen = set()
    for tok in tokens:
        pid = by_lower.get(tok.lower())
        if pid and pid not in seen:
            seen.add(pid)
            matched.append(pid)
    return matched


class RoomIdNormalizer:
    """⑫ RoomIdNormalizer 薄壳（Phase 1）。

    Phase 1 仅支持 ``desktop`` 信道直通：``room_uid == raw_room_id``。
    跨渠道映射（``channel::room_id`` → ``room_uid`` 的持久化 / TTL 解析，
    以及飞书 / 钉钉 / 企微 / Telegram 的方言 room 归一）留 Phase 2（⑫ 扩展点）。

    审计纪律（plan §3 T3）：调用 ``_session_key_for_room`` 前**先过 normalizer**，
    避免同一物理群因渠道差异被识别为多个房间。
    """

    @staticmethod
    def normalize(channel: str, raw_room_id: str) -> str:
        """归一 raw_room_id 为 room_uid。

        P1：所有信道一律直通（返回 raw_room_id）。Phase 2 在此分支
        不同 channel 的映射逻辑（如 ``(channel, raw) -> 持久化 room_uid``）。
        """
        if not raw_room_id:
            return ""
        # P1 直通：不区分信道。
        return raw_room_id
