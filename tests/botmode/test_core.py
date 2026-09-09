"""③ Bot Mode · core.py 纯函数测试（2026-09-09 补覆盖）。

core.py 是神魔堂 botmode 的底层 helper（房间 @mention 解析 / session key 派生 /
room_uid 归一），被 T4 后端路由与 T5 异构接线直接调用，此前 **0 直接测试覆盖**。

覆盖三件套：
- _session_key_for_room：命名空间隔离语义 + 后缀包含 profile 的缓存安全前提
- parse_room_mentions：空输入/无提及/按 id/按 name/大小写不敏感/去重保序/未匹配跳过/CJK/对象型 profile/None 不炸
- RoomIdNormalizer.normalize：空 raw/直通/P1 信道无关

可独立运行：python tests/botmode/test_core.py
"""

import sys

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

import pytest

from vermes_cli.botmode import core as bc


# ─────────────────────────── _session_key_for_room ───────────────────────────


class TestSessionKeyForRoom:
    """房间内某 agent 的 session key 派生。"""

    def test_format_is_room_namespaced(self):
        # 必须带 room: 前缀，与单聊 session 隔离（plan §9 风险缓解）
        assert bc._session_key_for_room("r1", "a1") == "room:r1:agent:a1"

    def test_distinct_agents_distinct_keys(self):
        k_coder = bc._session_key_for_room("r", "coder")
        k_decoder = bc._session_key_for_room("r", "decoder")
        assert k_coder != k_decoder

    def test_full_key_safe_for_endswith_cache_eviction(self):
        # 缓存淘汰用 endswith(f":{session_id}")（plan §1 G4）。
        # 后缀包含关系（decoder⊃coder）下，仅当 session_id 传「完整房间 key」
        # 才不会误命中彼此。验证：完整 key 末尾的 :{session_id} 段互不包含。
        k_coder = bc._session_key_for_room("r", "coder")
        k_decoder = bc._session_key_for_room("r", "decoder")
        assert not k_decoder.endswith(":coder")  # 误传 profile_id 才会触发这条
        assert k_coder.endswith(":coder")        # 传完整 key 时精准命中自身
        assert k_decoder.endswith(":decoder")


# ─────────────────────────── parse_room_mentions ───────────────────────────


class _ObjProfile:
    """模拟非 dict 形态的 profile（含 .id / .name 属性）。"""

    def __init__(self, pid, name):
        self.id = pid
        self.name = name


class TestParseRoomMentions:
    """房间消息 @mention 解析 → 匹配的 agent_profile_id 列表。"""

    def test_empty_text_returns_empty(self):
        assert bc.parse_room_mentions("", [{"id": "a1"}]) == []

    def test_no_mention_returns_empty(self):
        assert bc.parse_room_mentions("大家好，开工了", [{"id": "a1", "name": "甲"}]) == []

    def test_match_by_id(self):
        profiles = [{"id": "a1", "name": "甲"}]
        assert bc.parse_room_mentions("请 @a1 处理", profiles) == ["a1"]

    def test_match_by_name(self):
        profiles = [{"id": "a1", "name": "甲"}]
        assert bc.parse_room_mentions("请 @甲 处理", profiles) == ["a1"]

    def test_case_insensitive(self):
        profiles = [{"id": "AgentX", "name": "叉"}]
        assert bc.parse_room_mentions("@agentx 来", profiles) == ["AgentX"]

    def test_dedup_preserves_first_seen_order(self):
        profiles = [{"id": "a1"}, {"id": "a2"}]
        assert bc.parse_room_mentions("@a1 @a2 @a1", profiles) == ["a1", "a2"]

    def test_unmatched_token_skipped(self):
        # 文本里有 @ghost 但 profile 表里没有 → 不算匹配、不报错
        assert bc.parse_room_mentions("@ghost 你在吗", [{"id": "a1"}]) == []

    def test_cjk_name_match(self):
        profiles = [{"id": "a1", "name": "小明"}]
        assert bc.parse_room_mentions("拜托 @小明 看看", profiles) == ["a1"]

    def test_object_profile_not_dict(self):
        profiles = [_ObjProfile("a1", "甲")]
        assert bc.parse_room_mentions("@甲 处理下", profiles) == ["a1"]

    def test_profiles_none_does_not_crash(self):
        # profiles=None 时找不到任何匹配，但绝不能抛异常
        assert bc.parse_room_mentions("@a1", None) == []

    def test_mixed_mentions_and_text(self):
        profiles = [{"id": "a1", "name": "甲"}, {"id": "a2", "name": "乙"}]
        text = "甲你 @a1 乙你 @乙 一起上"
        assert bc.parse_room_mentions(text, profiles) == ["a1", "a2"]

    def test_token_with_dot_and_hyphen(self):
        # ROOM_MENTION_RE 允许 . 与 -：@code-1.2 这类 handle
        profiles = [{"id": "code-1.2"}]
        assert bc.parse_room_mentions("@code-1.2 跑", profiles) == ["code-1.2"]


# ─────────────────────────── RoomIdNormalizer ───────────────────────────


class TestRoomIdNormalizer:
    """room_uid 归一（P1 直通）。"""

    def test_empty_raw_returns_empty(self):
        assert bc.RoomIdNormalizer.normalize("desktop", "") == ""

    def test_passthrough_desktop(self):
        assert bc.RoomIdNormalizer.normalize("desktop", "room-123") == "room-123"

    def test_channel_agnostic_in_p1(self):
        # P1 不区分信道，飞书/钉钉/企微 一律直通返回 raw
        assert bc.RoomIdNormalizer.normalize("feishu", "x9") == "x9"
        assert bc.RoomIdNormalizer.normalize("telegram", "g-7") == "g-7"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    import types

    # 极简独立运行入口：逐类实例化跑方法
    classes = [v for v in globals().values()
               if isinstance(v, type) and v.__name__.startswith("Test")]
    fails = 0
    for cls in classes:
        inst = cls()
        for name in dir(inst):
            if name.startswith("test_"):
                try:
                    getattr(inst, name)()
                except Exception as e:  # noqa: BLE001
                    fails += 1
                    print(f"FAIL {cls.__name__}.{name}: {e}")
    if fails == 0:
        print(f"OK: 全部 {sum(len([n for n in dir(c) if n.startswith('test_')]) for c in classes)} 测试通过")
    else:
        raise SystemExit(f"{fails} 个测试失败")
