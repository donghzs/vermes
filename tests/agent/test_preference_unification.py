"""② 偏好词表共享常量：消除三处不一致（l1_extractor / memory_fabric / session_handoff）。

核心回归：此前 l1_extractor 抽到 "我更喜欢 Python" → 写 lifecycle_tag='preference'，
但 memory_fabric._infer_lifecycle_tag 词表更窄（无 "更喜欢"）→ 推断成 'reference'。
统一到 _preference_keywords 后，推断应与抽取一致。

语言覆盖纪律：中文断言具体值（"更喜欢"→preference），不只用非空断言。
"""

import sys
from pathlib import Path

import pytest

# 让 agent 包可导入（与项目测试约定一致）
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent._preference_keywords import (  # noqa: E402
    ZH_PREFERENCE_TRIGGERS,
    EN_PREFERENCE_TRIGGERS,
)
from agent import memory_fabric  # noqa: E402
from agent import l1_extractor  # noqa: E402
from agent import session_handoff  # noqa: E402


def test_shared_constant_is_superset_and_nonempty():
    # 共享词表必须覆盖原本 l1_extractor 的抽取口径（含"更喜欢"等）
    assert "更喜欢" in ZH_PREFERENCE_TRIGGERS
    assert "偏好" in ZH_PREFERENCE_TRIGGERS
    assert "prefer" in EN_PREFERENCE_TRIGGERS
    assert len(ZH_PREFERENCE_TRIGGERS) >= 14
    assert len(EN_PREFERENCE_TRIGGERS) >= 9


def test_infer_recognizes_更喜欢_as_preference():
    # 关键回归：原本"更喜欢"不在 fabric 词表→推断成 reference；现在应推断 preference
    assert (
        memory_fabric._infer_lifecycle_tag({"fts_content": "用户说 我更喜欢用 Python 写后端"})
        == "preference"
    )
    # 阴性：普通知识不含偏好词 → 默认 reference
    assert (
        memory_fabric._infer_lifecycle_tag({"fts_content": "Postgres 是关系型数据库"})
        == "reference"
    )


def test_infer_recognizes_english_preference():
    assert (
        memory_fabric._infer_lifecycle_tag({"fts_content": "i prefer dark mode for terminals"})
        == "preference"
    )


def test_l1_extractor_uses_shared_keywords():
    facts = l1_extractor.extract_facts("我更喜欢用 Python 写后端，不喜欢 Java")
    prefs = [f for f in facts if f.kind == "preference"]
    assert prefs, "应抽到一条偏好事实"
    assert any("Python" in f.value for f in prefs), "中文偏好抽取应保留具体对象"


def test_session_handoff_captures_preference():
    # session_handoff 此前 0 偏好覆盖；现在应捕获用户偏好
    msgs = [
        {"role": "user", "content": "我更喜欢用 Python 写后端，不喜欢 Java"},
        {"role": "assistant", "content": "了解，已记下你的偏好。"},
    ]
    prefs = session_handoff._extract_preferences(msgs)
    assert prefs, "session_handoff 应捕获用户偏好"
    assert any("更喜欢" in p for p in prefs)
