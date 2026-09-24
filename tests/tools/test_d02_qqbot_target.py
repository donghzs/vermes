"""D02 哨兵：QQBot 32 位 openid / 数字群号识别为显式目标（回归哨兵）。

对应 35532f29fb57 fix(messaging)。实现已在、缺护栏 —— 本文件补断言。
"""

from tools.send_message_tool import _parse_target_ref


def test_qqbot_32char_openid_is_explicit_target():
    """32 位大写 hex openid 须解析为显式目标，不得被当普通文本。"""
    openid = "A1B2C3D4E5F60718293A4B5C6D7E8F90"
    chat_id, thread_id, explicit = _parse_target_ref("qqbot", openid)
    assert chat_id == openid
    assert thread_id is None
    assert explicit is True


def test_qqbot_numeric_group_id_is_explicit_target():
    """纯数字群号/guild id 须解析为显式目标。"""
    chat_id, thread_id, explicit = _parse_target_ref("qqbot", "123456789")
    assert chat_id == "123456789"
    assert thread_id is None
    assert explicit is True


def test_qqbot_plain_name_not_explicit():
    """人类可读名不得被当成显式 ID（须走目录解析）。"""
    chat_id, _thread, explicit = _parse_target_ref("qqbot", "我的群")
    assert explicit is False
