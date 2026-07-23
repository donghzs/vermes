"""
#1 边界修复测试 — plan 检测软强约束（system prompt 注入 todo 维护提醒）

验证：
1. 缺失提醒时追加
2. 已存在提醒时不重复追加（幂等）
3. 空 prompt 也能生成
"""
from agent.conversation_loop import _with_planning_reminder, _PLANNING_REMINDER


def test_appends_when_absent():
    sp = "You are a helpful agent."
    out = _with_planning_reminder(sp)
    assert _PLANNING_REMINDER.strip() in out
    assert out.startswith("You are a helpful agent.")


def test_idempotent_no_double_append():
    sp = "You are a helpful agent."
    once = _with_planning_reminder(sp)
    twice = _with_planning_reminder(once)
    assert once == twice
    assert twice.count(_PLANNING_REMINDER.strip()) == 1


def test_empty_prompt():
    out = _with_planning_reminder("")
    assert _PLANNING_REMINDER.strip() in out
