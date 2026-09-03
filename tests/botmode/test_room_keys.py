"""T3 helper 单元测试（③ Bot Mode P1）。

覆盖：session key 格式与命名空间隔离、@mention 解析（基础 / 大小写不敏感 /
去重保序 / 无匹配 / 英文 handle）、RoomIdNormalizer 直通语义。
"""

import sys
from pathlib import Path

# 让仓库根可导入（pytest 根目录未必在 sys.path）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.botmode import (
    RoomIdNormalizer,
    _session_key_for_room,
    parse_room_mentions,
)

PROFILES = [
    {"id": "researcher", "name": "研究助手"},
    {"id": "coder", "name": "编码助手"},
]


def test_session_key_format_and_namespace():
    sk = _session_key_for_room("r1", "researcher")
    assert sk == "room:r1:agent:researcher"
    # 命名空间隔离：与单聊 key 不同前缀，且以 :agent_profile_id 结尾（G4 兼容）。
    assert sk.startswith("room:")
    assert sk.endswith(":researcher")


def test_parse_mentions_basic():
    assert parse_room_mentions("@研究助手 帮我查 X", PROFILES) == ["researcher"]


def test_parse_mentions_case_insensitive_by_name_and_id():
    # 大小写不敏感匹配 name
    assert parse_room_mentions("@研究助手", PROFILES) == ["researcher"]
    # 大小写不敏感匹配 id
    assert parse_room_mentions("@CODER", PROFILES) == ["coder"]


def test_parse_mentions_dedup_and_order():
    out = parse_room_mentions("@编码助手 @研究助手 @编码助手", PROFILES)
    assert out == ["coder", "researcher"]


def test_parse_mentions_no_match():
    assert parse_room_mentions("没有提到任何人", PROFILES) == []
    assert parse_room_mentions("", PROFILES) == []
    assert parse_room_mentions(None, PROFILES) == []
    assert parse_room_mentions("@不存在的人", PROFILES) == []


def test_parse_mentions_english_handle():
    profs = [{"id": "research_bot", "name": "ResearchBot"}]
    assert parse_room_mentions("@ResearchBot hi", profs) == ["research_bot"]


def test_room_id_normalizer_passthrough():
    assert RoomIdNormalizer.normalize("desktop", "room-123") == "room-123"
    assert RoomIdNormalizer.normalize("desktop", "") == ""
    # P1：其他信道同样直通（Phase 2 才分支映射逻辑）。
    assert RoomIdNormalizer.normalize("telegram", "g-9") == "g-9"
